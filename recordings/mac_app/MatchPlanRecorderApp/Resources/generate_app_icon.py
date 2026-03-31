#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


SIZES = [
    16,
    32,
    64,
    128,
    256,
    512,
    1024,
]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(lerp(c1[0], c2[0], t)),
        int(lerp(c1[1], c2[1], t)),
        int(lerp(c1[2], c2[2], t)),
    )


def make_master_icon(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    top = (7, 18, 42)
    bottom = (16, 84, 92)
    accent = (90, 246, 182)
    line = (214, 255, 242)
    warning = (255, 97, 80)

    for y in range(size):
        t = y / max(size - 1, 1)
        color = blend(top, bottom, t)
        draw.line([(0, y), (size, y)], fill=color)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        [size * 0.08, size * 0.04, size * 0.92, size * 0.88],
        fill=(58, 190, 160, 110),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.08))
    image.alpha_composite(glow)

    pad = int(size * 0.08)
    radius = int(size * 0.22)

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)

    card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card)
    card_draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=int(size * 0.18),
        fill=(8, 26, 38, 190),
        outline=(170, 255, 233, 70),
        width=max(2, size // 90),
    )
    card = card.filter(ImageFilter.GaussianBlur(radius=size * 0.002))
    image.alpha_composite(card)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    cx = size * 0.5
    cy = size * 0.52

    radar_w = size * 0.56
    radar_h = size * 0.48
    for idx, alpha in enumerate((160, 125, 92)):
        inset = idx * size * 0.055
        od.arc(
            [
                cx - radar_w / 2 + inset,
                cy - radar_h / 2 + inset,
                cx + radar_w / 2 - inset,
                cy + radar_h / 2 - inset,
            ],
            start=204,
            end=336,
            fill=(*accent, alpha),
            width=max(6, size // 55),
        )

    field_w = size * 0.44
    field_h = size * 0.56
    left = cx - field_w / 2
    top_y = cy - field_h / 2
    right = cx + field_w / 2
    bottom_y = cy + field_h / 2
    stroke = max(8, size // 70)

    od.rounded_rectangle(
        [left, top_y, right, bottom_y],
        radius=int(size * 0.045),
        outline=(*line, 235),
        width=stroke,
    )
    od.line([(cx, top_y), (cx, bottom_y)], fill=(*line, 220), width=stroke)
    od.ellipse(
        [cx - size * 0.055, cy - size * 0.055, cx + size * 0.055, cy + size * 0.055],
        outline=(*line, 220),
        width=stroke,
    )
    box_w = field_w * 0.18
    box_h = field_h * 0.22
    od.rectangle([left, cy - box_h / 2, left + box_w, cy + box_h / 2], outline=(*line, 215), width=stroke)
    od.rectangle([right - box_w, cy - box_h / 2, right, cy + box_h / 2], outline=(*line, 215), width=stroke)

    banner_h = size * 0.14
    banner_y = top_y - size * 0.01
    od.rounded_rectangle(
        [left + size * 0.06, banner_y, right - size * 0.06, banner_y + banner_h],
        radius=int(size * 0.04),
        fill=(7, 22, 30, 220),
        outline=(180, 255, 241, 120),
        width=max(4, size // 120),
    )
    dot_r = size * 0.03
    dot_cx = left + size * 0.115
    dot_cy = banner_y + banner_h / 2
    od.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=warning,
    )

    bars_x = dot_cx + size * 0.09
    bar_bottom = banner_y + banner_h * 0.72
    bar_width = size * 0.025
    gap = size * 0.018
    heights = [0.22, 0.38, 0.55, 0.78]
    for idx, ratio in enumerate(heights):
        x0 = bars_x + idx * (bar_width + gap)
        x1 = x0 + bar_width
        y0 = bar_bottom - banner_h * ratio
        od.rounded_rectangle(
            [x0, y0, x1, bar_bottom],
            radius=bar_width / 2,
            fill=(*accent, 220),
        )

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=size * 0.0015))
    image.alpha_composite(overlay)
    image.putalpha(mask)
    return image


def write_iconset(output_dir: Path) -> None:
    iconset_dir = output_dir / "AppIcon.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)
    master = make_master_icon(1024)
    for size in SIZES:
        icon = master.resize((size, size), Image.Resampling.LANCZOS)
        base_path = iconset_dir / f"icon_{size}x{size}.png"
        icon.save(base_path)
        if size != 1024:
            icon2 = master.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
            icon2.save(iconset_dir / f"icon_{size}x{size}@2x.png")


def main() -> None:
    resources = Path(__file__).resolve().parent
    write_iconset(resources)


if __name__ == "__main__":
    main()
