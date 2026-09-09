"""Generate a phone-keyboard background with one face per key, labeled
with the actual QWERTY letter and 두벌식 한글 jamo printed on each key
(like a real keyboard skin sticker).
"""

import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(ROOT, "photos")
OUT_DIR = os.path.join(ROOT, "keyboard")
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

TEAL = (57, 197, 187)
BG = (11, 13, 17)
WHITE = (255, 255, 255)
MUTED = (205, 216, 224)

ORDER = [
    "01_miku.png", "02_teto.png", "03_rin.png", "07_len.png", "04_luka.png",
    "05_meiko.png", "06_kaito.png", "12_purple.png", "13_pinkhair.png",
    "10_blondepink.png", "09_bluebolt.png", "08_laser.png", "11_meme.jpg",
]

# (column start, span, korean jamo, english letter) for the two 10/9-wide
# letter rows, then the mixed third/fourth rows below.
ROW1 = [("ㅂ", "Q"), ("ㅈ", "W"), ("ㄷ", "E"), ("ㄱ", "R"), ("ㅅ", "T"),
        ("ㅛ", "Y"), ("ㅕ", "U"), ("ㅑ", "I"), ("ㅐ", "O"), ("ㅔ", "P")]
ROW2 = [("ㅁ", "A"), ("ㄴ", "S"), ("ㅇ", "D"), ("ㄹ", "F"), ("ㅎ", "G"),
        ("ㅗ", "H"), ("ㅓ", "J"), ("ㅏ", "K"), ("ㅣ", "L")]
ROW3_MID = [("ㅋ", "Z"), ("ㅌ", "X"), ("ㅊ", "C"), ("ㅍ", "V"), ("ㅠ", "B"),
            ("ㅜ", "N"), ("ㅡ", "M")]

_cache = {}
_font_cache = {}


def photo(name):
    if name not in _cache:
        _cache[name] = Image.open(os.path.join(PHOTOS, name)).convert("RGB")
    return _cache[name]


def font(size):
    size = max(1, int(size))
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


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


def key_image(names, w, h, radius, dim, border=True):
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


def label_letter_key(tile, kr, en):
    """Stamp small English top-left + big Korean jamo bottom, keycap-sticker style."""
    SSF = 3
    w, h = tile.size[0] * SSF, tile.size[1] * SSF
    big = tile.resize((w, h), Image.LANCZOS)
    d = ImageDraw.Draw(big)

    # bottom scrim so the jamo reads over any face brightness
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds = ImageDraw.Draw(scrim)
    ds.rectangle([0, h * 0.56, w, h], fill=(8, 10, 14, 150))
    big = Image.alpha_composite(big, scrim)
    d = ImageDraw.Draw(big)

    en_size = h * 0.20
    d.text((w * 0.11, h * 0.07), en, font=font(en_size), fill=MUTED + (235,))

    kr_size = h * 0.40
    kf = font(kr_size)
    bbox = d.textbbox((0, 0), kr, font=kf)
    tw = bbox[2] - bbox[0]
    d.text((w / 2 - tw / 2 - bbox[0], h * 0.60), kr, font=kf, fill=WHITE + (255,))

    return big.resize(tile.size, Image.LANCZOS)


def _dim_bottom(tile, ssf, frac=0.30, alpha=160):
    w, h = tile.size[0] * ssf, tile.size[1] * ssf
    big = tile.resize((w, h), Image.LANCZOS)
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rectangle([0, h * (1 - frac), w, h], fill=(8, 10, 14, alpha))
    return Image.alpha_composite(big, scrim), w, h


def label_special_key(tile, text, size_frac=0.30):
    SSF = 3
    big, w, h = _dim_bottom(tile, SSF)
    d = ImageDraw.Draw(big)

    fs = h * size_frac
    kf = font(fs)
    bbox = d.textbbox((0, 0), text, font=kf)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((w / 2 - tw / 2 - bbox[0], h * 0.66 - th / 2 - bbox[1]), text, font=kf, fill=WHITE + (255,))

    return big.resize(tile.size, Image.LANCZOS)


