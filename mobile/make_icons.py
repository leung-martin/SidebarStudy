"""Android launcher icons + splash screens from the desktop app's icon: a white 学 on a
violet-to-pink gradient (see icon.ico). Run with the desktop app's Python (needs Pillow)."""
import os
from PIL import Image, ImageDraw, ImageFont

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "res")
FONT = r"C:\Windows\Fonts\msyhbd.ttc"
TOP, BOTTOM = (139, 108, 255), (255, 79, 154)
DARK = (16, 18, 25)
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def gradient(size):
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        d.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    return img


def glyph(img, box_size, scale):
    """White 学 centered, sized to `scale` of the box."""
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, round(box_size * scale))
    w, h = img.size
    d.text((w / 2, h / 2 + box_size * 0.02), "学", font=font, fill="white", anchor="mm")
    return img


def masked(img, mask_fn):
    mask = Image.new("L", img.size, 0)
    mask_fn(ImageDraw.Draw(mask), img.size[0])
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


for name, k in DENSITIES.items():
    folder = os.path.join(RES, f"mipmap-{name}")
    legacy, adaptive = round(48 * k), round(108 * k)
    # adaptive icon: gradient background layer + transparent foreground with the glyph (safe zone 66/108)
    gradient(adaptive).save(os.path.join(folder, "ic_launcher_background.png"))
    glyph(Image.new("RGBA", (adaptive, adaptive), (0, 0, 0, 0)), adaptive, 0.42).save(
        os.path.join(folder, "ic_launcher_foreground.png"))
    # legacy launchers (Android 7 and older): rounded square and circle
    tile = glyph(gradient(legacy), legacy, 0.62)
    masked(tile, lambda d, s: d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=255)).save(
        os.path.join(folder, "ic_launcher.png"))
    masked(tile, lambda d, s: d.ellipse([0, 0, s - 1, s - 1], fill=255)).save(
        os.path.join(folder, "ic_launcher_round.png"))

for xml in ("ic_launcher.xml", "ic_launcher_round.xml"):
    with open(os.path.join(RES, "mipmap-anydpi-v26", xml), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '    <background android:drawable="@mipmap/ic_launcher_background"/>\n'
                '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
                '</adaptive-icon>\n')

# splash screens: the app's dark background with the icon tile in the middle
for root, _, files in os.walk(RES):
    for f in files:
        if f == "splash.png":
            p = os.path.join(root, f)
            w, h = Image.open(p).size
            s = round(min(w, h) * 0.28)
            tile = glyph(gradient(s), s, 0.62)
            tile = masked(tile, lambda d, z: d.rounded_rectangle([0, 0, z - 1, z - 1], radius=z * 0.22, fill=255))
            splash = Image.new("RGB", (w, h), DARK)
            splash.paste(tile, ((w - s) // 2, (h - s) // 2), tile)
            splash.save(p)
print("icons and splash screens written")
