"""Validate the iOS theme sources and package them into a .ktheme file."""

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "ios", "src")
IMAGES = os.path.join(SRC, "Images")
CSS = os.path.join(SRC, "KakaoTalkTheme.css")
OUT = os.path.join(ROOT, "ios", "VocaloidCosplay.ktheme")

# shipped without being referenced from the css (theme list thumbnail)
UNREFERENCED_OK = {"commonIcoTheme.png"}


def validate():
    css = open(CSS, encoding="utf-8").read()
    referenced = set(re.findall(r"'([A-Za-z0-9_]+\.png)'", css))
    have = set(os.listdir(IMAGES))

    problems = []
    for ref in sorted(referenced):
        stem = ref[:-4]
        variants = {f"{stem}.png", f"{stem}@2x.png", f"{stem}@3x.png"}
        if not (variants & have):
            problems.append(f"missing image for '{ref}'")

    provided_stems = set()
    for f in have:
        provided_stems.add(re.sub(r"@[23]x(?=\.png$)", "", f))
    unused = provided_stems - referenced - UNREFERENCED_OK
    for u in sorted(unused):
        print(f"  note: '{u}' is shipped but not referenced in the css")

    manifest = dict(re.findall(r"-kakaotalk-([a-z-]+):\s*'([^']*)'", css))
    for key in ("theme-name", "theme-version", "theme-id", "author-name"):
        if not manifest.get(key):
            problems.append(f"ManifestStyle is missing -kakaotalk-{key}")

    return manifest, referenced, have, problems


def package():
    manifest, referenced, have, problems = validate()
    if problems:
        for p in problems:
            print(f"  ERROR: {p}")
        sys.exit(1)

    if os.path.exists(OUT):
        os.remove(OUT)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(CSS, "KakaoTalkTheme.css")
        for name in sorted(have):
            if name.startswith("."):
                continue
            z.write(os.path.join(IMAGES, name), f"Images/{name}")

    size = os.path.getsize(OUT) / 1024 / 1024
    print(f"  theme    : {manifest['theme-name']} v{manifest['theme-version']}")
    print(f"  id       : {manifest['theme-id']}")
    print(f"  images   : {len(have)} files, {len(referenced)} referenced from css")
    print(f"  output   : {os.path.relpath(OUT, ROOT)} ({size:.2f} MB)")


if __name__ == "__main__":
    package()