def label_icon_key(tile, icon):
    """Draw a keyboard icon (shift / backspace / enter) as vector shapes,
    since these glyphs aren't reliably in every font."""
    SSF = 3
    big, w, h = _dim_bottom(tile, SSF)
    d = ImageDraw.Draw(big)
    cx, cy = w / 2, h * 0.70
    s = min(w, h) * 0.30
    lw = max(2, int(s * 0.16))
    c = WHITE + (255,)

    if icon == "shift":
        d.polygon([(cx - s * 0.55, cy + s * 0.05), (cx, cy - s * 0.65), (cx + s * 0.55, cy + s * 0.05)],
                  outline=c, width=lw)
        d.rectangle([cx - s * 0.28, cy + s * 0.05, cx + s * 0.28, cy + s * 0.55], outline=c, width=lw)
    elif icon == "backspace":
        pts = [(cx - s * 0.75, cy), (cx - s * 0.30, cy - s * 0.45), (cx + s * 0.75, cy - s * 0.45),
               (cx + s * 0.75, cy + s * 0.45), (cx - s * 0.30, cy + s * 0.45)]
        d.polygon(pts, outline=c, width=lw)
        d.line([(cx - s * 0.02, cy - s * 0.20), (cx + s * 0.40, cy + s * 0.20)], fill=c, width=lw)
        d.line([(cx - s * 0.02, cy + s * 0.20), (cx + s * 0.40, cy - s * 0.20)], fill=c, width=lw)
    elif icon == "enter":
        d.line([(cx + s * 0.55, cy - s * 0.55), (cx + s * 0.55, cy + s * 0.05),
                (cx - s * 0.55, cy + s * 0.05)], fill=c, width=lw, joint="curve")
        d.polygon([(cx - s * 0.20, cy - s * 0.30), (cx - s * 0.20, cy + s * 0.40),
                   (cx - s * 0.70, cy + s * 0.05)], fill=c)

    return big.resize(tile.size, Image.LANCZOS)


def build(width=1440, height=1300, dim=0.42, path="keyboard_vocaloid.png"):
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

    def place(col_start, span, row, kr=None, en=None, label=None, icon=None):
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
        tile = key_image(names, w, h, radius, dim)
        if kr and en:
            tile = label_letter_key(tile, kr, en)
        elif icon:
            tile = label_icon_key(tile, icon)
        elif label:
            tile = label_special_key(tile, label)
        canvas.paste(tile, (int(x0), int(y0)), tile)

    # row 1 : ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔ  /  Q W E R T Y U I O P
    for c, (kr, en) in enumerate(ROW1):
        place(c, 1, 0, kr, en)

    # row 2 : ㅁㄴㅇㄹㅎㅗㅓㅏㅣ  /  A S D F G H J K L
    for c, (kr, en) in enumerate(ROW2):
        place(c + 0.5, 1, 1, kr, en)

    # row 3 : shift + ㅋㅌㅊㅍㅠㅜㅡ + backspace
    place(0, 1.5, 2, icon="shift")
    for c, (kr, en) in enumerate(ROW3_MID):
        place(1.5 + c, 1, 2, kr, en)
    place(8.5, 1.5, 2, icon="backspace")

    # row 4 : 123 / , / space / . / enter
    place(0, 1.5, 3, label="123")
    place(1.5, 1, 3, label=",")
    place(2.5, 5, 3, label="space")
    place(7.5, 1, 3, label=".")
    place(8.5, 1.5, 3, icon="enter")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, path)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print(f"  {path}  {width}x{height}  {os.path.getsize(out)//1024} KB  ({idx} keys)")
    return out


if __name__ == "__main__":
    build(1440, 1040, 0.42, "keyboard_vocaloid.png")
    build(1440, 1300, 0.42, "keyboard_vocaloid_tall.png")
    build(1440, 1040, 0.62, "keyboard_vocaloid_dark.png")
