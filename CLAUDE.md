# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Python script that images the SD card / eMMC of SBC servers (Raspberry Pi 4, Banana Pi, Orange Pi 5) on a local network via SSH+dd, and stores compressed `.img.gz` files on a NAS. Images can be written back with `dd` or `balenaEtcher`.

Target servers: 192.168.0.53, 192.168.0.54, 192.168.0.55

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Check SSH connectivity and list block devices on all servers
python backup.py --check

# Run backups (all servers)
python backup.py

# Run backup for one server
python backup.py --server raspberry-pi4

# Simulate without writing
python backup.py --dry-run

# List existing backups on the NAS
python backup.py --list
```

## Architecture

- `backup.py` — Entry point: argument parsing, orchestrates check → backup → rotation
- `server.py` — SSH connection + `dd | gzip` streaming via `subprocess`; no data stored on the Pi
- `storage.py` — NAS write check, backup rotation (`cleanup_old_backups`), listing
- `config.yml` — Server list (name, IP, user, device) and NAS path / retention count
- `logs/backup.log` — Appended each run (directory created on first run)

## Key constraints

**SSH key required**: `BatchMode=yes` is set, so password auth will fail. The user running this script must have a key-based SSH access to each server with passwordless `sudo dd`.

**Orange Pi 5 eMMC device**: may differ (`/dev/mmcblk0` vs `/dev/mmcblk1`). Always run `--check` on first use to confirm the correct device before editing `config.yml`.

**Streaming**: `dd` stdout is piped directly to an open file on the NAS — no temporary file on the Pi. A 32 GB card produces ~5–15 GB compressed depending on content.

**Timeout**: 2 hours per server. Adjust `timeout=7200` in `server.py` for very large eMMC.
