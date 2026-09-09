"""Generate KakaoTalk theme image assets from the cosplay photo set."""

import math
import os
from PIL import Image, ImageDraw, ImageFilter, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(ROOT, "photos")
IOS_IMAGES = os.path.join(ROOT, "ios", "src", "Images")
AND_IMAGES = os.path.join(ROOT, "android", "images")
DOCS = os.path.join(ROOT, "docs", "assets")

BG = (17, 19, 24)
PANEL = (23, 27, 35)
TEAL = (57, 197, 187)
TEAL_HI = (127, 227, 220)
PINK = (255, 111, 165)
YELLOW = (255, 210, 74)
TEXT = (237, 243, 245)
MUTED = (124, 139, 153)

SS = 4  # supersample factor for drawn shapes

ORDER = [
    "01_miku.png", "04_luka.png", "02_teto.png",
    "07_len.png", "13_pinkhair.png", "03_rin.png",
    "06_kaito.png", "05_meiko.png", "12_purple.png",
    "09_bluebolt.png", "10_blondepink.png", "08_laser.png",
    "11_meme.jpg",
]


def load(name):
    return Image.open(os.path.join(PHOTOS, name)).convert("RGB")


def cover(img, w, h, focus=0.42):
    """Resize+crop to exactly w x h, biased toward the upper part (faces)."""
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = max(w, int(round(sw * scale))), max(h, int(round(sh * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * focus)
    return img.crop((left, top, left + w, top + h))


def linear_gradient(w, h, top, bottom):
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(round(top[i] + (bottom[i] - top[i]) * t)) for i in range(3))
    return grad.resize((w, h), Image.BILINEAR)


def radial_glow(w, h, cx, cy, radius, color, strength):
    """Additive soft glow layer."""
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 26
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius * t
        a = strength * (1 - t) ** 2
        col = tuple(int(round(c * a)) for c in color)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.12))


def mosaic(w, h, cols, names, focus=0.40):
    """Grid collage covering w x h."""
    cw = w // cols
    ch = int(round(cw * 1505 / 1045))
    rows = math.ceil(h / ch)
    canvas = Image.new("RGB", (cw * cols, ch * rows), BG)
    i = 0
    for r in range(rows):
        for c in range(cols):
            name = names[i % len(names)]
            i += 1
            tile = cover(load(name), cw, ch, focus)
            canvas.paste(tile, (c * cw, r * ch))
    return canvas.resize((w, h), Image.LANCZOS) if canvas.size != (w, h) else canvas


def darken(img, amount):
    """Multiply toward black. amount 0..1 = how much brightness is kept."""
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.blend(black, img, amount)


def tint(img, color, amount):
    layer = Image.new("RGB", img.size, color)
    return Image.blend(img, layer, amount)


def edge_fade(img, top_frac=0.16, bottom_frac=0.20, strength=0.85):
    """Darken the top and bottom bands so header / input bar stay legible."""
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    px = mask.load()
    top_px = int(h * top_frac)
    bot_px = int(h * bottom_frac)
    for y in range(top_px):
        v = int(255 * (1 - strength * (1 - y / max(1, top_px)) ** 1.6))
        for x in range(w):
            px[x, y] = v
    for i in range(bot_px):
        y = h - 1 - i
        v = int(255 * (1 - strength * (1 - i / max(1, bot_px)) ** 1.6))
        for x in range(w):
            px[x, y] = v
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(img, black, mask)


def vignette(img, strength=0.55):
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    steps = 40
    for i in range(steps):
        t = i / steps
        inset_x = w * 0.5 * t
        inset_y = h * 0.5 * t
        d.ellipse([inset_x - w * 0.15, inset_y - h * 0.10,
                   w - inset_x + w * 0.15, h - inset_y + h * 0.10],
                  fill=int(255 * (1 - t)))
    mask = mask.filter(ImageFilter.GaussianBlur(w * 0.05))
    black = Image.new("RGB", (w, h), (0, 0, 0))
    faded = Image.blend(black, img, 1 - strength)
    return Image.composite(img, faded, mask)


def save_ios(img, name):
    path = os.path.join(IOS_IMAGES, name)
    img.save(path, "PNG", optimize=True)
    return path


# --------------------------------------------------------------------------
# Backgrounds
# --------------------------------------------------------------------------

