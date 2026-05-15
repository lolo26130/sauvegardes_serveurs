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
from pathlib import Path

import yaml

from server import backup_server, check_server, speed_test_server, start_backup_tmux, tmux_status
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sauvegarde des cartes SD/eMMC via SSH+dd",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  make backup                              # Sauvegarder tous les serveurs
  make tmux SERVER=raspberry-pi4           # Sauvegarde tmux un seul serveur
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
        list_backups(nas_path, servers)
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
        for server in servers:
            s = tmux_status(server, remote_nas)
            if s["done"]:
                verdict = "OK" if s["exit_code"] == 0 else f"ERREUR (code {s['exit_code']})"
                log.info(f"[{s['name']}] Terminé — {verdict}  {s['latest']}")
            elif s["running"]:
                log.info(f"[{s['name']}] En cours…  {s['latest']}")
            else:
                log.info(f"[{s['name']}] Aucune session active")
        return 0

    if args.tmux:
        if not remote_nas:
            log.error("'nas.remote_path' manquant dans config.yml (requis pour --tmux)")
            return 1
        success = 0
        for server in servers:
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
