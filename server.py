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
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",           # Fail immediately if no SSH key
    "-o", "ServerAliveInterval=30",  # Ping toutes les 30s
    "-o", "ServerAliveCountMax=4",   # Abandonne après 4 pings sans réponse (~2 min)
]

TIMEOUT = 3600 * 20

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


def speed_test_server(server: dict, size_mb: int = 256) -> dict | None:
    """Mesure le débit SSH et la taille du périphérique à sauvegarder.

    Transfère ``size_mb`` Mo depuis ``/dev/zero`` du serveur distant vers
    ``/dev/null`` local pour mesurer le débit réseau SSH, puis interroge
    la taille réelle du périphérique via ``blockdev``.

    :param server: Dictionnaire de configuration du serveur.
    :param size_mb: Volume transféré pour le test de débit (défaut : 256 Mo).
    :returns: Dictionnaire avec ``ssh_mbs`` (Mo/s), ``device_gb`` et
              ``estimated_min`` (fourchette basse/haute), ou ``None`` si échec.
    """
    name = server["name"]
    device = server["device"]

    # Taille réelle du device
    try:
        r = subprocess.run(
            _ssh_cmd(server, f"sudo blockdev --getsize64 {device}"),
            capture_output=True, text=True, timeout=15,
        )
        device_bytes = int(r.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError) as e:
        log.error(f"[{name}] Impossible de lire la taille du device: {e}")
        return None

    # Débit SSH : lecture /dev/zero distant → /dev/null local
    cmd = _ssh_cmd(server, f"dd if=/dev/zero bs=1M count={size_mb} 2>/dev/null")
    try:
        start = time.monotonic()
        subprocess.run(cmd, stdout=subprocess.DEVNULL, timeout=120, check=True)
        elapsed = time.monotonic() - start
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        log.error(f"[{name}] Test débit SSH échoué: {e}")
        return None

    ssh_mbs = size_mb / elapsed
    device_gb = device_bytes / (1024 ** 3)

    # Estimation : compression 30 % (best) à 55 % (worst) pour image OS
    compressed_best  = device_bytes * 0.30
    compressed_worst = device_bytes * 0.55
    speed_bps = ssh_mbs * 1024 * 1024
    est_min_best  = compressed_best  / speed_bps / 60
    est_min_worst = compressed_worst / speed_bps / 60

    return {
        "name": name,
        "device": device,
        "device_gb": device_gb,
        "ssh_mbs": ssh_mbs,
        "est_min_best": est_min_best,
        "est_min_worst": est_min_worst,
    }


def _tmux_session_name(server: dict) -> str:
    return f"bkp_{server['name']}"


def start_backup_tmux(server: dict, remote_nas: str, dry_run=False) -> str | None:
    """Lance la sauvegarde dans une session tmux détachée sur le serveur.

    Le flux est entièrement local au Pi : ``dd | compressor > NAS``.
    La connexion SSH du PC peut être interrompue sans affecter la sauvegarde.
    Un fichier ``<nom>_<ts>.done`` (contenant le code retour) est créé à la fin.

    Prérequis supplémentaire sur le Pi :

    - NAS monté en écriture sur ``remote_nas`` (ex. via ``/etc/fstab`` SMB/NFS)

    :param server: Dictionnaire de configuration du serveur.
    :param remote_nas: Chemin du NAS tel que monté sur le Pi.
    :param dry_run: Si ``True``, affiche la commande sans l'exécuter.
    :returns: Nom de la session tmux, ou ``None`` en cas d'échec.
    """
    name = server["name"]
    device = server["device"]
    compressor = server.get("compressor", "gzip")
    ext = "lz4" if compressor == "lz4" else "gz"
    compress_cmd = "lz4 -c" if compressor == "lz4" else "gzip -c"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_dir = f"{remote_nas}/{name}"
    output_file = f"{remote_dir}/{name}_{timestamp}.img.{ext}"
    done_file = f"{remote_dir}/{name}_{timestamp}.done"
    log_file = f"{remote_dir}/{name}_{timestamp}.ddlog"

    session = _tmux_session_name(server)

    # dd stderr (progression) → log_file ; code retour → done_file
    remote_cmd = (
        f"mkdir -p {remote_dir} && "
        f"sudo dd if={device} bs=4M 2>{log_file} | {compress_cmd} > {output_file} ; "
        f"echo $? > {done_file}"
    )
    tmux_cmd = f"/usr/bin/tmux new-session -d -s {session} '{remote_cmd}'"

    log.info(f"[{name}] Mode tmux — {device} → {output_file}")

    if dry_run:
        log.info(f"[{name}] [DRY-RUN] {tmux_cmd}")
        return session

    # Tuer une éventuelle session fantôme du même nom
    subprocess.run(_ssh_cmd(server, f"tmux kill-session -t {session} 2>/dev/null"), timeout=10)

    result = subprocess.run(_ssh_cmd(server, tmux_cmd), capture_output=True, timeout=15)
    if result.returncode != 0:
        log.error(f"[{name}] Échec création session tmux: {result.stderr.strip()}")
        return None

    log.info(f"[{name}] Session '{session}' démarrée.")
    log.info(f"[{name}] Suivi interactif : ssh {server.get('user','pi')}@{server['ip']} 'tmux attach -t {session}'")
    return session


