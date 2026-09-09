"""Build a real, installable KakaoTalk Android theme APK.

Takes the user-supplied working theme APK as a template (its resource
*names*, AndroidManifest.xml and APK Signing Block layout are exactly what
KakaoTalk's theme loader expects — see README for how that contract was
reverse engineered), swaps every themeable image for Vocaloid-cosplay art,
recolors the two 9-patch chat bubbles and the tab-cell highlight in place
(preserving their compiled 9-patch chunks byte-for-byte), patches the 54
named colors in resources.arsc in place, then re-zips, zipaligns and signs
the result with a fresh key using APK Signature Scheme v2 (vendored pure
python implementation in tools/vendor/apk_v2_signer.py).

Run with the androguard-enabled interpreter, e.g.:
    <venv>/bin/python3 tools/build_android_theme_apk.py <template.apk> <out.apk>
"""

import io
import math
import os
import struct
import sys
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(ROOT, "photos")
sys.path.insert(0, os.path.join(ROOT, "tools", "vendor"))

from apk_v2_signer import APKSigner  # noqa: E402

from loguru import logger as _loguru_logger  # noqa: E402
_loguru_logger.remove()
from androguard.core.axml import ARSCParser, TYPE_INT_COLOR_ARGB8  # noqa: E402
from androguard.core.apk import APK  # noqa: E402

# --------------------------------------------------------------------------
# palette (matches ios/src/KakaoTalkTheme.css)
# --------------------------------------------------------------------------

BG = (17, 19, 24)          # #111318
PANEL = (21, 25, 34)       # #151922
PANEL2 = (23, 27, 35)      # #171b23
TEAL = (57, 197, 187)      # #39c5bb
TEAL_HI = (127, 227, 220)  # #7fe3dc
PINK = (255, 111, 165)     # #ff6fa5
YELLOW = (255, 210, 74)    # #ffd24a
TEXT = (237, 243, 245)     # #edf3f5
MUTED = (143, 160, 174)    # #8fa0ae

ORDER = [
    "01_miku.png", "04_luka.png", "02_teto.png",
    "07_len.png", "13_pinkhair.png", "03_rin.png",
    "06_kaito.png", "05_meiko.png", "12_purple.png",
    "09_bluebolt.png", "10_blondepink.png", "08_laser.png",
    "11_meme.jpg",
]

_photo_cache = {}


def _load(name):
    if name not in _photo_cache:
        _photo_cache[name] = Image.open(os.path.join(PHOTOS, name)).convert("RGB")
    return _photo_cache[name]


def cover(img, w, h, focus=0.30):
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = max(w, round(sw * scale)), max(h, round(sh * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * focus)
    return img.crop((left, top, left + w, top + h))


def mosaic(w, h, cols, focus=0.30):
    cw = max(1, w // cols)
    ch = round(cw * 1505 / 1045)
    rows = math.ceil(h / ch) or 1
    canvas = Image.new("RGB", (cw * cols, ch * rows), BG)
    i = 0
    for r in range(rows):
        for c in range(cols):
            name = ORDER[i % len(ORDER)]
            i += 1
            canvas.paste(cover(_load(name), cw, ch, focus), (c * cw, r * ch))
    return canvas.resize((w, h), Image.LANCZOS) if canvas.size != (w, h) else canvas


def linear_gradient(w, h, top, bottom):
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad.resize((w, h), Image.BILINEAR)


def darken(img, keep):
    return Image.blend(Image.new("RGB", img.size, (0, 0, 0)), img, keep)


def tint(img, color, amount):
    return Image.blend(img, Image.new("RGB", img.size, color), amount)


def radial_glow(w, h, cx, cy, radius, color, strength):
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(24, 0, -1):
        t = i / 24
        r = radius * t
        a = strength * (1 - t) ** 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=tuple(round(c * a) for c in color))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.12))


