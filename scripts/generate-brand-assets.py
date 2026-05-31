"""Generate the favicon PNG sizes + OG share card from the canonical
brand tokens. Run from repo root: `python scripts/generate-brand-assets.py`.

Outputs:
  src/subarr/static/v1/favicon-16.png
  src/subarr/static/v1/favicon-32.png
  src/subarr/static/v1/favicon-192.png
  src/subarr/static/v1/favicon-512.png
  src/subarr/static/v1/og-card.png    (1200x630, Twitter/Facebook standard)

Why a script and not pre-rendered assets: the design tokens
(--bg-0 = #1a1a1c, --violet-500 = #8b5cf6) might change. Re-running
this script is a 50ms idempotent operation; committed PNGs would
silently drift from the live UI.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Brand tokens — match tokens.css.
BG = (26, 26, 28)        # --bg-0
ACCENT = (139, 92, 246)  # --violet-500
FG_0 = (245, 245, 247)   # near-white text
FG_2 = (170, 170, 178)   # muted text

# Output paths (relative to repo root).
ROOT = Path(__file__).parent.parent
OUTDIR = ROOT / "src/subarr/static/v1"


def _try_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """Best-effort font loader. Falls back through the candidate list,
    then to PIL's default bitmap font (legible but ugly)."""
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# Pillow ≥10.0 nukes a few of the API surfaces we used to rely on. Use
# the modern equivalents and avoid deprecation warnings.

def make_favicon(size: int) -> Image.Image:
    """Dark rounded square + violet `s`. Same visual as the canonical SVG."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(1, round(size * 0.1875))  # 6 of 32
    # Rounded square background.
    try:
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BG)
    except AttributeError:
        # Old Pillow — draw a flat rect.
        draw.rectangle((0, 0, size - 1, size - 1), fill=BG)
    # Centre the `s` glyph. We hand-tune the metrics because the SVG's
    # `Space Grotesk` isn't always installed.
    font_size = round(size * 0.7)
    font = _try_font(
        ["Space Grotesk", "Inter", "Arial", "Segoe UI"],
        font_size,
    )
    text = "s"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2 - bbox[0]
    # Visual centre — text baseline sits slightly low vs. geometric centre.
    y = (size - text_h) // 2 - bbox[1] - round(size * 0.04)
    draw.text((x, y), text, fill=ACCENT, font=font)
    return img


def make_og_card() -> Image.Image:
    """1200x630 share card — wordmark left, tagline + repo URL right."""
    W, H = 1200, 630
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # Subtle gradient: violet glow top-left → dark bottom-right.
    for y in range(H):
        # Linear fade from a soft violet tint at top → BG at bottom.
        ratio = y / H
        r = round(BG[0] * ratio + (BG[0] + 12) * (1 - ratio))
        g = round(BG[1] * ratio + (BG[1] + 4)  * (1 - ratio))
        b = round(BG[2] * ratio + (BG[2] + 20) * (1 - ratio))
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Big favicon left.
    fav = make_favicon(220)
    img.paste(fav, (80, (H - 220) // 2), fav)

    # Wordmark + tagline right of favicon.
    title_font = _try_font(["Space Grotesk", "Inter", "Arial Bold"], 84)
    tagline_font = _try_font(["Inter", "Segoe UI", "Arial"], 32)
    meta_font = _try_font(["JetBrains Mono", "Consolas", "Courier New"], 22)

    title_x = 360
    draw.text((title_x, 200), "subarr", fill=FG_0, font=title_font)
    draw.text((title_x, 320),
              "The queue + control layer\nsubgen never had.",
              fill=FG_2, font=tagline_font, spacing=8)
    draw.text((title_x, 480),
              "github.com/coaxk/subarr",
              fill=ACCENT, font=meta_font)

    return img


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 192, 512):
        path = OUTDIR / f"favicon-{size}.png"
        make_favicon(size).save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size})")
    og = OUTDIR / "og-card.png"
    make_og_card().save(og, "PNG", optimize=True)
    print(f"wrote {og.relative_to(ROOT)} (1200x630)")


if __name__ == "__main__":
    main()
