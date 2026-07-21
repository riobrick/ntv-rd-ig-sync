#!/usr/bin/env python3
"""Navy-band telop overlay generator (brand template).

Design rules:
- Band: navy #1F2AA0, 24px grid that terminates flush at all edges,
  warm off-white text with letter tracking
- Hook (first ~2.8s): one band, 2 lines centered as a block
  (line1 large/bold, line2 smaller/lighter for hierarchy)
- Header: recolored logo top-left (60,230), title band right-aligned
  (right edge at x=1020), vertically centered against the logo;
  auto-shrinks so it never reaches the logo
- Telops: single-line bands, bottom edge fixed above y=1500
- Safe zones respected: top 220 / bottom 420 / sides 60
"""
import math
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
CELL = 24
NAVY = (31, 42, 160, 255)
GRID = (58, 69, 181, 255)
TEXT = (216, 212, 200, 255)
SUB = (200, 196, 184, 255)
FD = "/usr/share/fonts/opentype/noto/"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


_FALLBACK = {"Medium": ["Medium", "Bold", "Regular"],
             "Bold": ["Bold", "Black", "Regular"],
             "Regular": ["Regular", "Medium", "Bold"]}


def font(size, weight="Medium"):
    for w in _FALLBACK.get(weight, [weight, "Regular"]):
        path = FD + f"NotoSansCJK-{w}.ttc"
        if os.path.exists(path):
            return ImageFont.truetype(path, size, index=0)
    raise OSError("No NotoSansCJK font found under " + FD)


def measure(text, f, tracking):
    ws = [f.getbbox(c)[2] - f.getbbox(c)[0] for c in text]
    return ws, sum(ws) + tracking * (len(text) - 1)


def draw_tracked(d, text, f, tracking, x, y_draw, fill):
    ws, _ = measure(text, f, tracking)
    for ch, cw in zip(text, ws):
        bb = f.getbbox(ch)
        d.text((x - bb[0], y_draw), ch, font=f, fill=fill)
        x += cw + tracking


def grid_canvas(bw, bh):
    bw = math.ceil(bw / CELL) * CELL
    bh = math.ceil(bh / CELL) * CELL
    band = Image.new("RGBA", (bw, bh), NAVY)
    d = ImageDraw.Draw(band)
    for gx in range(0, bw + 1, CELL):
        d.line([(min(gx, bw - 1), 0), (min(gx, bw - 1), bh)], fill=GRID, width=1)
    for gy in range(0, bh + 1, CELL):
        d.line([(0, min(gy, bh - 1)), (bw, min(gy, bh - 1))], fill=GRID, width=1)
    return band, d


def make_band(text, fontsize, weight="Medium", pad_x=46, pad_y=22, tracking=6):
    f = font(fontsize, weight)
    _, tw = measure(text, f, tracking)
    gb = f.getbbox(text)
    band, d = grid_canvas(tw + pad_x * 2, (gb[3] - gb[1]) + pad_y * 2)
    x = (band.width - tw) // 2
    y_draw = (band.height - (gb[3] - gb[1])) // 2 - gb[1]
    draw_tracked(d, text, f, tracking, x, y_draw, TEXT)
    return band


def make_band_2line(l1, s1, w1, l2, s2, w2, pad_x=56, pad_y=34, gap=22,
                    tr1=8, tr2=6):
    f1, f2 = font(s1, w1), font(s2, w2)
    _, tw1 = measure(l1, f1, tr1)
    _, tw2 = measure(l2, f2, tr2)
    g1, g2 = f1.getbbox(l1), f2.getbbox(l2)
    h1, h2 = g1[3] - g1[1], g2[3] - g2[1]
    block = h1 + gap + h2
    band, d = grid_canvas(max(tw1, tw2) + pad_x * 2, block + pad_y * 2)
    y0 = (band.height - block) // 2
    draw_tracked(d, l1, f1, tr1, (band.width - tw1) // 2, y0 - g1[1], TEXT)
    draw_tracked(d, l2, f2, tr2, (band.width - tw2) // 2,
                 y0 + h1 + gap - g2[1], SUB)
    return band


def canvas_with(bands_pos):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for band, (cx, top) in bands_pos:
        img.paste(band, (cx - band.width // 2, top), band)
    return img


def fit_hook(l1, l2):
    """Shrink hook text sizes until band fits within side margins (60px)."""
    s1, s2 = 64, 38
    while True:
        band = (make_band_2line(l1, s1, "Bold", l2, s2, "Medium") if l2
                else make_band(l1, s1, "Bold", pad_x=56, pad_y=34, tracking=8))
        if band.width <= W - 120 or s1 <= 40:
            return band
        s1 -= 4
        s2 = max(26, s2 - 2)


def build_overlays(outdir, hook1, hook2, title, telops):
    os.makedirs(outdir, exist_ok=True)
    # logo
    logo = Image.open(os.path.join(ASSETS, "logo_navy.png")).convert("RGBA")
    logo = logo.resize((150, 150), Image.LANCZOS)
    lg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lg.paste(logo, (60, 230), logo)
    lg.save(f"{outdir}/ov_logo.png")
    # hook
    hb = fit_hook(hook1.strip(), (hook2 or "").strip())
    canvas_with([(hb, (W // 2, 840 - hb.height // 2))]).save(f"{outdir}/ov_hook.png")
    # title (right-aligned, never reaches the logo at x<=250)
    size = 28
    tb = make_band(title, size, "Medium", pad_x=34, pad_y=16, tracking=3)
    while tb.width > 1020 - 250 and size > 20:
        size -= 2
        tb = make_band(title, size, "Medium", pad_x=30, pad_y=14, tracking=2)
    canvas_with([(tb, (1020 - tb.width // 2, 305 - tb.height // 2))]).save(
        f"{outdir}/ov_title.png")
    # telops
    paths = []
    for i, t in enumerate(telops):
        band = make_band(t.strip(), 42, "Medium", tracking=6)
        if band.width > W - 120:
            band = make_band(t.strip(), 36, "Medium", tracking=4)
        canvas_with([(band, (W // 2, 1500 - band.height - 8))]).save(
            f"{outdir}/ov_telop{i}.png")
        paths.append(f"{outdir}/ov_telop{i}.png")
    return paths