def edge_fade(img, top_frac, bottom_frac, strength):
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    px = mask.load()
    tp, bp = int(h * top_frac), int(h * bottom_frac)
    for y in range(tp):
        v = int(255 * (1 - strength * (1 - y / max(1, tp)) ** 1.6))
        for x in range(w):
            px[x, y] = v
    for i in range(bp):
        y = h - 1 - i
        v = int(255 * (1 - strength * (1 - i / max(1, bp)) ** 1.6))
        for x in range(w):
            px[x, y] = v
    return Image.composite(img, Image.new("RGB", (w, h), (0, 0, 0)), mask)


def themed_background(w, h, glow_color=TEAL, cols=3, focus=0.30, dim=0.46,
                       tint_color=(24, 38, 54), tint_amt=0.26, fade=True):
    base = mosaic(w, h, cols, focus)
    base = darken(base, 1 - dim)
    base = tint(base, tint_color, tint_amt)
    grad = linear_gradient(w, h, (10, 14, 22), (16, 26, 34))
    base = Image.blend(base, grad, 0.25)
    glow = radial_glow(w, h, w * 0.2, h * 0.1, w * 0.85, glow_color, 0.15)
    base = ImageChops.add(base, glow)
    if fade:
        base = edge_fade(base, 0.12, 0.14, 0.75)
    return base


def circle_mask(im, size):
    im = cover(im, size, size, 0.16)
    m = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    m = m.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), m)
    return out


# --------------------------------------------------------------------------
# tab icons
# --------------------------------------------------------------------------

def _icon(box):
    img = Image.new("RGBA", (box * 4, box * 4), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def icon_friends(box, color):
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    d.ellipse([S * 0.50, S * 0.10, S * 0.84, S * 0.44], fill=c)
    d.pieslice([S * 0.44, S * 0.46, S * 0.98, S * 1.00], 180, 360, fill=c)
    g = (0, 0, 0, 0)
    d.ellipse([S * 0.10, S * 0.06, S * 0.52, S * 0.48], fill=g)
    d.pieslice([S * 0.00, S * 0.44, S * 0.62, S * 1.06], 180, 360, fill=g)
    d.ellipse([S * 0.14, S * 0.10, S * 0.48, S * 0.44], fill=c)
    d.pieslice([S * 0.04, S * 0.48, S * 0.58, S * 1.02], 180, 360, fill=c)
    return img.resize((box, box), Image.LANCZOS)


def icon_chats(box, color):
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    d.rounded_rectangle([S * 0.06, S * 0.12, S * 0.94, S * 0.76], radius=S * 0.22, fill=c)
    d.polygon([(S * 0.24, S * 0.72), (S * 0.44, S * 0.72), (S * 0.24, S * 0.96)], fill=c)
    return img.resize((box, box), Image.LANCZOS)


def icon_more(box, color):
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    h = S * 0.115
    for y in (0.20, 0.44, 0.68):
        d.rounded_rectangle([S * 0.08, S * y, S * 0.92, S * y + h], radius=h / 2, fill=c)
    return img.resize((box, box), Image.LANCZOS)


def icon_now(box, color):
    """'지금' tab: a small pulse / compass-ish dot-in-ring glyph."""
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    lw = int(S * 0.09)
    d.ellipse([S * 0.14, S * 0.14, S * 0.86, S * 0.86], outline=c, width=lw)
    d.ellipse([S * 0.42, S * 0.42, S * 0.58, S * 0.58], fill=c)
    d.line([(S * 0.5, S * 0.02), (S * 0.5, S * 0.14)], fill=c, width=lw)
    return img.resize((box, box), Image.LANCZOS)


def icon_piccoma(box, color):
    """Webtoon/comics tab: a small open-book glyph."""
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    lw = int(S * 0.075)
    d.line([(S * 0.5, S * 0.16), (S * 0.5, S * 0.86)], fill=c, width=lw)
    d.rounded_rectangle([S * 0.10, S * 0.16, S * 0.50, S * 0.82], radius=S * 0.05, outline=c, width=lw)
    d.rounded_rectangle([S * 0.50, S * 0.16, S * 0.90, S * 0.82], radius=S * 0.05, outline=c, width=lw)
    return img.resize((box, box), Image.LANCZOS)


def icon_shopping(box, color):
    """Shopping tab: a simple bag glyph."""
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    lw = int(S * 0.08)
    d.rounded_rectangle([S * 0.16, S * 0.32, S * 0.84, S * 0.90], radius=S * 0.06, outline=c, width=lw)
    d.arc([S * 0.30, S * 0.10, S * 0.70, S * 0.50], 180, 360, fill=c, width=lw)
    return img.resize((box, box), Image.LANCZOS)


def icon_call(box, color):
    """Call tab: a classic phone-handset glyph."""
    img, d = _icon(box)
    S = box * 4
    c = color + (255,)
    d.rounded_rectangle([S * 0.18, S * 0.08, S * 0.86, S * 0.50], radius=S * 0.24, fill=c)
    d.rounded_rectangle([S * 0.14, S * 0.46, S * 0.60, S * 0.92], radius=S * 0.22, fill=c)
    g = (0, 0, 0, 0)
    d.ellipse([S * 0.30, S * 0.58, S * 0.46, S * 0.74], fill=g)
    return img.resize((box, box), Image.LANCZOS)


TAB_ICONS = {
    "friends": icon_friends,
    "chats": icon_chats,
    "more": icon_more,
    "now": icon_now,
    "piccoma": icon_piccoma,
    "shopping": icon_shopping,
    "call": icon_call,
}


def fit_square(icon, box):
    return icon.resize((box, box), Image.LANCZOS)


# --------------------------------------------------------------------------
# misc small assets
# --------------------------------------------------------------------------

def build_profile(size):
    img = circle_mask(_load("01_miku.png"), size)
    rgb = tint(img.convert("RGB"), (18, 30, 42), 0.12)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(rgb, (0, 0), img.split()[-1])
    return out


def build_add_friend_button(w, h, pressed=False):
    SS = 4
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = TEAL_HI if pressed else TEAL
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=W * 0.22, fill=bg + (255,))
    c = (7, 39, 43, 255)
    d.ellipse([W * 0.26, H * 0.18, W * 0.60, H * 0.50], fill=c)
    d.pieslice([W * 0.16, H * 0.46, W * 0.70, H * 0.98], 180, 360, fill=c)
    lw = int(W * 0.09)
    d.line([(W * 0.68, H * 0.66), (W * 0.92, H * 0.66)], fill=c, width=lw)
    d.line([(W * 0.80, H * 0.54), (W * 0.80, H * 0.78)], fill=c, width=lw)
    return img.resize((w, h), Image.LANCZOS)


