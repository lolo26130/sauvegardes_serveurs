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

from server import backup_server, check_server
from storage import check_nas, cleanup_old_backups, list_backups


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
  python backup.py                         # Sauvegarder tous les serveurs
  python backup.py --server raspberry-pi4  # Un seul serveur
  python backup.py --check                 # Vérifier la connectivité SSH
  python backup.py --list                  # Lister les sauvegardes existantes
  python backup.py --dry-run               # Simuler sans rien écrire
        """,
    )
    parser.add_argument("--config", default="config.yml", help="Fichier de configuration")
    parser.add_argument("--server", metavar="NOM_OU_IP", help="Ne traiter qu'un seul serveur")
    parser.add_argument("--check", action="store_true", help="Vérifier la connectivité SSH uniquement")
    parser.add_argument("--list", action="store_true", help="Lister les sauvegardes existantes")
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

    if not args.dry_run and not check_nas(nas_path):
        log.error("Abandon: NAS inaccessible")
        return 1

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
