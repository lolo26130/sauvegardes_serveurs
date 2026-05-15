.PHONY: check backup tmux status list speed docs uml

check:
	uv run backup.py --check

backup:
	uv run backup.py

tmux:
	uv run backup.py --tmux

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
