"""The bundled ``safe_*`` themes must use only exact xterm-256 colours.

A colour that is already an xterm-256 palette member quantizes to itself on a
256-colour terminal (macOS Terminal.app), so these themes render identically
there and on truecolor terminals — unlike the truecolor example themes whose
hex values get approximated.
"""

import pytest

from dunders.windowing.themes.loader import list_themes, load_theme
from dunders.windowing.themes.modern_dark import modern_dark

# xterm-256 colour cube: each channel is one of these six levels.
CUBE = frozenset({0x00, 0x5F, 0x87, 0xAF, 0xD7, 0xFF})
# xterm-256 grayscale ramp (indices 232..255), plus the cube grays.
GRAYS = frozenset(
    {0x08, 0x12, 0x1C, 0x26, 0x30, 0x3A, 0x44, 0x4E, 0x58, 0x62, 0x6C, 0x76,
     0x80, 0x8A, 0x94, 0x9E, 0xA8, 0xB2, 0xBC, 0xC6, 0xD0, 0xDA, 0xE4, 0xEE}
) | CUBE

SAFE_THEMES = ["safe_dark", "safe_light", "safe_nord"]


def is_256_safe(hexstr: str) -> bool:
    """True iff ``hexstr`` (``#rrggbb``) is an exact xterm-256 palette entry."""
    s = hexstr.lstrip("#")
    if len(s) != 6:
        return False
    r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    if r in CUBE and g in CUBE and b in CUBE:
        return True
    return r == g == b and r in GRAYS


def test_is_256_safe_recognizes_grid_and_offgrid():
    for ok in ("#5fafff", "#1c1c1c", "#d0d0d0", "#000000", "#ffffff"):
        assert is_256_safe(ok), ok
    for bad in ("#0f0f0f", "#2a2a2a", "#a0a0a0", "#88c0d0", "#bd93f9"):
        assert not is_256_safe(bad), bad


@pytest.mark.parametrize("name", SAFE_THEMES)
def test_safe_theme_loads_and_is_listed(name):
    theme = load_theme(name)
    assert theme.name == name
    assert name in list_themes()


@pytest.mark.parametrize("name", SAFE_THEMES)
def test_safe_theme_only_uses_256_safe_colors(name):
    theme = load_theme(name)
    offenders = []
    for role, style in theme.styles.items():
        for attr in ("fg", "bg"):
            value = getattr(style, attr)
            if value is not None and not is_256_safe(value):
                offenders.append(f"{role}.{attr}={value}")
    assert not offenders, f"{name} has non-256-safe colours: {offenders}"


@pytest.mark.parametrize("name", SAFE_THEMES)
def test_safe_theme_covers_all_modern_dark_roles(name):
    theme = load_theme(name)
    missing = set(modern_dark.styles) - set(theme.styles)
    assert not missing, f"{name} is missing roles: {sorted(missing)}"
