"""Draws the app icon (mac/AppIcon.icns): an orange negative strip being read by a light beam.
Only needed to change the icon; the .icns is kept in git. Needs Pillow:  pip install pillow
    python mac/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024
HERE = Path(__file__).resolve().parent


def squircle_mask(size, inset, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((inset, inset, size - inset, size - inset), radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((size, size))


def draw():
    inset, radius = 100, 185
    mask = squircle_mask(S, inset, radius)
    bg = vertical_gradient(S, (52, 60, 76), (16, 19, 26))
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # drop shadow under the tile
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(18)).point(lambda v: v * 0.45))
    icon.alpha_composite(shadow, (0, 12))
    tile = bg.convert("RGBA")
    tile.putalpha(mask)
    icon.alpha_composite(tile)

    # the film strip, drawn flat then turned a little
    strip = Image.new("RGBA", (1300, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    d.rectangle((0, 0, 1300, 420), fill=(214, 120, 52, 255))            # orange negative base
    for x in range(-10, 1300, 64):                                          # sprocket holes
        d.rounded_rectangle((x, 22, x + 34, 58), 7, fill=(20, 22, 28, 255))
        d.rounded_rectangle((x, 362, x + 34, 398), 7, fill=(20, 22, 28, 255))
    for i, x in enumerate(range(40, 1300, 330)):                            # frames
        d.rounded_rectangle((x, 82, x + 300, 338), 14, fill=(122, 62, 30, 255))
        # a little negative landscape in each frame: a sun and hills, colours inverted
        d.ellipse((x + 180, 110, x + 250, 180), fill=(70, 150, 220, 255))
        d.polygon([(x, 338), (x + 90, 230), (x + 170, 300), (x + 240, 210), (x + 300, 280), (x + 300, 338)],
                  fill=(60, 110, 150, 255))
    strip = strip.rotate(-14, resample=Image.BICUBIC, expand=True)
    sx = (S - strip.width) // 2
    sy = (S - strip.height) // 2 + 30
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.alpha_composite(strip, (sx, sy))

    # the scanner's light: a bright band across the strip, with a soft glow
    beam = Image.new("L", (S, S), 0)
    ImageDraw.Draw(beam).rectangle((502, 0, 522, S), fill=255)
    glow = beam.filter(ImageFilter.GaussianBlur(30)).point(lambda v: min(255, v * 3))
    light = Image.new("RGBA", (S, S), (255, 244, 220, 0))
    light.putalpha(ImageChops.add(glow.point(lambda v: v * 0.55), beam.point(lambda v: v * 0.85)))

    inner = squircle_mask(S, inset, radius)
    for part in (layer, light):
        a = ImageChops.multiply(part.getchannel("A"), inner)
        part.putalpha(a)
        icon.alpha_composite(part)

    # a thin rim so it reads on a dark Dock
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle((inset, inset, S - inset, S - inset), radius,
                                          outline=(255, 255, 255, 40), width=4)
    icon.alpha_composite(rim)
    return icon


if __name__ == "__main__":
    img = draw()
    img.save(HERE / "AppIcon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    print("wrote", HERE / "AppIcon.icns")
