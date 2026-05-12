"""
NAS storage management: pre-flight checks and backup rotation.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def check_nas(nas_path: Path) -> bool:
    """Verify the NAS mount point exists and is writable."""
    if not nas_path.exists():
        log.error(f"NAS non accessible: {nas_path}")
        return False
    try:
        test = nas_path / ".write_test"
        test.touch()
        test.unlink()
        return True
    except OSError as e:
        log.error(f"NAS non accessible en écriture: {e}")
        return False


def cleanup_old_backups(server_dir: Path, keep: int, dry_run=False):
    """Delete oldest backups, keeping only the N most recent per server."""
    backups = sorted(
        server_dir.glob("*.img.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_delete = backups[keep:]
    if not to_delete:
        return

    log.info(f"Rotation: suppression de {len(to_delete)} ancienne(s) sauvegarde(s)")
    for backup in to_delete:
        log.info(f"  Suppression: {backup.name}")
        if not dry_run:
            backup.unlink()


def list_backups(nas_path: Path, servers: list[dict]):
    """Print a summary of existing backups on the NAS."""
    print(f"\nSauvegardes dans {nas_path}:\n")
    for server in servers:
        server_dir = nas_path / server["name"]
        backups = sorted(server_dir.glob("*.img.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            print(f"  {server['name']}: aucune sauvegarde")
            continue
        total_gb = sum(b.stat().st_size for b in backups) / (1024 ** 3)
        print(f"  {server['name']}: {len(backups)} sauvegarde(s) — {total_gb:.1f} GB total")
        for b in backups:
            size_gb = b.stat().st_size / (1024 ** 3)
            from datetime import datetime
            mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"    {mtime}  {b.name}  ({size_gb:.1f} GB)")
    print()
