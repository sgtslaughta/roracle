"""Trim the white background off the rOracle logo: background -> transparent, anti-aliased edge preserved."""
import sys
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

src, out_dir = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
a = np.asarray(im).astype(np.int16)
W, H = im.size

# outer background = everything not blocked by the dark tile, connected to the image border.
# A dark barrier (min channel < 160) ignores faint specks in the white; white inside the bulb stays (not connected).
light = (a.min(axis=2) >= 160).astype(np.uint8) * 255
mask = Image.fromarray(light).copy()  # fromarray image is read-only; floodfill would silently no-op
for xy in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]:
    ImageDraw.floodfill(mask, xy, 128, thresh=0)
outer = np.asarray(mask) == 128
tile = ~outer

def dilate(m, n):
    return np.asarray(Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(n))) > 0

# anti-aliased ring along the tile edge: un-mix white from the tile colour
ring = dilate(tile, 5) & dilate(outer, 5)
fg = np.median(a[tile & ~ring & (a.max(axis=2) < 120)], axis=0)  # tile colour
lum, fg_lum = a.mean(axis=2), fg.mean()
alpha = np.where(tile, 255.0, 0.0)
edge_alpha = np.clip((255 - lum) / (255 - fg_lum), 0, 1) * 255
alpha[ring] = edge_alpha[ring]
band = ring

rgba = np.dstack([a, alpha]).astype(np.uint8)
rgba[band, :3] = fg.astype(np.uint8)  # edge pixels take the tile colour, transparency carries the blend
logo = Image.fromarray(rgba, "RGBA")
logo = logo.crop(logo.getchannel("A").getbbox())  # bbox on alpha: transparent white still has RGB
side = max(logo.size)  # pad to square so icons don't distort
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(logo, ((side - logo.width) // 2, (side - logo.height) // 2))
logo = sq

assert logo.getpixel((0, 0))[3] == 0, "corner should be transparent"
assert logo.getpixel((logo.width // 2, logo.height // 2))[3] == 255, "centre should be opaque"
logo.save(f"{out_dir}/logo.png", optimize=True)
logo.resize((180, 180), Image.LANCZOS).save(f"{out_dir}/apple-touch-icon.png", optimize=True)
logo.resize((512, 512), Image.LANCZOS).save(f"{out_dir}/logo-512.png", optimize=True)
logo.save(f"{out_dir}/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
print("tile colour", "#%02x%02x%02x" % tuple(int(c) for c in fg), "size", logo.size)
