RESET  := \033[0m
BOLD   := \033[1m
RED    := \033[1;31m
GREEN  := \033[1;32m
YELLOW := \033[1;33m
BLUE   := \033[1;34m
CYAN   := \033[1;36m
GRAY   := \033[0;37m
SEP    := $(BLUE)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(RESET)

.PHONY: check backup tmux status list speed docs uml help

help:
	@printf "\n$(SEP)\n"
	@printf "  $(BOLD)Sauvegardes Serveurs$(RESET) — commandes disponibles\n"
	@printf "$(SEP)\n\n"
	@printf "  $(YELLOW)SAUVEGARDE$(RESET)\n"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make check"                      "Tester la connexion SSH à tous les serveurs"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make backup"                     "Sauvegarder tous les serveurs (streaming PC)"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make tmux"                       "Lancer sur tous les serveurs via tmux (résistant aux coupures)"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make tmux SERVER=raspberry-pi4"  "Lancer un seul serveur via tmux"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make status"                     "État des sessions tmux en cours"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make list"                       "Lister les sauvegardes existantes sur le NAS"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make speed"                      "Mesurer les débits SSH + NAS et estimer la durée"
	@printf "\n"
	@printf "  $(YELLOW)DOCUMENTATION$(RESET)\n"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make docs"  "Regénérer la doc HTML Sphinx"
	@printf "  $(GREEN)%-38s$(RESET) %s\n" "make uml"   "Regénérer les diagrammes UML + doc"
	@printf "\n$(SEP)\n"
	@printf "  $(BOLD)RESTAURER UNE IMAGE .img.lz4$(RESET)\n"
	@printf "$(SEP)\n\n"
	@printf "  $(CYAN)Méthode 1 — Ligne de commande Linux/Mac$(RESET) $(GRAY)(la plus rapide)$(RESET)\n\n"
	@printf "    $(GRAY)# 1. Repérer la carte SD : brancher, puis comparer avant/après$(RESET)\n"
	@printf "    $(GREEN)lsblk$(RESET)\n\n"
	@printf "    $(GRAY)# 2. Décompresser et écrire en un seul pipeline (remplacer /dev/sdX)$(RESET)\n"
	@printf "    $(GREEN)unlz4 raspberry-pi4_20260514.img.lz4 | sudo dd of=/dev/sdX bs=4M status=progress$(RESET)\n\n"
	@printf "    $(RED)⚠  Vérifiez /dev/sdX deux fois — dd écrase sans confirmation.$(RESET)\n"
	@printf "    $(RED)⚠  La carte doit être de taille >= à l'original (ex. >= 32 GB).$(RESET)\n\n"
	@printf "  $(CYAN)Méthode 2 — balenaEtcher$(RESET) $(GRAY)(graphique, Windows / Mac / Linux)$(RESET)\n\n"
	@printf "    $(GRAY)Avantage : valide l'écriture automatiquement après le flash.$(RESET)\n"
	@printf "    1. Télécharger : $(CYAN)https://etcher.balena.io$(RESET)\n"
	@printf "    2. Flash from file  → sélectionner le .img.lz4\n"
	@printf "    3. Select target    → choisir la carte SD\n"
	@printf "    4. Flash!\n"
	@printf "    $(GRAY)→ Le .lz4 est supporté nativement, pas besoin de décompresser.$(RESET)\n\n"
	@printf "  $(CYAN)Méthode 3 — Raspberry Pi Imager$(RESET) $(GRAY)(recommandé pour les Pi)$(RESET)\n\n"
	@printf "    $(GRAY)Avantage : peut injecter clé SSH et Wi-Fi avant d'écrire.$(RESET)\n"
	@printf "    1. Choose OS      → Use custom → sélectionner le .img.lz4\n"
	@printf "    2. Choose Storage → sélectionner la carte SD\n"
	@printf "    3. Icône engrenage → configurer hostname / SSH / Wi-Fi si besoin\n"
	@printf "    4. Write\n"
	@printf "    $(GRAY)→ Supporte les images compressées (.gz, .lz4, .xz).$(RESET)\n\n"
	@printf "$(SEP)\n\n"

check:
	uv run backup.py --check

backup:
	uv run backup.py

tmux:
	uv run backup.py --tmux $(if $(SERVER),--server $(SERVER),)

status:
	uv run backup.py --status

list:
	uv run backup.py --list

speed:
	uv run backup.py --speed-test

docs:
	uvx --with sphinx-rtd-theme --with sphinx-autodoc-typehints --with pyyaml \
	    --from sphinx sphinx-build -b html docs docs/_build/html

uml:
	uvx --from pylint pyreverse -o dot -p sauvegardes \
	    --output-directory docs/_static/uml backup.py server.py storage.py
	dot -Tpng docs/_static/uml/packages_sauvegardes.dot -o docs/_static/uml/packages_sauvegardes.png
	dot -Tpng docs/_static/uml/modules_fonctions.dot    -o docs/_static/uml/modules_fonctions.png
	$(MAKE) docs
