project = "Che vuoi?"
author = "ishida"
copyright = "2026, ishida"

extensions = ["myst_parser"]

language = "ja"

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = "Che vuoi? 仕様書"

myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