def build_chatroom_bg(w=1290, h=2796):
    base = mosaic(w, h, 3, ORDER, focus=0.34)
    base = darken(base, 0.46)
    base = tint(base, (24, 38, 54), 0.26)

    grad = linear_gradient(w, h, (10, 14, 22), (16, 26, 34))
    base = Image.blend(base, grad, 0.24)

    glow = radial_glow(w, h, w * 0.18, h * 0.10, w * 0.85, TEAL, 0.16)
    base = ImageChops.add(base, glow)
    glow2 = radial_glow(w, h, w * 0.86, h * 0.92, w * 0.75, PINK, 0.12)
    base = ImageChops.add(base, glow2)

    base = vignette(base, 0.30)
    base = edge_fade(base, 0.15, 0.18, 0.88)
    return base.convert("RGBA")


def build_main_bg(w=1290, h=2796):
    base = mosaic(w, h, 4, ORDER, focus=0.32)
    base = base.filter(ImageFilter.GaussianBlur(w * 0.006))
    base = darken(base, 0.40)
    base = tint(base, (16, 22, 32), 0.32)

    grad = linear_gradient(w, h, (12, 15, 21), (18, 22, 30))
    base = Image.blend(base, grad, 0.30)

    glow = radial_glow(w, h, w * 0.5, h * 0.02, w * 1.0, TEAL, 0.13)
    base = ImageChops.add(base, glow)
    base = vignette(base, 0.28)
    base = edge_fade(base, 0.13, 0.14, 0.80)
    return base.convert("RGBA")


def build_passcode_bg(size=1202):
    base = mosaic(size, size, 3, ORDER[:9], focus=0.36)
    base = darken(base, 0.30)
    base = tint(base, (18, 28, 40), 0.40)
    glow = radial_glow(size, size, size * 0.5, size * 0.42, size * 0.75, TEAL, 0.18)
    base = ImageChops.add(base, glow)
    base = vignette(base, 0.50)
    return base.convert("RGBA")


def build_tabbar_bg(w, h):
    img = Image.new("RGBA", (w, h), BG + (255,))
    grad = linear_gradient(w, h, (20, 24, 32), (13, 15, 20)).convert("RGBA")
    img = Image.alpha_composite(img, grad)
    d = ImageDraw.Draw(img)
    line = max(2, h // 40)
    for x in range(w):
        t = x / max(1, w - 1)
        mix = math.sin(t * math.pi)
        col = tuple(int(round(TEAL[i] * (0.35 + 0.65 * mix) + PINK[i] * 0.12 * mix)) for i in range(3))
        d.rectangle([x, 0, x, line], fill=col + (255,))
    return img


# --------------------------------------------------------------------------
# Chat bubbles
# --------------------------------------------------------------------------

def bubble(w, h, color, radius_pt=14, scale=2):
    r = int(round(radius_pt * scale))
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=r * SS, fill=color + (255,))
    return img.resize((w, h), Image.LANCZOS)


# --------------------------------------------------------------------------
# Tab-bar icons
# --------------------------------------------------------------------------

