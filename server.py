"""
Connexion SSH et création d'images disque.

Ce module gère la connexion SSH vers les serveurs SBC et le streaming de
l'image disque compressée (``dd | gzip``) directement vers le NAS, sans
stockage intermédiaire sur le serveur source.

Prérequis sur chaque serveur distant :

- Accès SSH par clé publique (``BatchMode=yes`` — pas de mot de passe)
- ``sudo dd`` sans mot de passe (ligne dans ``/etc/sudoers``) ::

    pi ALL=(ALL) NOPASSWD: /usr/bin/dd
"""
import logging
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",           # Fail immediately if no SSH key
]


def _ssh_cmd(server: dict, remote_cmd: str) -> list[str]:
    """Construit la liste d'arguments ``ssh`` pour un serveur donné.

    :param server: Dictionnaire de configuration du serveur (``ip``, ``user``, ``ssh_key`` optionnel).
    :param remote_cmd: Commande shell à exécuter sur le serveur distant.
    :returns: Liste d'arguments prête pour :func:`subprocess.run`.
    """
    cmd = ["ssh"] + SSH_OPTS
    if key := server.get("ssh_key"):
        cmd += ["-i", key]
    cmd += [f"{server.get('user', 'pi')}@{server['ip']}", remote_cmd]
    return cmd


def check_server(server: dict) -> bool:
    """Vérifie la connectivité SSH et liste les périphériques bloc du serveur.

    Exécute ``lsblk`` en distant et affiche le résultat dans les logs.
    Permet de confirmer le nom du périphérique (ex. ``/dev/mmcblk0``)
    avant la première sauvegarde.

    :param server: Dictionnaire de configuration du serveur.
    :returns: ``True`` si la connexion réussit, ``False`` sinon.
    """
    name = server["name"]
    ip = server["ip"]
    log.info(f"[{name}] Vérification connexion SSH ({ip})...")
    try:
        result = subprocess.run(
            _ssh_cmd(server, "lsblk -d -o NAME,SIZE,TYPE,MOUNTPOINT"),
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            log.error(f"[{name}] Connexion échouée: {result.stderr.strip()}")
            return False
        log.info(f"[{name}] Connecté. Périphériques:\n{result.stdout}")
        return True
    except subprocess.TimeoutExpired:
        log.error(f"[{name}] Timeout connexion SSH")
        return False


def backup_server(server: dict, nas_path: Path, dry_run=False) -> Path | None:
    """Image le périphérique disque du serveur et le stream compressé vers le NAS.

    Flux de données ::

        [Serveur] dd if=/dev/mmcblk0 | gzip -c  ──SSH──►  [NAS] nom_YYYYMMDD_HHMMSS.img.gz

    Aucun fichier temporaire n'est créé sur le serveur source.
    Le fichier de sortie est supprimé automatiquement en cas d'erreur partielle.

    :param server: Dictionnaire de configuration du serveur (``name``, ``ip``, ``user``, ``device``).
    :param nas_path: Répertoire racine de stockage sur le NAS.
    :param dry_run: Si ``True``, simule sans écrire aucun fichier.
    :returns: Chemin du fichier ``.img.gz`` créé, ou ``None`` en cas d'échec.
    :raises OSError: Si le NAS n'est pas accessible en écriture.
    """
    name = server["name"]
    device = server["device"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    server_dir = nas_path / name
    output_file = server_dir / f"{name}_{timestamp}.img.gz"

    # dd with progress sent to stderr (stays in terminal), stdout piped here
    # remote_cmd = f"sudo dd if={device} bs=4M 2>/dev/null | gzip -c"
    remote_cmd = f"sudo dd if={device} bs=4M status=progress | gzip -c"  # avec data de progression

    cmd = _ssh_cmd(server, remote_cmd)

    log.info(f"[{name}] {device} → {output_file.name}")

    if dry_run:
        log.info(f"[{name}] [DRY-RUN] Commande: {' '.join(cmd)}")
        return output_file

    server_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[{name}] Transfert en cours (peut durer 15-60 min selon la taille)...")
    try:
        with open(output_file, "wb") as f:
            proc = subprocess.run(cmd, stdout=f, timeout=7200)  # 2h max

        if proc.returncode != 0:
            log.error(f"[{name}] dd/SSH a retourné le code {proc.returncode}")
            output_file.unlink(missing_ok=True)
            return None

        size_mb = output_file.stat().st_size / (1024 ** 2)
        log.info(f"[{name}] Terminé: {output_file.name} ({size_mb:.0f} MB)")
        return output_file

    except subprocess.TimeoutExpired:
        log.error(f"[{name}] Timeout dépassé (2h)")
        output_file.unlink(missing_ok=True)
        return None
    except OSError as e:
        log.error(f"[{name}] Erreur écriture NAS: {e}")
        output_file.unlink(missing_ok=True)
        return None
