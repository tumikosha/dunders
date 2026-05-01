"""Built-in themes + TOML loader."""

from .modern_dark import modern_dark
from .loader import load_theme, list_themes, theme_registry

__all__ = ["modern_dark", "load_theme", "list_themes", "theme_registry"]
