Notice d'utilisation
====================

Prérequis
---------

**Sur cette machine (poste de contrôle) :**

- Python ≥ 3.10
- Clé SSH configurée (``~/.ssh/id_rsa`` ou ``id_ed25519``)
- NAS monté en lecture/écriture (ex. ``/home/laurent/Mounts/NAS_Vol2/...``)

**Sur chaque serveur SBC :**

Autoriser ``dd`` sans mot de passe via ``sudo visudo`` ::

    pi ALL=(ALL) NOPASSWD: /usr/bin/dd

Propager la clé publique une seule fois ::

    ssh-copy-id pi@192.168.0.53


Configuration
-------------

Éditer ``config.yml`` à la racine du projet :

.. code-block:: yaml

    nas:
      path: /home/laurent/Mounts/NAS_Vol2/ALL_servers_SP3C_backups/

    retention:
      keep: 3          # sauvegardes conservées par serveur

    servers:
      - name: raspberry-pi4
        ip: 192.168.0.53
        user: dietpi
        device: /dev/mmcblk0

      - name: orange-pi5
        ip: 192.168.0.55
        user: dietpi
        device: /dev/mmcblk1   # eMMC — vérifier avec --check

Clé SSH spécifique (optionnel) : ajouter ``ssh_key: /chemin/vers/id_ed25519``
dans l'entrée du serveur concerné.


Vérification initiale
---------------------

Avant toute sauvegarde, vérifier la connectivité SSH et identifier
le périphérique correct (indispensable pour l'Orange Pi 5 en eMMC) ::

    python backup.py --check

Exemple de sortie ::

    [raspberry-pi4] Connecté. Périphériques:
    NAME     SIZE TYPE MOUNTPOINT
    mmcblk0 29.7G disk
    mmcblk0boot0    4M disk
    mmcblk0boot1    4M disk

    [orange-pi5] Connecté. Périphériques:
    NAME     SIZE TYPE MOUNTPOINT
    mmcblk0   7G disk          ← carte SD (boot)
    mmcblk1  29G disk          ← eMMC (à sauvegarder)


Lancer les sauvegardes
----------------------

**Tous les serveurs :**

.. code-block:: bash

    python backup.py

**Un seul serveur (par nom ou IP) :**

.. code-block:: bash

    python backup.py --server raspberry-pi4
    python backup.py --server 192.168.0.53

**Simulation sans écriture :**

.. code-block:: bash

    python backup.py --dry-run

**Mode verbeux (debug) :**

.. code-block:: bash

    python backup.py -v


Consulter les sauvegardes existantes
-------------------------------------

.. code-block:: bash

    python backup.py --list

Exemple de sortie ::

    Sauvegardes dans /home/laurent/Mounts/NAS_Vol2/ALL_servers_SP3C_backups/:

      raspberry-pi4: 2 sauvegarde(s) — 18.4 GB total
        2026-05-12 02:15  raspberry-pi4_20260512_021500.img.gz  (9.3 GB)
        2026-05-11 23:00  raspberry-pi4_20260511_230000.img.gz  (9.1 GB)

      orange-pi5: 1 sauvegarde(s) — 11.2 GB total
        2026-05-12 04:45  orange-pi5_20260512_044500.img.gz  (11.2 GB)


Restaurer une image
-------------------

Pour réécrire l'image sur une nouvelle carte SD ou eMMC ::

    # Décompresser et écrire sur /dev/sdX (adapter le périphérique)
    gunzip -c raspberry-pi4_20260512_021500.img.gz | sudo dd of=/dev/sdX bs=4M status=progress

Ou avec **balenaEtcher** / **Raspberry Pi Imager** en sélectionnant
directement le fichier ``.img.gz``.

.. warning::

    Vérifier soigneusement le périphérique de destination (``lsblk``)
    avant d'écrire — l'opération est irréversible.


Automatisation (cron)
---------------------

Exemple pour lancer la sauvegarde tous les dimanches à 02h00 ::

    crontab -e

    0 2 * * 0  cd /home/laurent/Documents/Python/sauvegardes_serveurs && python backup.py >> logs/cron.log 2>&1

Les logs sont également écrits dans ``logs/backup.log`` à chaque exécution.


Durées typiques
---------------

+------------------+----------+------------+---------------------+
| Serveur          | Taille   | Compressé  | Durée estimée       |
+==================+==========+============+=====================+
| Raspberry Pi 4   | 32 GB    | ~8–12 GB   | 20–40 min           |
+------------------+----------+------------+---------------------+
| Banana Pi        | 32 GB    | ~8–12 GB   | 20–40 min           |
+------------------+----------+------------+---------------------+
| Orange Pi 5 eMMC | 32 GB    | ~8–15 GB   | 25–50 min           |
+------------------+----------+------------+---------------------+
| DietPiHole       | 16 GB    | ~3–6 GB    | 10–20 min           |
+------------------+----------+------------+---------------------+

La durée dépend de la vitesse réseau locale (100 Mbit/s à 1 Gbit/s)
et du taux de compression des données.
