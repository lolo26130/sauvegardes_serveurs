"""
Gestion du stockage NAS : vérification, rotation et inventaire.

Ce module fournit les utilitaires de gestion du répertoire de destination
sur le NAS : test d'accessibilité en écriture, rotation des anciennes
sauvegardes et affichage de l'inventaire.
"""
import logging
import os
import time
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
        [*server_dir.glob("*.img.gz"), *server_dir.glob("*.img.lz4")],
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


def speed_test_nas(nas_path: Path, size_mb: int = 256) -> float | None:
    """Mesure la vitesse d'écriture séquentielle sur le NAS.

    Écrit ``size_mb`` Mo de zéros en blocs de 1 Mo avec fsync final,
    puis supprime le fichier de test.

    :param nas_path: Répertoire racine du NAS.
    :param size_mb: Volume de données à écrire en Mo (défaut : 256).
    :returns: Débit en Mo/s, ou ``None`` si le NAS est inaccessible.
    """
    test_file = nas_path / ".speed_test_write"
    chunk = b"\x00" * (1024 * 1024)
    try:
        start = time.monotonic()
        with open(test_file, "wb") as f:
            for _ in range(size_mb):
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        elapsed = time.monotonic() - start
        return size_mb / elapsed
    except OSError as e:
        log.error(f"Speed test NAS impossible: {e}")
        return None
    finally:
        test_file.unlink(missing_ok=True)


def _taille(octets: int) -> str:
    """Taille lisible : en GB à partir de 1 GB, sinon en MB."""
    return f"{octets / 1024 ** 3:.1f} GB" if octets >= 1024 ** 3 else f"{octets / 1024 ** 2:.0f} MB"


def list_backups(nas_path: Path, servers: list[dict], externes: list[dict] | None = None):
    """Affiche l'inventaire des sauvegardes existantes sur le NAS.

    Pour chaque serveur, liste les fichiers ``.img.gz`` triés du plus récent
    au plus ancien, avec leur taille et leur date de création.
    Puis, pour chaque entrée ``externes`` de la configuration (sauvegardes faites par d'autres outils,
    ex. Home Assistant), liste les fichiers correspondant à ses ``motifs`` — en lecture seule.

    :param nas_path: Chemin du répertoire racine sur le NAS.
    :param servers: Liste de dictionnaires de configuration des serveurs.
    :param externes: Entrées ``name`` / ``dossier`` (relatif à ``nas_path``) / ``motifs`` / ``alerte_jours`` optionnel.
    """
    print(f"\nSauvegardes dans {nas_path}:\n")
    for server in servers:
        server_dir = nas_path / server["name"]
        backups = sorted(
            [*server_dir.glob("*.img.gz"), *server_dir.glob("*.img.lz4")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
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

    if externes:
        from datetime import datetime
        print("\n  Sauvegardes externes (faites par d'autres outils — affichées seulement, jamais modifiées ici) :\n")
        for ext in externes:
            ext_dir = nas_path / ext["dossier"]
            fichiers = sorted(
                {f for motif in ext.get("motifs", ["*"]) for f in ext_dir.glob(motif) if f.is_file()},
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not fichiers:
                print(f"  {ext['name']}: aucune sauvegarde dans {ext_dir}")
                continue
            total = sum(f.stat().st_size for f in fichiers)
            age = (time.time() - fichiers[0].stat().st_mtime) / 86400
            alerte = ""
            if (seuil := ext.get("alerte_jours")) and age > seuil:
                alerte = f"  ⚠ dernière il y a {age:.0f} jours (> {seuil})"
            print(f"  {ext['name']}: {len(fichiers)} sauvegarde(s) — {_taille(total)} total, "
                  f"dernière il y a {age:.0f} jour(s){alerte}")
            for f in fichiers:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"    {mtime}  {f.name}  ({_taille(f.stat().st_size)})")
    print()
