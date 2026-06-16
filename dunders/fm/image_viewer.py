"""ImageViewerContent — view image files as ASCII art (F3).

Pillow is imported lazily and guarded by PILLOW_AVAILABLE so the base
install stays dependency-light; the decode/resize step is the only place
that touches Pillow. The pixel->grid transform (`image_to_ascii`), the
aspect-fit helper (`_fit`), and the magic-byte sniffer (`sniff_image`) are
pure and import nothing heavy, so they unit-test in isolation.
"""

from __future__ import annotations

# Brightness ramp from darkest (space) to brightest ('@').
_RAMP = " .:-=+*#%@"


def sniff_image(head: bytes) -> bool:
    """True if `head` (first ~16 bytes of a file) starts with a known
    image magic signature. Extension is irrelevant."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return True
    if head.startswith(b"BM"):
        return True
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return False


def _fit(
    img_w: int,
    img_h: int,
    max_cols: int,
    max_rows: int,
    cell_aspect: float = 0.5,
) -> tuple[int, int]:
    """Fit an `img_w`x`img_h` image into a `max_cols`x`max_rows` character
    grid, correcting for the terminal cell aspect ratio (a cell is ~twice
    as tall as wide, so `cell_aspect` 0.5 squashes the row count)."""
    img_w = max(1, img_w)
    img_h = max(1, img_h)
    out_w = max(1, max_cols)
    out_h = max(1, int(out_w * img_h / img_w * cell_aspect))
    if out_h > max_rows:
        out_h = max(1, max_rows)
        out_w = max(1, int(out_h * img_w / img_h / cell_aspect))
    return out_w, out_h


def _ramp_char(lum: float) -> str:
    idx = int(lum / 255 * (len(_RAMP) - 1))
    idx = max(0, min(idx, len(_RAMP) - 1))
    return _RAMP[idx]


def image_to_ascii(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    *,
    color: bool,
) -> list[list[tuple[str, tuple[int, int, int] | None]]]:
    """Turn a row-major flat list of RGB pixels into a grid of
    (char, rgb_or_None) cells. In mono mode the rgb element is None; in
    color mode it carries the pixel's RGB for a truecolor foreground."""
    grid: list[list[tuple[str, tuple[int, int, int] | None]]] = []
    for y in range(height):
        row: list[tuple[str, tuple[int, int, int] | None]] = []
        base = y * width
        for x in range(width):
            r, g, b = pixels[base + x]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            char = _ramp_char(lum)
            row.append((char, (r, g, b) if color else None))
        grid.append(row)
    return grid