def _icon_canvas(box):
    img = Image.new("RGBA", (box * SS, box * SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def icon_friends(box, color):
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    # back person
    d.ellipse([S * 0.50, S * 0.10, S * 0.84, S * 0.44], fill=c)
    d.pieslice([S * 0.44, S * 0.46, S * 0.98, S * 1.00], 180, 360, fill=c)
    # front person (knocked out with a gap)
    g = (0, 0, 0, 0)
    d.ellipse([S * 0.10, S * 0.06, S * 0.52, S * 0.48], fill=g)
    d.pieslice([S * 0.00, S * 0.44, S * 0.62, S * 1.06], 180, 360, fill=g)
    d.ellipse([S * 0.14, S * 0.10, S * 0.48, S * 0.44], fill=c)
    d.pieslice([S * 0.04, S * 0.48, S * 0.58, S * 1.02], 180, 360, fill=c)
    return img.resize((box, box), Image.LANCZOS)


def icon_chats(box, color):
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    d.rounded_rectangle([S * 0.06, S * 0.12, S * 0.94, S * 0.76], radius=S * 0.22, fill=c)
    d.polygon([(S * 0.24, S * 0.72), (S * 0.44, S * 0.72), (S * 0.24, S * 0.96)], fill=c)
    return img.resize((box, box), Image.LANCZOS)


def icon_browse(box, color):
    """Open-chat style: filled speech bubble with a knocked-out hash."""
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    d.rounded_rectangle([S * 0.06, S * 0.12, S * 0.94, S * 0.76], radius=S * 0.22, fill=c)
    d.polygon([(S * 0.24, S * 0.72), (S * 0.44, S * 0.72), (S * 0.24, S * 0.96)], fill=c)

    gap = (0, 0, 0, 0)
    lw = int(S * 0.085)
    for x in (0.42, 0.60):
        d.line([(S * (x + 0.05), S * 0.24), (S * (x - 0.05), S * 0.64)], fill=gap, width=lw)
    for y in (0.35, 0.53):
        d.line([(S * 0.24, S * y), (S * 0.76, S * y)], fill=gap, width=lw)
    return img.resize((box, box), Image.LANCZOS)


def icon_find(box, color):
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    lw = int(S * 0.10)
    d.ellipse([S * 0.10, S * 0.10, S * 0.72, S * 0.72], outline=c, width=lw)
    d.line([(S * 0.66, S * 0.66), (S * 0.92, S * 0.92)], fill=c, width=int(lw * 1.15))
    return img.resize((box, box), Image.LANCZOS)


def icon_game(box, color):
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    d.rounded_rectangle([S * 0.04, S * 0.26, S * 0.96, S * 0.80], radius=S * 0.22, fill=c)
    g = (0, 0, 0, 0)
    lw = int(S * 0.075)
    d.line([(S * 0.18, S * 0.53), (S * 0.38, S * 0.53)], fill=g, width=lw)
    d.line([(S * 0.28, S * 0.43), (S * 0.28, S * 0.63)], fill=g, width=lw)
    d.ellipse([S * 0.62, S * 0.40, S * 0.74, S * 0.52], fill=g)
    d.ellipse([S * 0.76, S * 0.54, S * 0.88, S * 0.66], fill=g)
    return img.resize((box, box), Image.LANCZOS)


def icon_more(box, color):
    img, d = _icon_canvas(box)
    S = box * SS
    c = color + (255,)
    h = S * 0.115
    for y in (0.20, 0.44, 0.68):
        d.rounded_rectangle([S * 0.08, S * y, S * 0.92, S * y + h], radius=h / 2, fill=c)
    return img.resize((box, box), Image.LANCZOS)


ICONS = {
    "Friends": icon_friends,
    "Chats": icon_chats,
    "Browse": icon_browse,
    "Find": icon_find,
    "Game": icon_game,
    "More": icon_more,
}

# reference sizes taken from an official-format theme (@2x, @3x)
ICON_SIZES = {
    "Friends": ((70, 75), (107, 115)),
    "Chats": ((75, 75), (115, 115)),
    "Browse": ((75, 62), (115, 95)),
    "Find": ((75, 75), (115, 115)),
    "Game": ((75, 70), (115, 107)),
    "More": ((75, 69), (115, 106)),
}


def fit_into(icon, w, h):
    """Center an square icon inside a w x h transparent canvas."""
    box = min(w, h)
    icon = icon.resize((box, box), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(icon, ((w - box) // 2, (h - box) // 2), icon)
    return out


# --------------------------------------------------------------------------
# Profile / thumbnail / misc
# --------------------------------------------------------------------------

def build_profile(size=361, name="01_miku.png"):
    img = cover(load(name), size, size, focus=0.18).convert("RGBA")
    img = tint(img.convert("RGB"), (18, 30, 42), 0.12).convert("RGBA")
    mask = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 2 - 1, size * 2 - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def build_theme_icon(size=162):
    """Theme thumbnail: 2x2 face collage in a rounded square with a teal frame."""
    big = size * SS
    picks = ["01_miku.png", "02_teto.png", "07_len.png", "04_luka.png"]
    half = big // 2
    coll = Image.new("RGB", (big, big), BG)
    for i, name in enumerate(picks):
        tile = cover(load(name), half, half, focus=0.16)
        coll.paste(tile, ((i % 2) * half, (i // 2) * half))
    coll = tint(coll, (16, 26, 36), 0.16)

    glow = radial_glow(big, big, big * 0.5, big * 0.5, big * 0.8, TEAL, 0.10)
    coll = ImageChops.add(coll, glow)

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.24, fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(coll.convert("RGBA"), (0, 0), mask)

    d = ImageDraw.Draw(out)
    w = int(big * 0.035)
    d.rounded_rectangle([w // 2, w // 2, big - 1 - w // 2, big - 1 - w // 2],
                        radius=big * 0.23, outline=TEAL + (255,), width=w)
    return out.resize((size, size), Image.LANCZOS)


def build_add_friend(w, h):
    """Person-plus glyph on a rounded teal-outlined tile."""
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    W, H = w * SS, h * SS
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=W * 0.26, fill=(26, 34, 44, 255),
                        outline=TEAL + (255,), width=int(W * 0.05))
    c = TEAL + (255,)
    d.ellipse([W * 0.24, H * 0.20, W * 0.58, H * 0.51], fill=c)
    d.pieslice([W * 0.14, H * 0.48, W * 0.68, H * 0.99], 180, 360, fill=c)
    lw = int(W * 0.085)
    d.line([(W * 0.66, H * 0.68), (W * 0.90, H * 0.68)], fill=c, width=lw)
    d.line([(W * 0.78, H * 0.56), (W * 0.78, H * 0.80)], fill=c, width=lw)
    return img.resize((w, h), Image.LANCZOS)


def build_passcode_bullet(size, filled):
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = size * SS
    pad = S * 0.16
    if filled:
        d.ellipse([pad, pad, S - pad, S - pad], fill=TEAL + (255,))
    else:
        d.ellipse([pad, pad, S - pad, S - pad], outline=(140, 160, 175, 255), width=int(S * 0.09))
    return img.resize((size, size), Image.LANCZOS)


def build_keypad_pressed(size=180):
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = size * SS
    d.ellipse([0, 0, S - 1, S - 1], fill=(57, 197, 187, 90))
    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------

def main():
    for p in (IOS_IMAGES, AND_IMAGES, DOCS):
        os.makedirs(p, exist_ok=True)

    print("backgrounds...")
    chat_bg = build_chatroom_bg()
    save_ios(chat_bg, "chatroomBgImage@3x.png")
    main_bg = build_main_bg()
    save_ios(main_bg, "mainBgImage@3x.png")
    pass_bg = build_passcode_bg()
    save_ios(pass_bg, "passcodeBgImage@3x.png")

    save_ios(build_tabbar_bg(942, 100), "maintabBgImage@2x.png")
    save_ios(build_tabbar_bg(1413, 150), "maintabBgImage@3x.png")

    print("bubbles...")
    specs = {
        "chatroomBubbleSend01": (TEAL, False),
        "chatroomBubbleSend01Selected": (TEAL_HI, False),
        "chatroomBubbleSend02": (TEAL, False),
        "chatroomBubbleSend02Selected": (TEAL_HI, False),
        "chatroomBubbleReceive01": ((42, 49, 61), False),
        "chatroomBubbleReceive01Selected": ((58, 67, 82), False),
        "chatroomBubbleReceive02": ((42, 49, 61), False),
        "chatroomBubbleReceive02Selected": ((58, 67, 82), False),
    }
    for name, (color, _) in specs.items():
        save_ios(bubble(80, 70, color, 14, 2), f"{name}@2x.png")
        save_ios(bubble(120, 105, color, 14, 3), f"{name}@3x.png")

    print("tab icons...")
    for key, fn in ICONS.items():
        (w2, h2), (w3, h3) = ICON_SIZES[key]
        for state, color in (("", MUTED), ("Selected", TEAL)):
            base = fn(256, color)
            save_ios(fit_into(base, w2, h2), f"maintabIco{key}{state}@2x.png")
            save_ios(fit_into(base, w3, h3), f"maintabIco{key}{state}@3x.png")

    print("misc...")
    save_ios(build_profile(361), "profileImg01@3x.png")
    save_ios(build_theme_icon(162), "commonIcoTheme.png")
    save_ios(build_add_friend(78, 85), "findBtnAddFriend@2x.png")
    save_ios(build_add_friend(115, 125), "findBtnAddFriend@3x.png")

    for i in range(1, 5):
        save_ios(build_passcode_bullet(76, False), f"passcodeImgCode0{i}@3x.png")
        save_ios(build_passcode_bullet(76, True), f"passcodeImgCode0{i}Selected@3x.png")
    save_ios(build_keypad_pressed(180), "passcodeKeypadPressed@3x.png")

    print("android exports (Galaxy 1440x3120)...")
    build_chatroom_bg(1440, 3120).convert("RGB").save(
        os.path.join(AND_IMAGES, "chatroom_background.png"), "PNG", optimize=True)
    build_main_bg(1440, 3120).convert("RGB").save(
        os.path.join(AND_IMAGES, "wallpaper.png"), "PNG", optimize=True)
    build_theme_icon(512).save(os.path.join(AND_IMAGES, "theme_icon.png"), "PNG", optimize=True)

    n = len(os.listdir(IOS_IMAGES))
    print(f"done: {n} iOS images")


if __name__ == "__main__":
    main()