def build_passcode_dot(size, filled):
    SS = 4
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = S * 0.14
    if filled:
        d.ellipse([pad, pad, S - pad, S - pad], fill=TEAL + (255,))
    else:
        d.ellipse([pad, pad, S - pad, S - pad], outline=MUTED + (255,), width=int(S * 0.08))
    return img.resize((size, size), Image.LANCZOS)


def build_splash(w, h):
    base = themed_background(w, h, glow_color=TEAL, cols=4, focus=0.28, dim=0.42,
                              tint_color=(18, 24, 34), tint_amt=0.30, fade=False)
    return base


# --------------------------------------------------------------------------
# 9-patch surgical recolor (preserves IHDR/npOl/npTc/IEND byte-for-byte)
# --------------------------------------------------------------------------

def _png_chunks(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    off = 8
    chunks = []
    while off < len(data):
        length = struct.unpack(">I", data[off:off + 4])[0]
        ctype = data[off + 4:off + 8]
        payload = data[off + 8:off + 8 + length]
        crc = data[off + 8 + length:off + 12 + length]
        chunks.append((ctype, payload, crc))
        off += 12 + length
        if ctype == b"IEND":
            break
    return chunks


def _make_chunk(ctype, payload):
    import zlib as _zlib
    length = struct.pack(">I", len(payload))
    crc = struct.pack(">I", _zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    return length + ctype + payload + crc


def _encode_idat(rgba_bytes, w, h, has_alpha):
    """Re-encode raw RGBA/RGB scanlines as a single IDAT (filter type 0)."""
    import zlib as _zlib
    bpp = 4 if has_alpha else 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: None
        row = rgba_bytes[y * w * bpp:(y + 1) * w * bpp]
        raw.extend(row)
    return _zlib.compress(bytes(raw), 9)


NP_NO_COLOR = 0x00000001
NP_TRANSPARENT = 0x00000000
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def _parse_nptc(payload):
    """npTc is big-endian. Returns (header_dict, xdivs, ydivs, colors)."""
    n_x, n_y, n_c = payload[1], payload[2], payload[3]
    base = 32
    xd = list(struct.unpack(">%di" % n_x, payload[base:base + 4 * n_x]))
    base += 4 * n_x
    yd = list(struct.unpack(">%di" % n_y, payload[base:base + 4 * n_y]))
    base += 4 * n_y
    colors = list(struct.unpack(">%dI" % n_c, payload[base:base + 4 * n_c]))
    return payload[:32], xd, yd, colors


def _rebuild_nptc(payload, colors):
    """Replace only the trailing colors array, keeping everything else."""
    head, xd, yd, old = _parse_nptc(payload)
    assert len(colors) == len(old), (len(colors), len(old))
    body = struct.pack(">%di" % len(xd), *xd) + struct.pack(">%di" % len(yd), *yd)
    return head + body + struct.pack(">%dI" % len(colors), *colors)


def _region_colors(im, xdivs, ydivs):
    """Recompute the 9-patch per-region color hints from actual pixels.

    Android fills a region with this color instead of sampling the bitmap
    when it is a single solid colour, so a stale hint would paint the old
    theme's colour straight over our artwork.
    """
    w, h = im.size
    px = im.convert("RGBA").load()
    xs = [0] + list(xdivs) + [w]
    ys = [0] + list(ydivs) + [h]
    out = []
    for r in range(len(ys) - 1):
        for c in range(len(xs) - 1):
            first = None
            uniform = True
            all_clear = True
            for y in range(ys[r], ys[r + 1]):
                for x in range(xs[c], xs[c + 1]):
                    p = px[x, y]
                    if p[3] != 0:
                        all_clear = False
                    if first is None:
                        first = p
                    elif p != first:
                        uniform = False
                        break
                if not uniform:
                    break
            if all_clear:
                out.append(NP_TRANSPARENT)
            elif uniform and first is not None:
                r_, g_, b_, a_ = first
                out.append((a_ << 24) | (r_ << 16) | (g_ << 8) | b_)
            else:
                out.append(NP_NO_COLOR)
    return out


def _draw_name_tag(im, text, fill, text_color, anchor, bubble_top):
    """Draw a rounded name label in the fixed, non-stretching strip above
    the bubble (the area the template already reserved for decoration).

    anchor: "left" or "right" -- must be the side that does NOT contain the
    stretch column, so the label stays glued to that edge when the bubble
    grows. Characters are drawn one at a time so spacing stays exact.
    """
    W, _ = im.size
    SS = 4
    gap = 8
    tag_h = max(30, bubble_top - gap)
    font_px = int(tag_h * 0.60)
    font = ImageFont.truetype(FONT_PATH, font_px * SS)

    tracking = int(font_px * 0.06) * SS
    widths = [font.getbbox(ch)[2] - font.getbbox(ch)[0] for ch in text]
    text_w = sum(widths) + tracking * (len(text) - 1)
    pad_x = int(font_px * 0.55)
    tag_w = text_w // SS + pad_x * 2

    layer = Image.new("RGBA", (tag_w * SS, tag_h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([0, 0, tag_w * SS - 1, tag_h * SS - 1],
                        radius=tag_h * SS // 2, fill=fill + (255,))

    # little tail, pointing down toward the bubble on the anchored side
    tw = int(tag_h * 0.34) * SS
    tx = int(tag_w * 0.22) * SS if anchor == "left" else int(tag_w * 0.78) * SS - tw
    d.polygon([(tx, tag_h * SS - 2), (tx + tw, tag_h * SS - 2),
               (tx + tw // 2, tag_h * SS + int(gap * 0.8) * SS)], fill=fill + (255,))

    x = pad_x * SS
    ascent, descent = font.getmetrics()
    baseline = (tag_h * SS - (ascent + descent)) // 2 + ascent
    for ch, cw in zip(text, widths):
        bbox = font.getbbox(ch)
        d.text((x - bbox[0], baseline), ch, font=font, fill=text_color + (255,),
               anchor="ls")
        x += cw + tracking

    layer = layer.resize((tag_w, tag_h + gap), Image.LANCZOS)
    px = 6 if anchor == "left" else W - tag_w - 6
    im.alpha_composite(layer, (max(0, px), 0))
    return im


def rebuild_ninepatch(png_bytes, mode, spec, name=None, name_fill=None,
                      name_text_color=(7, 39, 43), anchor="left"):
    """Recolor a compiled 9-patch and optionally stamp a name label on it.

    Every chunk except IDAT and npTc is copied verbatim, so the stretch
    divisions and padding aapt2 computed survive untouched. npTc's trailing
    per-region colour hints ARE recomputed from the final pixels: Android
    fills a region with its hint colour instead of sampling the bitmap when
    the region is a single solid colour, so leaving the template's hints in
    place would paint the original theme's colours over our artwork.
    """
    chunks = _png_chunks(png_bytes)
    ihdr = next(p for t, p, _ in chunks if t == b"IHDR")
    w, h, bitdepth, colortype = struct.unpack(">IIBB", ihdr[:10])
    has_alpha = colortype == 6

    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    px = im.load()

    if mode == "dominant":
        counts = {}
        for y in range(h):
            for x in range(w):
                p = px[x, y]
                if p[3] == 0:
                    continue
                counts[p[:3]] = counts.get(p[:3], 0) + 1
        total = sum(counts.values()) or 1
        dominant = {c for c, n in counts.items() if n / total >= 0.5}
        swap = {old: spec[old] for old in dominant if old in spec}
        if not swap:
            raise ValueError(f"dominant colors {dominant} not in remap {spec}")
        new_fill = next(iter(swap.values()))
        for y in range(h):
            for x in range(w):
                p = px[x, y]
                if p[:3] in swap:
                    px[x, y] = swap[p[:3]] + (p[3],)
    else:
        new_fill = spec
        for y in range(h):
            for x in range(w):
                p = px[x, y]
                if p[3] > 0:
                    px[x, y] = new_fill + (p[3],)

    if name:
        # the template reserves a decorative strip above the bubble; find
        # where the bubble itself starts, wipe the old sticker, put the name
        # label there instead
        bubble_top = h
        for y in range(h):
            if any(px[x, y][:3] == new_fill and px[x, y][3] > 0 for x in range(w)):
                bubble_top = y
                break
        clear_to = max(0, bubble_top - 4)
        for y in range(clear_to):
            for x in range(w):
                px[x, y] = (0, 0, 0, 0)
        _draw_name_tag(im, name, name_fill, name_text_color, anchor, bubble_top)

    out_im = im if has_alpha else im.convert("RGB")
    new_idat = _encode_idat(out_im.tobytes(), w, h, has_alpha)

    nptc = next(p for t, p, _ in chunks if t == b"npTc")
    _, xd, yd, _old_colors = _parse_nptc(nptc)
    new_nptc = _rebuild_nptc(nptc, _region_colors(im, xd, yd))

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    inserted = False
    for ctype, payload, _ in chunks:
        if ctype == b"IDAT":
            if not inserted:
                out += _make_chunk(b"IDAT", new_idat)
                inserted = True
            continue
        if ctype == b"npTc":
            out += _make_chunk(b"npTc", new_nptc)
            continue
        out += _make_chunk(ctype, payload)
    return bytes(out)


# --------------------------------------------------------------------------
# resources.arsc color patcher
# --------------------------------------------------------------------------

COLOR_MAP = {
    "actionButtonBorderColor": None,  # keep (subtle black border, theme-neutral)
    "actionButtonPressedColor": 0xFF1C2430,
    "statusBarColor": 0xFF111318,
    "theme_background_color": 0xFF111318,
    "theme_body_cell_border_color": 0x294A5560,
    "theme_body_cell_color": None,  # alpha 0, keep transparent
    "theme_body_cell_pressed_color": 0x1A39C5BB,
    "theme_body_secondary_cell_color": 0xFF171B23,
    "theme_chatroom_background_color": 0xFF111318,
    "theme_chatroom_bubble_me_color": 0xFF39C5BB,
    "theme_chatroom_bubble_you_color": 0xFF2A313D,
    "theme_chatroom_input_bar_background_color": 0xFF151922,
    "theme_chatroom_input_bar_color": 0xFF9AA8B5,
    "theme_chatroom_input_bar_menu_button_color": 0x148FA0AE,
    "theme_chatroom_input_bar_menu_icon_color": 0xFFEDF3F5,
    "theme_chatroom_input_bar_send_button_color": 0xFF39C5BB,
    "theme_chatroom_input_bar_send_icon_color": 0xFFFFFFFF,
    "theme_chatroom_unread_count_color": 0xFFFF6FA5,
    "theme_description_color": 0xFF9AA8B5,
    "theme_description_pressed_color": 0xFFC7D2DA,
    "theme_direct_share_background_color": 0xFF151922,
    "theme_direct_share_button_color": 0xFF39C5BB,
    "theme_direct_share_color": 0xFFEDF3F5,
    "theme_feature_browse_tab_color": 0xFF8FA0AE,
    "theme_feature_browse_tab_focused_color": 0xFF39C5BB,
    "theme_feature_primary_color": 0xFF39C5BB,
    "theme_feature_primary_pressed_color": 0xFF7FE3DC,
    "theme_header_cell_color": 0xFF151922,
    "theme_header_color": 0xFFEDF3F5,
    "theme_maintab_cell_color": 0xFF171B23,
    "theme_notification_background_color": 0xFF171B23,
    "theme_notification_background_pressed_color": 0xFF222A36,
    "theme_notification_color": 0xFFEDF3F5,
    "theme_paragraph_color": 0xFFA8B6C2,
    "theme_paragraph_pressed_color": 0xFFD3DDE4,
    "theme_passcode_background_color": 0xFF111318,
    "theme_passcode_color": 0xFFEDF3F5,
    "theme_passcode_keypad_background_color": 0xFF151922,
    "theme_passcode_keypad_color": 0xFFEDF3F5,
    "theme_passcode_keypad_pressed_background_color": 0xFF39C5BB,
    "theme_passcode_keypad_pressed_color": 0xFFFFFFFF,
    "theme_passcode_pattern_line_color": 0xFF39C5BB,
    "theme_section_title_color": 0xFF7FE3DC,
    "theme_tab_bannerbadge_background_color": 0xFFFF6FA5,
    "theme_tab_lightbannerbadge_background_color": 0xFFFFD24A,
    "theme_title_color": 0xFFEDF3F5,
    "theme_title_pressed_color": 0xFF7FE3DC,
}


def patch_colors(arsc_bytes, verbose=True):
    arsc = ARSCParser(arsc_bytes)
    pkg = arsc.get_packages_names()[0]
    patched = bytearray(arsc_bytes)
    n_patched, n_skipped = 0, 0

    for name, new_val in COLOR_MAP.items():
        if new_val is None:
            n_skipped += 1
            continue
        rid = arsc.get_res_id_by_key(pkg, "color", name)
        configs = arsc.get_res_configs(rid)
        if len(configs) != 1:
            raise RuntimeError(f"expected exactly one config for {name}, got {len(configs)}")
        _, entry = configs[0]
        value_ref = entry.key
        if value_ref.data_type != TYPE_INT_COLOR_ARGB8:
            raise RuntimeError(
                f"{name} is not a plain ARGB8 color (data_type=0x{value_ref.data_type:02x}); "
                "it's probably a color-state-list reference and must not be patched this way"
            )
        offset = value_ref.start + 4  # skip size(2)+res0(1)+dtype(1) to reach the 4-byte data field
        old_val = struct.unpack_from("<I", patched, offset)[0]
        struct.pack_into("<I", patched, offset, new_val)
        n_patched += 1
        if verbose:
            print(f"  {name:48s} 0x{old_val:08X} -> 0x{new_val:08X}")

    print(f"  colors patched: {n_patched}, left as-is: {n_skipped}")
    return bytes(patched)


# --------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------

# (zip path) -> generator returning raw PNG bytes, given the ORIGINAL bytes
IMAGE_BUILDERS = {}


def _reg(path):
    def deco(fn):
        IMAGE_BUILDERS[path] = fn
        return fn
    return deco


def _png(img):
    buf = io.BytesIO()
    img.convert("RGB" if img.mode == "RGB" else "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _photo(img, quality=82):
    """Encode photographic content as JPEG bytes while keeping the ".png"
    filename the resource table expects. Android's Skia decoder sniffs the
    magic bytes, not the extension, so this is safe -- and it shrinks a
    ~4MB lossless PNG of a photo mosaic down to a few hundred KB."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def build_all_images(original_sizes):
    """original_sizes: dict zip_path -> (w, h). Returns dict zip_path -> new bytes,
    for every plain (non-9-patch, non-mipmap) themeable PNG."""
    out = {}

    def size(path):
        return original_sizes[path]

    for path, (w, h) in original_sizes.items():
        base = os.path.basename(path)
        if ".9.png" in base or "/mipmap" in path:
            continue

        if base == "theme_splash_image.png":
            out[path] = _photo(build_splash(w, h))
        elif base == "theme_background_image.png":
            out[path] = _photo(themed_background(w, h, glow_color=TEAL, cols=4, focus=0.30, dim=0.42))
        elif base == "theme_chatroom_background_image.png":
            out[path] = _photo(themed_background(w, h, glow_color=PINK, cols=3, focus=0.32, dim=0.50))
        elif base == "theme_passcode_background_image.png":
            out[path] = _photo(themed_background(w, h, glow_color=TEAL, cols=3, focus=0.36, dim=0.60))
        elif base == "theme_profile_01_image.png":
            out[path] = _png(build_profile(w))
        elif base == "theme_find_add_friend_button_image.png":
            out[path] = _png(build_add_friend_button(w, h, pressed=False))
        elif base == "theme_find_add_friend_button_pressed_image.png":
            out[path] = _png(build_add_friend_button(w, h, pressed=True))
        elif base.startswith("theme_passcode_0") and base.endswith("_checked_image.png"):
            out[path] = _png(build_passcode_dot(w, filled=True))
        elif base.startswith("theme_passcode_0") and base.endswith("_image.png"):
            out[path] = _png(build_passcode_dot(w, filled=False))
        elif base.startswith("theme_maintab_ico_"):
            rest = base[len("theme_maintab_ico_"):-len("_image.png")]
            focused = rest.endswith("_focused")
            key = rest[:-len("_focused")] if focused else rest
            fn = TAB_ICONS.get(key)
            if fn is None:
                continue
            color = TEAL if focused else MUTED
            icon = fn(max(w, h) * 2, color)
            out[path] = _png(fit_square(icon, w).resize((w, h), Image.LANCZOS)
                              if w == h else icon.resize((w, h), Image.LANCZOS))
        # anything else (mipmap icons) is left untouched by omission

    return out


YOU_BUBBLE = (42, 49, 61)

# Name labels ride in the fixed strip above each first-of-group bubble, on
# the side away from that bubble's stretch column, so they never smear:
# "me" stretches at x=60..63 (label goes right), "you" at x=258..261 (left).
MY_NAME = "코미"
THEIR_NAME = "좆현우"

NINEPATCH_JOBS = {
    "theme_chatroom_bubble_me_01_image.9.png": dict(
        mode="dominant", spec={(34, 51, 230): TEAL},
        name=MY_NAME, name_fill=PINK, anchor="right"),
    "theme_chatroom_bubble_me_02_image.9.png": dict(
        mode="solid", spec=TEAL),
    "theme_chatroom_bubble_you_01_image.9.png": dict(
        mode="dominant", spec={(242, 244, 244): YOU_BUBBLE},
        name=THEIR_NAME, name_fill=TEAL, anchor="left"),
    "theme_chatroom_bubble_you_02_image.9.png": dict(
        mode="solid", spec=YOU_BUBBLE),
    "theme_maintab_cell_image.9.png": dict(
        mode="solid", spec=PANEL2),
}


def build_ninepatch_images(original_bytes_by_path):
    out = {}
    for path, data in original_bytes_by_path.items():
        base = os.path.basename(path)
        job = NINEPATCH_JOBS.get(base)
        if job is None:
            continue
        out[path] = rebuild_ninepatch(data, **job)
    return out


def main():
    if len(sys.argv) != 3:
        print("usage: build_android_theme_apk.py <template.apk> <out.apk>")
        sys.exit(1)
    template_path, out_path = sys.argv[1], sys.argv[2]

    print("== reading template APK ==")
    with zipfile.ZipFile(template_path) as zin:
        infos = [i for i in zin.infolist() if not i.filename.startswith("META-INF/")]
        original_bytes = {i.filename: zin.read(i.filename) for i in infos}

    sizes = {}
    for name, data in original_bytes.items():
        if name.endswith(".png"):
            w, h, *_ = struct.unpack(">IIBB", data[16:26])
            sizes[name] = (w, h)

    ninepatch_names = {n for n in sizes if os.path.basename(n) in NINEPATCH_JOBS}
    plain_sizes = {n: s for n, s in sizes.items() if n not in ninepatch_names}

    print("== generating plain images ==")
    new_plain = build_all_images(plain_sizes)
    print(f"  generated {len(new_plain)} / {len(plain_sizes)} plain PNGs")

    print("== recoloring 9-patch assets ==")
    ninepatch_src = {n: original_bytes[n] for n in ninepatch_names}
    new_ninepatch = build_ninepatch_images(ninepatch_src)
    for n in ninepatch_names:
        if n not in new_ninepatch:
            print(f"  WARNING: no recolor rule matched for {n}, left unchanged")

    print("== patching resources.arsc colors ==")
    new_arsc = patch_colors(original_bytes["resources.arsc"])

    print("== assembling unsigned APK ==")
    updated = dict(original_bytes)
    updated.update(new_plain)
    updated.update(new_ninepatch)
    updated["resources.arsc"] = new_arsc

    unsigned_path = out_path + ".unsigned.apk"
    with zipfile.ZipFile(unsigned_path, "w") as zout:
        for info in infos:
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.external_attr = info.external_attr
            zout.writestr(zi, updated[info.filename],
                           compress_type=zipfile.ZIP_DEFLATED)

    print("== zipaligning + signing (APK Signature Scheme v2) ==")
    signer = APKSigner()
    signed_path = signer.sign(unsigned_path)
    os.replace(signed_path, out_path)
    os.remove(unsigned_path)

    print("== verifying with androguard ==")
    verify(out_path)

    print(f"\ndone: {out_path}  ({os.path.getsize(out_path)/1024/1024:.2f} MB)")


def verify(apk_path):
    a = APK(apk_path)
    assert a.is_signed(), "not signed at all"
    assert a.is_signed_v2(), "v2 signature missing"
    print("  is_signed_v2:", a.is_signed_v2())
    certs = a.get_certificates_der_v2()
    print("  v2 certificates:", len(certs))

    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes, serialization
    import hashlib as _hashlib

    a.parse_v2_signing_block()
    for signer in a._v2_signing_data:
        pub = serialization.load_der_public_key(signer.public_key)
        sig_algo, sig_bytes = signer.signatures[0]
        signed_data_bytes = signer.signed_data._bytes
        # cryptography's verify() hashes `signed_data_bytes` itself, so pass
        # the message, not a pre-computed digest (that would hash it twice).
        pub.verify(sig_bytes, signed_data_bytes, padding.PKCS1v15(), hashes.SHA256())
        print("  independent RSA/PKCS1v15/SHA256 signature check: OK")

    print("  package:", a.get_package())
    arsc_bytes = a.get_file("resources.arsc")
    arsc = ARSCParser(arsc_bytes)
    pkg = arsc.get_packages_names()[0]
    sample = ["theme_feature_primary_color", "theme_chatroom_bubble_me_color",
              "theme_background_color"]
    for name in sample:
        rid = arsc.get_res_id_by_key(pkg, "color", name)
        _, entry = arsc.get_res_configs(rid)[0]
        print(f"  {name} = 0x{entry.key.data:08X}")


if __name__ == "__main__":
    main()
