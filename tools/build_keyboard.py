"""Generate a phone-keyboard background with one face per key.

The layout matches the QWERTY / 두벌식 한글 grid (10 / 9 / 7 + space row),
so the same image works for both the English and Korean keyboards.
"""

import os
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(ROOT, "photos")
OUT_DIR = os.path.join(ROOT, "keyboard")

TEAL = (57, 197, 187)
BG = (11, 13, 17)

ORDER = [
    "01_miku.png", "02_teto.png", "03_rin.png", "07_len.png", "04_luka.png",
    "05_meiko.png", "06_kaito.png", "12_purple.png", "13_pinkhair.png",
    "10_blondepink.png", "09_bluebolt.png", "08_laser.png", "11_meme.jpg",
]

_cache = {}


def photo(name):
    if name not in _cache:
        _cache[name] = Image.open(os.path.join(PHOTOS, name)).convert("RGB")
    return _cache[name]


def face(name, w, h):
    """Crop around the head at the requested aspect, resized to fill w x h."""
    src = photo(name)
    sw, sh = src.size

    # the head occupies roughly this box, centred a bit above the middle
    head_w, head_h = sw * 0.58, sh * 0.48
    cx, cy = sw * 0.50, sh * 0.36

    scale = max(head_w / w, head_h / h)
    bw, bh = w * scale, h * scale
    if bw > sw:
        bw, bh = sw, sw * h / w
    if bh > sh:
        bh, bw = sh, sh * w / h

    left = min(max(cx - bw / 2, 0), sw - bw)
    top = min(max(cy - bh / 2, 0), sh - bh)
    crop = src.crop((int(left), int(top), int(left + bw), int(top + bh)))
    return crop.resize((w, h), Image.LANCZOS)


def key(names, w, h, radius, dim, border=True):
    """One key tile: dimmed face(s), rounded corners, teal hairline.

    A wide key (space bar) is filled with a strip of faces instead of one
    stretched crop, so every key still reads as faces.
    """
    SSF = 3
    W, H = w * SSF, h * SSF

    n = max(1, min(len(names), round(W / H)))
    big = Image.new("RGB", (W, H))
    edges = [round(W * i / n) for i in range(n + 1)]
    for i in range(n):
        seg = edges[i + 1] - edges[i]
        big.paste(face(names[i % len(names)], seg, H), (edges[i], 0))
    big = big.convert("RGBA")

    scrim = Image.new("RGBA", big.size, (0, 0, 0, int(255 * dim)))
    big = Image.alpha_composite(big, scrim)

    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, big.size[0] - 1, big.size[1] - 1], radius=radius * SSF, fill=255)

    tile = Image.new("RGBA", big.size, (0, 0, 0, 0))
    tile.paste(big, (0, 0), mask)

    if border:
        d = ImageDraw.Draw(tile)
        bw = max(2, int(1.6 * SSF))
        d.rounded_rectangle([bw // 2, bw // 2, big.size[0] - 1 - bw // 2, big.size[1] - 1 - bw // 2],
                            radius=radius * SSF, outline=TEAL + (140,), width=bw)
    return tile.resize((w, h), Image.LANCZOS)


def build(width=1440, height=1040, dim=0.42, path="keyboard_vocaloid.png"):
    canvas = Image.new("RGBA", (width, height), BG + (255,))

    # subtle backdrop so gaps between keys are not flat black
    back = face(ORDER[0], width, height).filter(ImageFilter.GaussianBlur(width * 0.05))
    back = Image.blend(Image.new("RGB", (width, height), BG), back, 0.25)
    canvas.paste(back.convert("RGBA"), (0, 0))

    rows = 4
    row_h = height / rows
    unit = width / 10.0          # one key column
    pad_x = unit * 0.055
    pad_y = row_h * 0.10
    radius = int(unit * 0.13)

    idx = 0

    def place(col_start, span, row):
        nonlocal idx
        x0 = col_start * unit + pad_x
        x1 = (col_start + span) * unit - pad_x
        y0 = row * row_h + pad_y
        y1 = (row + 1) * row_h - pad_y
        w, h = int(x1 - x0), int(y1 - y0)
        if w <= 0 or h <= 0:
            return
        slots = max(1, round(w / h))
        names = [ORDER[(idx + i) % len(ORDER)] for i in range(slots)]
        idx += slots
        tile = key(names, w, h, radius, dim)
        canvas.paste(tile, (int(x0), int(y0)), tile)

    # row 1 : ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ  /  Q W E R T Y U I O P
    for c in range(10):
        place(c, 1, 0)

    # row 2 : ㅁㄴㅇㄹㅎㅗㅓㅏㅣ  /  A S D F G H J K L
    for c in range(9):
        place(c + 0.5, 1, 1)

    # row 3 : shift + ㅋㅌㅊㅍㅠㅜㅡ + backspace
    place(0, 1.5, 2)
    for c in range(7):
        place(1.5 + c, 1, 2)
    place(8.5, 1.5, 2)

    # row 4 : 123 / , / space / . / enter
    place(0, 1.5, 3)
    place(1.5, 1, 3)
    place(2.5, 5, 3)
    place(7.5, 1, 3)
    place(8.5, 1.5, 3)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, path)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print(f"  {path}  {width}x{height}  {os.path.getsize(out)//1024} KB  ({idx} keys)")
    return out


if __name__ == "__main__":
    build(1440, 1040, 0.42, "keyboard_vocaloid.png")
    build(1440, 1300, 0.42, "keyboard_vocaloid_tall.png")
    build(1440, 1040, 0.62, "keyboard_vocaloid_dark.png")
