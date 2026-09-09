"""Render a mockup preview of the theme for the download page."""

import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "ios", "src", "Images")
DOCS = os.path.join(ROOT, "docs", "assets")
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

TEAL = (57, 197, 187)
TEXT = (237, 243, 245)
MUTED = (143, 160, 174)
PINK = (255, 111, 165)
SEND_TEXT = (7, 39, 43)

S = 2  # render scale
W, H = 430 * S, 900 * S


def f(size):
    return ImageFont.truetype(FONT, size * S)


def img(name):
    return Image.open(os.path.join(IMAGES, name)).convert("RGBA")


def cover(im, w, h, focus=0.2):
    sw, sh = im.size
    sc = max(w / sw, h / sh)
    im = im.resize((int(sw * sc), int(sh * sc)), Image.LANCZOS)
    left = (im.size[0] - w) // 2
    top = int((im.size[1] - h) * focus)
    return im.crop((left, top, left + w, top + h))


def circle(im, size):
    im = cover(im, size, size, 0.16)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def photo(name):
    return Image.open(os.path.join(ROOT, "photos", name)).convert("RGBA")


def nine(name, w, h):
    """Stretch a bubble image keeping its rounded corners."""
    src = img(name)
    sw, sh = src.size
    cap = int(sw * 0.34)
    capv = int(sh * 0.40)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    parts = [
        ((0, 0, cap, capv), (0, 0, cap, capv)),
        ((sw - cap, 0, sw, capv), (w - cap, 0, w, capv)),
        ((0, sh - capv, cap, sh), (0, h - capv, cap, h)),
        ((sw - cap, sh - capv, sw, sh), (w - cap, h - capv, w, h)),
        ((cap, 0, sw - cap, capv), (cap, 0, w - cap, capv)),
        ((cap, sh - capv, sw - cap, sh), (cap, h - capv, w - cap, h)),
        ((0, capv, cap, sh - capv), (0, capv, cap, h - capv)),
        ((sw - cap, capv, sw, sh - capv), (w - cap, capv, w, h - capv)),
        ((cap, capv, sw - cap, sh - capv), (cap, capv, w - cap, h - capv)),
    ]
    for sbox, dbox in parts:
        piece = src.crop(sbox)
        tw, th = dbox[2] - dbox[0], dbox[3] - dbox[1]
        if tw <= 0 or th <= 0:
            continue
        out.paste(piece.resize((tw, th), Image.BILINEAR), (dbox[0], dbox[1]))
    return out


def status_bar(d, y=14):
    d.text((26 * S, y * S), "9:41", font=f(15), fill=TEXT)
    d.rounded_rectangle([W - 60 * S, y * S + 3 * S, W - 32 * S, y * S + 15 * S],
                        radius=3 * S, outline=TEXT, width=S)
    d.rectangle([W - 58 * S, y * S + 5 * S, W - 42 * S, y * S + 13 * S], fill=TEXT)


