import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "Sauvegardes Serveurs"
author = "Laurent"
release = "1.0"
language = "fr"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
]

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "special-members": "__init__",
}
autodoc_typehints = "description"
napoleon_use_param = True
napoleon_use_rtype = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "Sauvegardes Serveurs"

templates_path = ["_templates"]
exclude_patterns = ["_build"]
