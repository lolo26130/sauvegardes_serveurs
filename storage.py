"""
Gestion du stockage NAS : vérification, rotation et inventaire.

Ce module fournit les utilitaires de gestion du répertoire de destination
sur le NAS : test d'accessibilité en écriture, rotation des anciennes
sauvegardes et affichage de l'inventaire.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def check_nas(nas_path: Path) -> bool:
    """Vérifie que le point de montage NAS existe et est accessible en écriture.

    Tente de créer puis supprimer un fichier temporaire ``.write_test``
    dans le répertoire cible.

    :param nas_path: Chemin du répertoire racine sur le NAS.
    :returns: ``True`` si le NAS est accessible en écriture, ``False`` sinon.
    """
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
    """Supprime les sauvegardes excédentaires en ne conservant que les *N* plus récentes.

    Les fichiers sont triés par date de modification (``mtime``). Les plus
    anciens au-delà du quota ``keep`` sont supprimés définitivement.

    :param server_dir: Répertoire de sauvegardes d'un serveur spécifique.
    :param keep: Nombre de sauvegardes à conserver.
    :param dry_run: Si ``True``, liste les suppressions sans les effectuer.
    """
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
    """Affiche l'inventaire des sauvegardes existantes sur le NAS.

    Pour chaque serveur, liste les fichiers ``.img.gz`` triés du plus récent
    au plus ancien, avec leur taille et leur date de création.

    :param nas_path: Chemin du répertoire racine sur le NAS.
    :param servers: Liste de dictionnaires de configuration des serveurs.
    """
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
