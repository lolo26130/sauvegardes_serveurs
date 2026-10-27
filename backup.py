#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]
# ///

"""
Sauvegarde des cartes SD/eMMC des serveurs SBC (Raspberry Pi, Banana Pi, Orange Pi).
Chaque sauvegarde est un fichier .img.gz récupéré par dd via SSH.
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml

from containers import docker_status_server
from server import backup_server, check_server, check_support, speed_test_server, start_backup_tmux, tmux_status
from storage import check_nas, cleanup_old_backups, list_backups, speed_test_nas


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    log_file = Path(__file__).parent / "logs" / "backup.log"
    log_file.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file),
        ],
    )


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Génère un tableau Markdown avec colonnes alignées sur la valeur la plus large."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    header = "| " + " | ".join(f"{h:<{w}}" for h, w in zip(headers, widths)) + " |\n"
    sep    = "| " + " | ".join("-" * w        for w in widths)               + " |\n"
    lines  = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(f"{c:<{w}}" for c, w in zip(row, widths)) + " |\n")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sauvegarde des cartes SD/eMMC via SSH+dd",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  make backup                              # Sauvegarder tous les serveurs
  make tmux SERVER=bananapi-m5_docker-vaultwarden-medias_emmc-systeme           # Sauvegarde tmux un seul serveur
  make check                              # Vérifier la connectivité SSH
  make status                             # État des sessions tmux
  make list                               # Lister les sauvegardes existantes
  uv run backup.py --dry-run              # Simuler sans rien écrire
        """,
    )
    parser.add_argument("--config", default="config.yml", help="Fichier de configuration")
    parser.add_argument("--server", metavar="NOM_OU_IP", help="Ne traiter qu'un seul serveur")
    parser.add_argument("--check", action="store_true", help="Vérifier la connectivité SSH uniquement")
    parser.add_argument("--list", action="store_true", help="Lister les sauvegardes existantes")
    parser.add_argument("--speed-test", action="store_true", help="Mesurer débit SSH + NAS et estimer la durée")
    parser.add_argument("--tmux", action="store_true", help="Lancer la sauvegarde dans tmux sur le Pi (résistant aux coupures PC)")
    parser.add_argument("--status", action="store_true", help="État des sessions tmux de sauvegarde en cours")
    parser.add_argument("--docker", action="store_true", help="Lister les containers Docker actifs sur chaque serveur")
    parser.add_argument("--dry-run", action="store_true", help="Simuler sans écrire")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    config = load_config(args.config)
    nas_path = Path(config["nas"]["path"])
    keep = config["retention"]["keep"]
    servers = config["servers"]

    if args.server:
        servers = [s for s in servers if s["name"] == args.server or s["ip"] == args.server]
        if not servers:
            log.error(f"Serveur '{args.server}' introuvable dans {args.config}")
            return 1

    if args.list:
        list_backups(nas_path, servers, config.get("externes", []))
        return 0

    if args.check:
        results = [check_server(s) for s in servers]
        ok = sum(results)
        log.info(f"{ok}/{len(servers)} serveur(s) accessibles")
        return 0 if ok == len(servers) else 1

    if args.speed_test:
        nas_mbs = speed_test_nas(nas_path)
        if nas_mbs:
            log.info(f"NAS écriture : {nas_mbs:.1f} Mo/s")
        print()
        all_ok = True
        for server in servers:
            result = speed_test_server(server)
            if not result:
                all_ok = False
                continue
            bottleneck = min(result["ssh_mbs"], nas_mbs or result["ssh_mbs"])
            # Recalcule l'estimation avec le vrai goulot d'étranglement
            device_bytes = result["device_gb"] * (1024 ** 3)
            est_best  = device_bytes * 0.30 / (bottleneck * 1024 * 1024) / 60
            est_worst = device_bytes * 0.55 / (bottleneck * 1024 * 1024) / 60
            log.info(
                f"[{result['name']}] {result['device']}  "
                f"{result['device_gb']:.1f} GB  |  "
                f"SSH {result['ssh_mbs']:.1f} Mo/s  |  "
                f"durée estimée {est_best:.0f}–{est_worst:.0f} min "
                f"(goulot: {'SSH' if result['ssh_mbs'] <= (nas_mbs or 9999) else 'NAS'})"
            )
        return 0 if all_ok else 1

    remote_nas = config["nas"].get("remote_path", "")

    if args.status:
        if not remote_nas:
            log.error("'nas.remote_path' manquant dans config.yml (requis pour --status)")
            return 1

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        status_file = nas_path / f"status_{timestamp}.md"
        lines: list[str] = [
            f"# Statut sauvegardes — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        ]

        for server in servers:
            s = tmux_status(server, remote_nas)
            # ordre : occupé, puis en cours (prime sur un .done), puis terminé
            if s.get("busy"):
                msg = f"Occupé  {s['latest']}"
                icon = "⏳"
            elif s["running"]:
                msg = f"En cours…  {s['latest']}"
                icon = "🔄"
            elif s["done"]:
                verdict = "OK" if s["exit_code"] == 0 else f"ERREUR (code {s['exit_code']})"
                msg = f"Terminé — {verdict}  {s['latest']}"
                icon = "✅" if s["exit_code"] == 0 else "❌"
            else:
                msg = "Aucune session active"
                icon = "⏹"
            log.info(f"[{s['name']}] {msg}")
            lines.append(f"- {icon} **{s['name']}** : {msg}\n")

        try:
            status_file.write_text("".join(lines), encoding="utf-8")
            log.info(f"Rapport écrit : {status_file.name}")
        except OSError as e:
            log.warning(f"Impossible d'écrire le rapport sur le NAS : {e}")

        return 0

    if args.docker:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        docker_file = nas_path / f"docker_{timestamp}.md"
        lines: list[str] = [
            f"# Statut Docker — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        ]

        for server in servers:
            result = docker_status_server(server)
            sname = result["name"]
            ip = result["ip"]
            method = result.get("method", "docker")

            if result["installed"] is False:
                log.info(f"[{sname}] ⚠  Docker non détecté")
                lines.append(f"## ⚠ {sname} ({ip})\nDocker non détecté\n\n")

            elif result["error"]:
                log.warning(f"[{sname}] ❌ Erreur : {result['error']}")
                lines.append(f"## ❌ {sname} ({ip})\nErreur : `{result['error']}`\n\n")

            elif not result["containers"]:
                log.info(f"[{sname}] 🐳 Aucun container actif")
                lines.append(f"## 🐳 {sname} ({ip})\nAucun container actif\n\n")

            elif method == "proc":
                nb = len(result["containers"])
                log.info(f"[{sname}] 🔍 {nb} container(s) détecté(s) via /proc")
                lines.append(f"## 🔍 {sname} ({ip}) — {nb} container(s)\n\n")
                lines.append("> Détection via `/proc` (sans accès socket Docker) "
                             "— nom et image non disponibles\n\n")
                rows = [
                    [f"`{c['name']}`", f"`{c['image']}`", c["cpu_pct"],
                     f"{c['mem_used']} / {c['mem_limit']}"]
                    for c in result["containers"]
                ]
                lines.extend(_md_table(["ID container", "Processus", "CPU", "RAM utilisée / limite"], rows))
                # Terminal : largeurs dynamiques
                w_proc = max((len(c["image"]) for c in result["containers"]), default=10)
                w_cpu  = max((len(c["cpu_pct"]) for c in result["containers"]), default=5)
                w_mem  = max((len(f"{c['mem_used']} / {c['mem_limit']}") for c in result["containers"]), default=10)
                for c in result["containers"]:
                    mem_str = f"{c['mem_used']} / {c['mem_limit']}"
                    log.info(
                        f"  {c['name']}  {c['image']:<{w_proc}}  "
                        f"CPU {c['cpu_pct']:<{w_cpu}}  RAM {mem_str:<{w_mem}}"
                    )
                all_ports = result.get("all_ports", [])
                if all_ports:
                    port_str = "  ".join(all_ports)
                    log.info(f"  Ports exposés : {port_str}")
                    lines.append(f"\n**Ports exposés (tous containers) :** `{port_str}`\n")
                lines.append("\n")

            else:
                nb = len(result["containers"])
                log.info(f"[{sname}] 🐳 {nb} container(s) actif(s)")
                lines.append(f"## 🐳 {sname} ({ip}) — {nb} container(s)\n\n")
                lines.append("> NET ↓/↑ : trafic cumulatif depuis le démarrage du container\n\n")
                rows = [
                    [c["name"], c["image"], c["status"], c["cpu_pct"],
                     f"{c['mem_used']} / {c['mem_limit']}", f"{c['net_rx']} / {c['net_tx']}"]
                    for c in result["containers"]
                ]
                lines.extend(_md_table(
                    ["Nom", "Image", "Statut", "CPU", "RAM utilisée / limite", "NET ↓ / ↑"], rows
                ))
                # Terminal : largeurs dynamiques
                w_name = max((len(c["name"])   for c in result["containers"]), default=20)
                w_img  = max((len(c["image"])  for c in result["containers"]), default=30)
                w_st   = max((len(c["status"]) for c in result["containers"]), default=10)
                w_cpu  = max((len(c["cpu_pct"]) for c in result["containers"]), default=5)
                for c in result["containers"]:
                    log.info(
                        f"  {c['name']:<{w_name}}  {c['image']:<{w_img}}  {c['status']:<{w_st}}  "
                        f"CPU {c['cpu_pct']:<{w_cpu}}  "
                        f"RAM {c['mem_used']}/{c['mem_limit']}  "
                        f"NET ↓{c['net_rx']} ↑{c['net_tx']}"
                    )
                lines.append("\n")

        try:
            docker_file.write_text("".join(lines), encoding="utf-8")
            log.info(f"Rapport Docker écrit : {docker_file.name}")
        except OSError as e:
            log.warning(f"Impossible d'écrire le rapport Docker sur le NAS : {e}")

        return 0

    if args.tmux:
        if not remote_nas:
            log.error("'nas.remote_path' manquant dans config.yml (requis pour --tmux)")
            return 1
        success = 0
        for server in servers:
            if not check_support(server):  # garde-fou : disque = type annoncé (emmc / sdcard), sinon sauté
                continue
            session = start_backup_tmux(server, remote_nas, dry_run=args.dry_run)
            if session:
                success += 1
        log.info(f"{success}/{len(servers)} session(s) tmux démarrée(s)")
        log.info("Suivre l'état : make status  (ou : uv run backup.py --status)")
        return 0 if success == len(servers) else 1

    if not check_nas(nas_path):
        if args.dry_run:
            log.warning("NAS inaccessible (dry-run: poursuite quand même)")
        else:
            log.error("Abandon: NAS inaccessible")
            return 1

    if args.dry_run:
        results = [check_server(s) for s in servers]
        log.info(f"SSH: {sum(results)}/{len(servers)} serveur(s) accessibles")

    success = 0
    for server in servers:
        log.info(f"{'='*60}")
        log.info(f"Serveur: {server['name']} ({server['ip']})")
        log.info(f"{'='*60}")
        if not check_support(server):  # garde-fou : disque = type annoncé (emmc / sdcard), sinon sauté
            continue
        try:
            backup_path = backup_server(server, nas_path, dry_run=args.dry_run)
            if backup_path:
                cleanup_old_backups(nas_path / server["name"], keep, dry_run=args.dry_run)
                success += 1
            else:
                log.error(f"Échec sauvegarde: {server['name']}")
        except Exception as e:
            log.error(f"Erreur inattendue pour {server['name']}: {e}", exc_info=args.verbose)

    log.info(f"{'='*60}")
    log.info(f"Résultat: {success}/{len(servers)} sauvegarde(s) réussie(s)")
    return 0 if success == len(servers) else 1


if __name__ == "__main__":
    sys.exit(main())