def tmux_status(server: dict, remote_nas: str) -> dict:
    """Retourne l'état de la session tmux de sauvegarde.

    Vérifie si la session est active, et si un fichier ``.done`` existe
    (indiquant la fin de la sauvegarde et son code retour).

    :param server: Dictionnaire de configuration du serveur.
    :param remote_nas: Chemin du NAS tel que monté sur le Pi.
    :returns: Dictionnaire avec les clés ``running`` (bool), ``done`` (bool),
              ``exit_code`` (int|None), ``files`` (liste des ``.img.*`` créés).
    """
    name = server["name"]
    session = _tmux_session_name(server)
    remote_dir = f"{remote_nas}/{name}"

    # Session toujours active ?
    r = subprocess.run(
        _ssh_cmd(server, f"tmux has-session -t {session} 2>/dev/null"),
        capture_output=True, timeout=10,
    )
    running = r.returncode == 0

    # Fichier .done présent ?
    r2 = subprocess.run(
        _ssh_cmd(server, f"cat {remote_dir}/*.done 2>/dev/null || true"),
        capture_output=True, text=True, timeout=10,
    )
    done_content = r2.stdout.strip()
    done = bool(done_content)
    exit_code = int(done_content) if done_content.isdigit() else None

    # Taille du dernier fichier image en cours
    r3 = subprocess.run(
        _ssh_cmd(server, f"ls -lh {remote_dir}/*.img.* 2>/dev/null | tail -1 || true"),
        capture_output=True, text=True, timeout=10,
    )
    latest = r3.stdout.strip()

    return {"name": name, "running": running, "done": done, "exit_code": exit_code, "latest": latest}


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
    # lz4 : compression ~10x plus rapide que gzip, ratio légèrement inférieur
    compressor = server.get("compressor", "gzip")
    ext = "lz4" if compressor == "lz4" else "gz"
    compress_cmd = "lz4 -c" if compressor == "lz4" else "gzip -c"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    server_dir = nas_path / name
    output_file = server_dir / f"{name}_{timestamp}.img.{ext}"

    remote_cmd = f"sudo dd if={device} bs=4M status=progress | {compress_cmd}"

    cmd = _ssh_cmd(server, remote_cmd)

    log.info(f"[{name}] {device} → {output_file.name}")

    if dry_run:
        log.info(f"[{name}] [DRY-RUN] Commande: {' '.join(cmd)}")
        return output_file

    server_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"[{name}] Transfert en cours (peut durer 15-60 min selon la taille)...")
    try:
        with open(output_file, "wb") as f:
            proc = subprocess.run(cmd, stdout=f, timeout=TIMEOUT)  #  max

        if proc.returncode != 0:
            log.error(f"[{name}] dd/SSH a retourné le code {proc.returncode}")
            output_file.unlink(missing_ok=True)
            return None

        size_mb = output_file.stat().st_size / (1024 ** 2)
        log.info(f"[{name}] Terminé: {output_file.name} ({size_mb:.0f} MB)")
        return output_file

    except subprocess.TimeoutExpired:
        log.error(f"[{name}] Timeout dépassé ({TIMEOUT/3600} h)")
        output_file.unlink(missing_ok=True)
        return None
    except OSError as e:
        log.error(f"[{name}] Erreur écriture NAS: {e}")
        output_file.unlink(missing_ok=True)
        return None
