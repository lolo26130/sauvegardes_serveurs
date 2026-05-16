# Sauvegardes Serveurs SBC

Sauvegarde des cartes SD/eMMC de serveurs SBC (Raspberry Pi 4, Banana Pi, Orange Pi 5)
via SSH + `dd`, avec stockage compressé (`.img.lz4` / `.img.gz`) sur un NAS Synology.

Les images peuvent être restaurées avec `dd`, balenaEtcher ou Raspberry Pi Imager.

---

## Architecture

```
[Serveur Pi]  dd if=/dev/mmcblk0 | lz4 -c
                      │ SSH
                      ▼
[PC]  ──────────────────────────────────► [NAS]  nom_YYYYMMDD_HHMMSS.img.lz4
         (mode streaming — aucun fichier temporaire sur le Pi)

[Serveur Pi]  tmux → dd | lz4 -c > /mnt/NAS/...   (mode tmux — PC peut se déconnecter)
```

| Fichier         | Rôle                                                              |
|-----------------|-------------------------------------------------------------------|
| `backup.py`     | Point d'entrée CLI — parse args, orchestre check → backup → rotation |
| `server.py`     | Connexion SSH, streaming `dd \| lz4/gzip`, sessions tmux          |
| `storage.py`    | Vérification NAS, rotation des anciennes sauvegardes, inventaire  |
| `containers.py` | Détection des containers Docker (CLI ou fallback `/proc`)         |
| `config.yml`    | Liste des serveurs (IP, user, device) + chemin NAS + rétention    |

---

## Prérequis

### Sur le PC (machine locale)

```bash
pip install pyyaml   # ou : uv run backup.py  (gestion auto des deps)
```

- `uv` recommandé : `pip install uv` — gère les dépendances sans venv manuel
- `make` pour les raccourcis
- NAS monté en local (ex. `/home/laurent/Mounts/NAS_Vol2/...`)

### Sur chaque serveur distant

```bash
sudo apt install tmux lz4 nfs-common
```

- **Accès SSH par clé** (`BatchMode=yes` — aucun mot de passe accepté)
- **`sudo dd` sans mot de passe** dans `/etc/sudoers` :
  ```
  dietpi ALL=(ALL) NOPASSWD: /usr/bin/dd
  ```
- **NAS monté** sur le serveur (mode tmux uniquement) :
  ```bash
  sudo mkdir /mnt/NAS_sauvegardes
  ```
  Ligne `/etc/fstab` :
  ```
  192.168.0.21:/volume2/Volume_2/ALL_servers_SP3C_backups  /mnt/NAS_sauvegardes  nfs4  defaults,_netdev,nofail,hard,rsize=131072,wsize=131072  0  0
  ```

---

## Configuration

`config.yml` — à adapter avant la première utilisation :

```yaml
nas:
  path: /home/laurent/Mounts/NAS_Vol2/ALL_servers_SP3C_backups/  # NAS monté sur ce PC
  remote_path: /mnt/NAS_sauvegardes                              # NAS monté sur les Pi (mode tmux)

retention:
  keep: 3          # sauvegardes conservées par serveur

servers:
  - name: raspberry-pi4
    ip: 192.168.0.53
    user: dietpi
    device: /dev/mmcblk0    # confirmer avec : make check
    compressor: lz4         # ou gzip si lz4 absent
```

> **Orange Pi 5** : le device eMMC peut être `/dev/mmcblk0` ou `/dev/mmcblk1`.
> Toujours vérifier avec `make check` avant la première sauvegarde.

Options par serveur :
- `compressor: lz4` — compression ~10× plus rapide que gzip (ratio légèrement inférieur)
- `no_sudo: true` — connexion déjà en root (ex. HAOS)
- `ssh_key: /path/to/key` — clé SSH spécifique

---

## Commandes

```bash
make check                      # Tester la connexion SSH à tous les serveurs
make backup                     # Sauvegarder tous les serveurs (streaming via PC)
make tmux                       # Lancer sur tous les serveurs via tmux (résistant aux coupures)
make tmux SERVER=raspberry-pi4  # Lancer un seul serveur via tmux
make status                     # État des sessions tmux en cours
make list                       # Lister les sauvegardes existantes sur le NAS
make speed                      # Mesurer les débits SSH + NAS et estimer la durée
make docker                     # Lister les containers Docker actifs (CPU / RAM / trafic)
```

Sans `make` (équivalents directs) :

```bash
uv run backup.py --check
uv run backup.py --tmux --server raspberry-pi4
uv run backup.py --status
uv run backup.py --dry-run       # Simuler sans rien écrire
```

### Modes de sauvegarde

| Mode | Commande | Résistant aux coupures PC | Stockage intermédiaire |
|------|----------|--------------------------|------------------------|
| Streaming PC | `make backup` | Non | Aucun |
| tmux sur Pi | `make tmux` | **Oui** | Aucun (stream direct NAS) |

Le mode tmux est recommandé pour les grosses eMMC (Orange Pi 5, 233 GB).

### Suivi d'une session tmux en cours

```bash
make status
# ou directement sur le Pi :
ssh -t dietpi@192.168.0.53 '/usr/bin/tmux attach -t bkp_raspberry-pi4'
```

---

## Détection Docker (`make docker`)

Détecte les containers Docker en cours sur chaque serveur, sans modifier les droits.

Deux méthodes selon les permissions disponibles :
1. **Docker CLI** — si l'utilisateur SSH est dans le groupe `docker`
2. **Fallback `/proc`** — via `ps` + cgroup, sans droits particuliers (CPU%, RAM, ports exposés)

Produit un rapport `docker_YYYYMMDD_HHMMSS.md` sur le NAS.

---

## Restauration d'une image

### Méthode 1 — Ligne de commande (la plus rapide)

```bash
# Identifier la carte SD (avant/après branchement)
lsblk

# Décompresser et écrire (remplacer /dev/sdX)
unlz4 raspberry-pi4_20260514.img.lz4 | sudo dd of=/dev/sdX bs=4M status=progress
# ou pour .img.gz :
gunzip -c raspberry-pi4_20260514.img.gz  | sudo dd of=/dev/sdX bs=4M status=progress
```

> ⚠ Vérifier `/dev/sdX` deux fois — `dd` écrase sans confirmation.
> La carte doit être de taille >= à l'original.

### Méthode 2 — balenaEtcher (graphique, Windows/Mac/Linux)

1. Télécharger : https://etcher.balena.io
2. Flash from file → sélectionner le `.img.lz4`
3. Select target → choisir la carte SD
4. Flash!

Le `.lz4` est supporté nativement, pas besoin de décompresser.

### Méthode 3 — Raspberry Pi Imager (recommandé pour les Pi)

1. Choose OS → Use custom → sélectionner le `.img.lz4`
2. Choose Storage → sélectionner la carte SD
3. Icône engrenage → configurer hostname / SSH / Wi-Fi si besoin
4. Write

---

## Documentation

```bash
make docs   # Génère la doc HTML Sphinx dans docs/_build/html/
make uml    # Régénère les diagrammes UML (nécessite graphviz)
```

Diagramme d'architecture : `docs/_static/uml/modules_fonctions.png`

---

## Logs

Chaque exécution est journalisée dans `logs/backup.log` (créé automatiquement).

Les sessions tmux produisent également sur le NAS :
- `<nom>_<ts>.ddlog` — sortie stderr de `dd` (progression, vitesse)
- `<nom>_<ts>.done` — code retour (0 = succès)
