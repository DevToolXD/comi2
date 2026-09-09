"""Render a chat mockup from the built APK's own 9-patch bubbles.

Stretches each bubble exactly the way Android does (fixed corners, the
npTc stretch row/column repeated) so the baked-in name labels can be
checked at real message widths before shipping.
"""

import io
import os
import struct
import sys
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
BUBBLE_DIR = "res/drawable-xxhdpi/"


def png_chunks(data):
    off, out = 8, []
    while off < len(data):
        ln = struct.unpack(">I", data[off:off + 4])[0]
        t = data[off + 4:off + 8]
        out.append((t, data[off + 8:off + 8 + ln]))
        off += 12 + ln
        if t == b"IEND":
            break
    return out


def load_ninepatch(apk, name):
    data = apk.read(BUBBLE_DIR + name)
    cs = dict(png_chunks(data))
    np = cs[b"npTc"]
    n_x, n_y = np[1], np[2]
    base = 32
    xd = struct.unpack(">%di" % n_x, np[base:base + 4 * n_x]); base += 4 * n_x
    yd = struct.unpack(">%di" % n_y, np[base:base + 4 * n_y])
    pl, pr, pt, pb = struct.unpack(">iiii", np[12:28])
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    return im, xd, yd, (pl, pr, pt, pb)


def stretch(im, xd, yd, w, h):
    """Reproduce Android's 9-patch scaling: fixed regions keep their size,
    the single stretch row/column absorbs the rest."""
    sw, sh = im.size
    x0, x1 = xd
    y0, y1 = yd
    fixed_w, fixed_h = sw - (x1 - x0), sh - (y1 - y0)
    w, h = max(w, fixed_w), max(h, fixed_h)

    xs_src = [(0, x0), (x0, x1), (x1, sw)]
    ys_src = [(0, y0), (y0, y1), (y1, sh)]
    xs_dst_w = [x0, w - fixed_w, sw - x1]
    ys_dst_h = [y0, h - fixed_h, sh - y1]

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dy = 0
    for (sy0, sy1), dh in zip(ys_src, ys_dst_h):
        dx = 0
        for (sx0, sx1), dw in zip(xs_src, xs_dst_w):
            if dw <= 0 or dh <= 0:
                dx += dw
                continue
            part = im.crop((sx0, sy0, sx1, sy1)).resize((dw, dh), Image.NEAREST)
            out.paste(part, (dx, dy))
            dx += dw
        dy += dh
    return out


def main(apk_path, out_path):
    apk = zipfile.ZipFile(apk_path)
    me1, me_xd, me_yd, me_pad = load_ninepatch(apk, "theme_chatroom_bubble_me_01_image.9.png")
    me2, me2_xd, me2_yd, me2_pad = load_ninepatch(apk, "theme_chatroom_bubble_me_02_image.9.png")
    you1, y_xd, y_yd, y_pad = load_ninepatch(apk, "theme_chatroom_bubble_you_01_image.9.png")
    you2, y2_xd, y2_yd, y2_pad = load_ninepatch(apk, "theme_chatroom_bubble_you_02_image.9.png")

    bg = Image.open(io.BytesIO(apk.read(BUBBLE_DIR + "theme_chatroom_background_image.png")))
    W, H = 1080, 1500
    bw, bh = bg.size
    sc = max(W / bw, H / bh)
    bg = bg.resize((int(bw * sc), int(bh * sc)), Image.LANCZOS).crop((0, 0, W, H)).convert("RGBA")

    canvas = bg
    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(FONT, 40)

    convo = [
        ("you", True, "오늘 촬영본 나왔어?"),
        ("you", False, "13종 다 찍은거 실화냐"),
        ("me", True, "ㅇㅇ 방금 테마까지 뽑음"),
        ("me", False, "말풍선에 이름도 박음"),
        ("you", True, "헐 대박"),
    ]

    y = 40
    for side, first, text in convo:
        tw = int(d.textlength(text, font=font))
        if side == "me":
            im, xd, yd, pad = (me1, me_xd, me_yd, me_pad) if first else (me2, me2_xd, me2_yd, me2_pad)
        else:
            im, xd, yd, pad = (you1, y_xd, y_yd, y_pad) if first else (you2, y2_xd, y2_yd, y2_pad)
        pl, pr, pt, pb = pad
        want_w = tw + pl + pr
        want_h = 58 + pt + pb
        b = stretch(im, xd, yd, want_w, want_h)
        bx = W - b.size[0] - 30 if side == "me" else 30
        canvas.alpha_composite(b, (bx, y))
        tc = (7, 39, 43) if side == "me" else (237, 243, 245)
        d.text((bx + pl, y + pt + 10), text, font=font, fill=tc)
        y += b.size[1] + 18

    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    print("wrote", out_path, canvas.size)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