def tab_bar(canvas):
    bar_h = 78 * S
    bg = img("maintabBgImage@3x.png").resize((W, bar_h), Image.LANCZOS)
    canvas.paste(bg, (0, H - bar_h), bg)
    keys = ["Friends", "Chats", "Browse", "Find", "More"]
    active = 1
    slot = W // len(keys)
    for i, k in enumerate(keys):
        name = f"maintabIco{k}{'Selected' if i == active else ''}@3x.png"
        ic = img(name)
        ic.thumbnail((30 * S, 30 * S), Image.LANCZOS)
        canvas.paste(ic, (i * slot + (slot - ic.size[0]) // 2, H - bar_h + 20 * S), ic)


def screen_chatroom():
    canvas = Image.new("RGBA", (W, H))
    bg = cover(img("chatroomBgImage@3x.png"), W, H, 0.0)
    canvas.paste(bg, (0, 0))
    d = ImageDraw.Draw(canvas)
    status_bar(d)

    d.text((W // 2, 52 * S), "미쿠 팬방", font=f(17), fill=TEXT, anchor="mm")
    d.text((W - 30 * S, 52 * S), "4", font=f(14), fill=MUTED, anchor="mm")

    d.text((W // 2, 96 * S), "2026년 9월 8일", font=f(11), fill=(190, 205, 215), anchor="mm")

    convo = [
        ("recv", "02_teto.png", "테토", "오늘 촬영본 나왔어?", 132),
        ("recv", "02_teto.png", None, "13종 다 찍은거 실화냐", 176),
        ("send", None, None, "ㅇㅇ 방금 테마까지 뽑음", 222),
        ("send", None, None, "카톡테마로 만들었다", 266),
        ("recv", "04_luka.png", "루카", "헐 대박 나도 줘", 312),
        ("send", None, None, "링크 보내줄게", 358),
    ]

    for kind, av, name, text, y in convo:
        y = y * S
        fnt = f(14)
        tw = d.textlength(text, font=fnt)
        bw = int(tw + 28 * S)
        bh = 38 * S
        if kind == "recv":
            x = 62 * S
            if av:
                pic = circle(photo(av), 36 * S)
                canvas.paste(pic, (16 * S, y - 2 * S), pic)
            if name:
                d.text((62 * S, y - 16 * S), name, font=f(11), fill=(205, 216, 224))
            b = nine("chatroomBubbleReceive01@3x.png", bw, bh)
            canvas.paste(b, (x, y), b)
            d.text((x + 14 * S, y + bh // 2), text, font=fnt, fill=TEXT, anchor="lm")
            d.text((x + bw + 8 * S, y + bh - 6 * S), "오후 9:4", font=f(9),
                   fill=(190, 203, 213), anchor="lm")
        else:
            x = W - 16 * S - bw
            b = nine("chatroomBubbleSend01@3x.png", bw, bh)
            canvas.paste(b, (x, y), b)
            d.text((x + 14 * S, y + bh // 2), text, font=fnt, fill=SEND_TEXT, anchor="lm")
            d.text((x - 8 * S, y + bh - 6 * S), "1", font=f(10), fill=(255, 210, 74), anchor="rm")

    bar_y = H - 74 * S
    d.rectangle([0, bar_y, W, H], fill=(21, 25, 34, 255))
    d.text((22 * S, bar_y + 26 * S), "+", font=f(22), fill=MUTED, anchor="lm")
    d.rounded_rectangle([48 * S, bar_y + 12 * S, W - 62 * S, bar_y + 42 * S],
                        radius=15 * S, fill=(34, 40, 52, 255))
    d.text((62 * S, bar_y + 27 * S), "메시지 입력", font=f(13), fill=(120, 134, 148), anchor="lm")
    d.ellipse([W - 54 * S, bar_y + 12 * S, W - 24 * S, bar_y + 42 * S], fill=TEAL)
    return canvas


def screen_friends():
    canvas = Image.new("RGBA", (W, H))
    bg = cover(img("mainBgImage@3x.png"), W, H, 0.0)
    canvas.paste(bg, (0, 0))
    d = ImageDraw.Draw(canvas)
    status_bar(d)

    d.text((20 * S, 58 * S), "친구", font=f(22), fill=TEXT, anchor="lm")

    d.line([(20 * S, 92 * S), (W - 20 * S, 92 * S)], fill=(57, 197, 187, 90), width=S)

    me = [("01_miku.png", "조현우", "13종 코스프레 완료")]
    friends = [
        ("02_teto.png", "테토", "0401"),
        ("07_len.png", "렌", "미쿠 없으면 못삼"),
        ("04_luka.png", "루카", "just be friends"),
        ("06_kaito.png", "카이토", "아이스크림"),
        ("05_meiko.png", "메이코", "술은 어른이 되고나서"),
        ("03_rin.png", "린", "리모컨"),
        ("13_pinkhair.png", "핑크", "안녕"),
        ("12_purple.png", "퍼플", "..."),
    ]

    y = 108 * S
    for av, name, msg in me:
        pic = circle(photo(av), 56 * S)
        canvas.paste(pic, (20 * S, y), pic)
        d.text((88 * S, y + 18 * S), name, font=f(16), fill=TEXT, anchor="lm")
        d.text((88 * S, y + 40 * S), msg, font=f(11), fill=(154, 168, 181), anchor="lm")
        y += 78 * S

    d.text((20 * S, y + 4 * S), "친구 8", font=f(11), fill=TEAL)
    y += 28 * S

    for av, name, msg in friends:
        pic = circle(photo(av), 44 * S)
        canvas.paste(pic, (20 * S, y), pic)
        d.text((78 * S, y + 14 * S), name, font=f(14), fill=TEXT, anchor="lm")
        d.text((78 * S, y + 33 * S), msg, font=f(10), fill=(154, 168, 181), anchor="lm")
        y += 60 * S

    tab_bar(canvas)
    return canvas


def frame(screen, radius=44):
    """Round the corners and add a thin device bezel."""
    w, h = screen.size
    pad = 10 * S
    out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    d.rounded_rectangle([0, 0, out.size[0] - 1, out.size[1] - 1],
                        radius=(radius + 10) * S, fill=(38, 42, 50, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius * S, fill=255)
    out.paste(screen, (pad, pad), mask)
    return out


def main():
    os.makedirs(DOCS, exist_ok=True)
    a = frame(screen_chatroom())
    b = frame(screen_friends())

    gap = 30 * S
    margin = 36 * S
    sheet = Image.new("RGB", (a.size[0] + b.size[0] + gap + margin * 2,
                              a.size[1] + margin * 2), (13, 15, 20))
    sheet.paste(b, (margin, margin), b)
    sheet.paste(a, (margin + b.size[0] + gap, margin), a)

    sheet = sheet.resize((sheet.size[0] // S, sheet.size[1] // S), Image.LANCZOS)
    sheet.save(os.path.join(DOCS, "preview.png"), "PNG", optimize=True)
    print("preview:", sheet.size, os.path.getsize(os.path.join(DOCS, "preview.png")) // 1024, "KB")


if __name__ == "__main__":
    main()
