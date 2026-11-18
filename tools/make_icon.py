"""Dev only: draw SpaceMap's icon with Pillow (no runtime dependency)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

INDIGO = (99, 102, 241)
INDIGO_DARK = (67, 56, 202)
WHITE = (255, 255, 255)
TINTS = [(255, 255, 255), (224, 231, 255), (199, 210, 254), (165, 180, 252)]


def draw(size: int) -> Image.Image:
    scale = 8
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=INDIGO)
    # subtle inner border so the icon still reads on a white taskbar
    d.rounded_rectangle([int(s * 0.035)] * 2 + [s - int(s * 0.035)] * 2,
                        radius=int(s * 0.19), outline=INDIGO_DARK, width=int(s * 0.012))

    # treemap: one tall block on the left, two stacked on the right,
    # and a strip of small blocks along the bottom
    gap = int(s * 0.018)
    left = int(s * 0.16)
    top = int(s * 0.16)
    right = int(s * 0.84)
    bottom = int(s * 0.84)
    split = left + int((right - left) * 0.56)
    split_y = top + int((bottom - top) * 0.46)
    strip_y = bottom - int((bottom - top) * 0.20)

    d.rectangle([left, top, split - gap, strip_y - gap], fill=TINTS[0])
    d.rectangle([split + gap, top, right, split_y - gap], fill=TINTS[1])
    d.rectangle([split + gap, split_y + gap, right, strip_y - gap], fill=TINTS[2])

    width = (right - left - gap * 3) // 3
    for index in range(3):
        x0 = left + index * (width + gap)
        d.rectangle([x0, strip_y + gap, x0 + width, bottom],
                    fill=TINTS[3] if index == 1 else TINTS[0])

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    master = draw(1024)
    master.save(assets / "spacemap.ico",
                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    (root / "docs").mkdir(exist_ok=True)
    master.resize((256, 256), Image.LANCZOS).save(root / "docs" / "icon.png")
    print("icon written to assets/spacemap.ico and docs/icon.png")


if __name__ == "__main__":
    main()
