#!/usr/bin/env python3
"""Build the animated SVG cards used by README.md.

    python3 scripts/cards/build.py          # offline: uses live.json if present, else defaults
    python3 scripts/cards/build.py --live   # refresh pub.dev versions and GitHub numbers first

Every card is written twice, once per GitHub theme (assets/cards/<name>-light.svg and
-dark.svg), and README.md swaps between them with a <picture> element. Inter is
subset per card and embedded, so the cards render identically everywhere.

Requires: fonttools, brotli   (pip install fonttools brotli)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[2]
FONT_SRC = ROOT / "scripts" / "cards" / "fonts" / "Inter-latin-variable.woff2"
OUT_DIR = ROOT / "assets" / "cards"
LIVE_FILE = ROOT / "scripts" / "cards" / "live.json"

# --------------------------------------------------------------------------- content

PRODUCTS = [
    # key, name, description lines, domain, href
    ("edie", "edie", ["AI video editing on your phone. Describe the cut,", "get a real timeline back, finish it by hand."], "edie.video", "https://edie.video"),
    ("splitfast", "SplitFast", ["Split group expenses from a link.", "No app, no signup."], "splitfast.app", "https://splitfast.app"),
    ("fluttertune", "fluttertune", ["AI performance tuning for Flutter apps.", "Diagnose jank, cut startup time."], "fluttertune.com", "https://fluttertune.com"),
    ("croomfs", "croomfs", ["Meet people your mutuals follow.", "Crush on a tweet, not a photo."], "croomfs.com", "https://croomfs.com"),
    ("turnitgenz", "Turn It Gen Z", ["Turn any text into Gen Z slang.", "Pick a vibe, translate for free."], "turnitgenz.com", "https://turnitgenz.com"),
]

HASH = ("hashstudios", "Hash Studios", "A two-person studio designing and building Flutter apps for founders. Design and build, no handoff.", "hashstudios.in", "https://hashstudios.in")

PACKAGES = [
    # key, pub.dev name, description lines
    ("bubbles_sheet", "bubbles_sheet", ["iOS 26-style modal sheets", "with detents and sticky CTAs."]),
    ("morph_route", "morph_route", ["Tile-to-screen container", "morph with blur and tilt."]),
    ("flywheel_carousel", "flywheel_carousel", ["Arc-shaped carousel. Flick,", "coast, snap to a card."]),
    ("flutter_mesh_transform", "flutter_mesh_transform", ["Spring-driven mesh warp", "for any widget."]),
    ("arsenal", "arsenal", ["Cyberpunk design system:", "components, theme, fonts."]),
    ("flip_card_swiper", "flip_card_swiper", ["Swipeable cards with flip", "animations and haptics."]),
    ("flutter_debug_tools", "flutter_debug_tools", ["In-app inspector for UI and", "performance issues."]),
    ("better_textfield", "better_textfield", ["Text fields that size", "themselves properly."]),
]

DEFAULT_LIVE = {
    "versions": {},
    "prism_stars": 662,
    "contributions": 6233,
    "prism_downloads": "250k+",
}

# --------------------------------------------------------------------------- tokens

THEMES = {
    "dark": dict(
        surface="#18181c",
        border_color="#ffffff", border_top=0.14, border_bottom=0.04,
        text="#f5f5f7", text2="rgba(235,235,245,0.62)", text3="rgba(235,235,245,0.34)",
        accent="#e8734a", accent_text="#f08a63", accent_soft="rgba(232,115,74,0.16)",
        tile="rgba(255,255,255,0.06)", tile2="rgba(255,255,255,0.11)", tile3="rgba(255,255,255,0.18)",
        ink="#0b0b12", on_accent="#fff7f3",
        shadow=False,
    ),
    "light": dict(
        surface="#fbfbfd",
        border_color="#000000", border_top=0.06, border_bottom=0.10,
        text="#1c1c1e", text2="rgba(60,60,67,0.64)", text3="rgba(60,60,67,0.38)",
        accent="#e8734a", accent_text="#c9582a", accent_soft="rgba(232,115,74,0.14)",
        tile="rgba(28,28,30,0.05)", tile2="rgba(28,28,30,0.10)", tile3="rgba(28,28,30,0.16)",
        ink="#1c1c1e", on_accent="#fff7f3",
        shadow=True,
    ),
}

FONT_STACK = "'Inter',-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',system-ui,sans-serif"
INSET = 8
RADIUS = 20
# GitHub renders the profile README in an 846px column; the grid is 840 with 16px gutters
GRID, WIDE, COL3, COL4 = 840, 560, 280, 210


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- fonts

class Fonts:
    """Static instances of the Inter variable font, measured and subset on demand."""

    def __init__(self, path: Path):
        self.path = path
        self._bytes: dict[int, bytes] = {}
        self._metrics: dict[int, tuple] = {}

    def _instance_bytes(self, weight: int) -> bytes:
        if weight not in self._bytes:
            vf = TTFont(self.path)
            inst = instancer.instantiateVariableFont(vf, {"wght": weight}, inplace=True)
            buf = io.BytesIO()
            inst.save(buf)
            self._bytes[weight] = buf.getvalue()
        return self._bytes[weight]

    def _metrics_for(self, weight: int):
        if weight not in self._metrics:
            f = TTFont(io.BytesIO(self._instance_bytes(weight)))
            self._metrics[weight] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
        return self._metrics[weight]

    def measure(self, text: str, weight: int, size: float, tracking: float = 0.0) -> float:
        cmap, hmtx, upm = self._metrics_for(weight)
        fallback = cmap[ord("?")]
        units = sum(hmtx[cmap.get(ord(ch), fallback)][0] for ch in text)
        return units * size / upm + tracking * max(len(text) - 1, 0)

    def digit_advance(self, weight: int, size: float) -> float:
        cmap, hmtx, upm = self._metrics_for(weight)
        return max(hmtx[cmap[ord(d)]][0] for d in "0123456789") * size / upm

    def face_css(self, weight: int, chars: str) -> str:
        f = TTFont(io.BytesIO(self._instance_bytes(weight)))
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.desubroutinize = True
        opts.name_IDs = [1, 2]
        opts.notdef_outline = True
        s = subset.Subsetter(opts)
        s.populate(text=chars)
        s.subset(f)
        buf = io.BytesIO()
        f.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return ("@font-face{font-family:'Inter';font-weight:%d;font-display:block;"
                "src:url(data:font/woff2;base64,%s) format('woff2')}" % (weight, b64))


FONTS = Fonts(FONT_SRC)


# --------------------------------------------------------------------------- card builder

class Card:
    def __init__(self, theme: str, w: int, h: int):
        self.t = THEMES[theme]
        self.theme = theme
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.css: list[str] = []
        self.defs: list[str] = []
        self.chars: dict[int, set] = defaultdict(set)
        self._ids = 0

    # visible surface bounds
    @property
    def x0(self): return INSET
    @property
    def y0(self): return INSET
    @property
    def x1(self): return self.w - INSET
    @property
    def y1(self): return self.h - INSET

    def uid(self, prefix="i") -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    def text(self, x, y, s, size, weight=400, fill=None, anchor="start", tracking=0.0, cls="", attrs=""):
        self.chars[weight].update(s)
        fill = fill or self.t["text"]
        ls = f' letter-spacing="{tracking}"' if tracking else ""
        c = f' class="{cls}"' if cls else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{ls}{c}{(" " + attrs) if attrs else ""}>{esc(s)}</text>'
        )

    def add(self, svg: str): self.parts.append(svg)
    def style(self, css: str):
        self.css.append(css.replace('$OUT', EASE_OUT).replace('$INOUT', EASE_INOUT).replace('$SPRING', EASE_SPRING))
    def define(self, svg: str): self.defs.append(svg)

    def clip(self, x, y, w, h, rx=0) -> str:
        cid = self.uid("clip")
        self.define(f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/></clipPath>')
        return cid

    def render(self) -> str:
        t = self.t
        faces = "".join(FONTS.face_css(w, "".join(sorted(chars))) for w, chars in sorted(self.chars.items()))
        base_css = (
            "text{font-family:%s;-webkit-font-smoothing:antialiased}"
            "@media (prefers-reduced-motion:reduce){*{animation:none!important}}" % FONT_STACK
        )
        border = (
            f'<linearGradient id="bd" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{t["border_color"]}" stop-opacity="{t["border_top"]}"/>'
            f'<stop offset="1" stop-color="{t["border_color"]}" stop-opacity="{t["border_bottom"]}"/>'
            f'</linearGradient>'
        )
        shadow = ""
        filt = ""
        if t["shadow"]:
            shadow = ('<filter id="sh" x="-6%" y="-10%" width="112%" height="130%">'
                      '<feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#1c1c1e" flood-opacity="0.07"/></filter>')
            filt = ' filter="url(#sh)"'
        surface = (
            f'<rect x="{self.x0 + 0.5}" y="{self.y0 + 0.5}" width="{self.x1 - self.x0 - 1}" height="{self.y1 - self.y0 - 1}" '
            f'rx="{RADIUS}" fill="{t["surface"]}" stroke="url(#bd)"{filt}/>'
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
            f'<style><![CDATA[{faces}{base_css}{"".join(self.css)}]]></style>'
            f'<defs>{border}{shadow}{"".join(self.defs)}</defs>'
            f'{surface}{"".join(self.parts)}</svg>'
        )


# --------------------------------------------------------------------------- shared motion

EASE_OUT = "cubic-bezier(.16,1,.3,1)"
EASE_INOUT = "cubic-bezier(.65,0,.35,1)"
EASE_SPRING = "cubic-bezier(.34,1.4,.64,1)"


def rise_css(count: int, base_delay=0.05, step=0.1) -> str:
    css = ("@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}"
           ".r{animation:rise .8s %s both}" % EASE_OUT)
    for i in range(count):
        css += ".r%d{animation-delay:%.2fs}" % (i + 1, base_delay + i * step)
    return css


# --------------------------------------------------------------------------- hero

def hero(theme: str) -> str:
    c = Card(theme, GRID, 260)
    t = c.t
    x = 40
    c.text(x, 88, "Abhay Maurya", 40, 700, t["text"], tracking=-1.4, cls="r r1")
    c.text(x, 120, "Flutter engineer and occasional designer, based in Bengaluru.", 16, 400, t["text2"], cls="r r2")

    label = "Open to freelance and early-stage work"
    pw = FONTS.measure(label, 500, 13) + 28
    c.add(f'<g class="r r3"><rect x="{x}" y="146" width="{pw:.0f}" height="30" rx="15" fill="{t["accent_soft"]}"/>')
    c.text(x + 14, 166, label, 13, 500, t["accent_text"])
    c.add("</g>")
    c.text(x, 226, "Previously at CRED", 13, 500, t["text3"], cls="r r4")
    c.style(rise_css(4))

    # a fanned deck of three cards: the recurring object in the work (cards, sheets, tiles)
    c.define(f'<linearGradient id="ac" x1="0" y1="0" x2="1" y2="1">'
             f'<stop offset="0" stop-color="#f28b5f"/><stop offset="1" stop-color="{t["accent"]}"/></linearGradient>')
    cx, cy = GRID - 184, 132
    cards = [
        ("k3", t["tile"]),
        ("k2", t["tile2"]),
        ("k1", "url(#ac)"),
    ]
    c.add(f'<g transform="translate({cx},{cy})"><g class="deck">')
    for cls, fill in cards:
        c.add(f'<g class="k {cls}"><rect x="-70" y="-46" width="140" height="92" rx="16" fill="{fill}"/>')
        if cls == "k1":
            c.add('<circle cx="-46" cy="-22" r="9" fill="#fff" fill-opacity=".45"/>'
                  '<rect x="-52" y="12" width="60" height="6" rx="3" fill="#fff" fill-opacity=".5"/>'
                  '<rect x="-52" y="24" width="36" height="6" rx="3" fill="#fff" fill-opacity=".3"/>')
        c.add("</g>")
    c.add("</g></g>")
    c.style(
        ".deck{animation:rise 1s %s both;animation-delay:.3s}" % EASE_OUT +
        ".k{transform-box:fill-box;transform-origin:50%% 85%%;animation:8s %s infinite}" % EASE_INOUT +
        ".k1{animation-name:fan1}.k2{animation-name:fan2}.k3{animation-name:fan3}"
        "@keyframes fan1{0%,18%{transform:none}48%,66%{transform:translate(28px,4px) rotate(11deg)}92%,100%{transform:none}}"
        "@keyframes fan2{0%,18%{transform:none}48%,66%{transform:translateY(-4px)}92%,100%{transform:none}}"
        "@keyframes fan3{0%,18%{transform:none}48%,66%{transform:translate(-28px,4px) rotate(-11deg)}92%,100%{transform:none}}"
    )
    return c.render()


# --------------------------------------------------------------------------- proof tiles

def stat(theme: str, value: str, label: str, index: int) -> str:
    c = Card(theme, COL4, 128)
    t = c.t
    size, weight = 34, 700
    x, y = 24, 70
    L = 44  # roll pitch
    colw = FONTS.digit_advance(weight, size)
    delay = 0.1 + index * 0.12
    col = 0
    c.style(".d{animation:1.5s %s both}" % EASE_OUT)
    for ch in value:
        if ch.isdigit():
            d = int(ch)
            cid = c.clip(x - 1, y - 30, colw + 2, 40)
            c.add(f'<g clip-path="url(#{cid})"><g class="d roll{d}" style="animation-delay:{delay + col * 0.09:.2f}s">')
            for i in range(20):
                c.text(x + colw / 2, y + i * L, str(i % 10), size, weight, t["text"], anchor="middle")
            c.add("</g></g>")
            c.style("@keyframes roll%d{from{transform:translateY(0)}to{transform:translateY(-%dpx)}}.roll%d{animation-name:roll%d}" % (d, (10 + d) * L, d, d))
            x += colw
            col += 1
        else:
            c.text(x, y, ch, size, weight, t["text"])
            x += FONTS.measure(ch, weight, size)
    c.text(24, 98, label, 13, 400, t["text2"])
    return c.render()


# --------------------------------------------------------------------------- product cards

def product_frame(c: Card, w: int, name: str, lines: list[str], domain: str):
    t = c.t
    c.text(24, 160, name, 18, 600, t["text"], tracking=-0.3)
    for i, line in enumerate(lines):
        c.text(24, 183 + i * 18, line, 13, 400, t["text2"])
    c.text(w - 24, 228, domain, 12, 500, t["text3"], anchor="end")


def motif_area(c: Card, w: int, h: int = 108):
    """Returns (clip id, x, y, w, h) for the motif panel at the top of a card."""
    x, y, mw = 16, 16, w - 32
    cid = c.clip(x, y, mw, h, 14)
    return cid, x, y, mw, h


def edie_motif(c: Card, w: int):
    t = c.t
    cid, x, y, mw, mh = motif_area(c, w)
    bg = "#1f1a18" if c.theme == "dark" else "#f6e9e2"
    lane = "rgba(255,255,255,0.05)" if c.theme == "dark" else "rgba(28,28,30,0.05)"
    clip_fill = "rgba(255,255,255,0.16)" if c.theme == "dark" else "rgba(28,28,30,0.14)"
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="{bg}"/>')
    c.define('<radialGradient id="glow" cx="0.15" cy="0.5" r="0.7">'
             f'<stop offset="0" stop-color="{t["accent"]}" stop-opacity=".28"/><stop offset="1" stop-color="{t["accent"]}" stop-opacity="0"/></radialGradient>')
    c.add(f'<rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="url(#glow)"/>')
    rows = [y + 20, y + 46, y + 72]
    for ry in rows:
        c.add(f'<rect x="{x + 16}" y="{ry}" width="{mw - 32}" height="18" rx="5" fill="{lane}"/>')
    clips = [(0, 0, 150, False), (0, 164, 96, False), (0, 274, 190, True),
             (1, 30, 130, False), (1, 174, 160, False), (1, 350, 110, False),
             (2, 0, 110, True), (2, 126, 70, False), (2, 212, 220, False)]
    for row, start, width, accent in clips:
        fill = t["accent"] if accent else clip_fill
        c.add(f'<rect x="{x + 16 + start}" y="{rows[row]}" width="{width}" height="18" rx="5" fill="{fill}"/>')
    # the cut appears once the playhead passes it
    cut_x = x + 16 + int((mw - 32) * 0.46)
    c.add(f'<rect class="cut" x="{cut_x}" y="{rows[1] - 2}" width="3" height="22" fill="{bg}"/>')
    # playhead
    c.add(f'<g transform="translate({x + 16},{y + 6})"><g class="ph">'
          f'<rect x="-1" y="0" width="2" height="{mh - 12}" fill="{t["accent"]}"/>'
          f'<rect x="-5" y="0" width="10" height="9" rx="2.5" fill="{t["accent"]}"/></g></g>')
    c.add("</g>")
    travel = mw - 32
    c.style(
        ".ph{animation:scrub 7s linear infinite}"
        "@keyframes scrub{from{transform:translateX(0)}to{transform:translateX(%dpx)}}" % travel +
        ".cut{animation:cut 7s linear infinite}"
        "@keyframes cut{0%,45%{opacity:0}47%,100%{opacity:1}}"
    )


def splitfast_motif(c: Card, w: int):
    t = c.t
    cid, x, y, mw, mh = motif_area(c, w)
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="{t["tile"]}"/>')
    bx, by = x + (mw - 204) / 2, y + 28
    segs = [(0, 84, t["accent"]), (88, 56, t["tile3"]), (148, 56, t["tile3"])]
    for i, (sx, sw, fill) in enumerate(segs):
        c.add(f'<g class="s s{i + 1}"><rect x="{bx + sx}" y="{by}" width="{sw}" height="26" rx="13" fill="{fill}"/>'
              f'<circle cx="{bx + sx + sw / 2}" cy="{by + 54}" r="9" fill="{fill}" class="av"/></g>')
    c.add("</g>")
    c.style(
        ".s{animation:6s $INOUT infinite}"
        ".s1{animation-name:sl}.s3{animation-name:sr}"
        "@keyframes sl{0%,22%{transform:none}42%,72%{transform:translateX(-16px)}92%,100%{transform:none}}"
        "@keyframes sr{0%,22%{transform:none}42%,72%{transform:translateX(16px)}92%,100%{transform:none}}"
        ".av{animation:avatar 6s $INOUT infinite}"
        "@keyframes avatar{0%,28%{opacity:0;transform:translateY(6px)}44%,72%{opacity:1;transform:none}90%,100%{opacity:0;transform:translateY(6px)}}"
    )


def fluttertune_motif(c: Card, w: int):
    t = c.t
    cid, x, y, mw, mh = motif_area(c, w)
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="{t["tile"]}"/>')
    rnd = random.Random(7)
    n = 22
    bw, gap = 6, 4
    bx = x + (mw - (n * (bw + gap) - gap)) / 2
    base = y + mh - 16
    settle = 20
    c.add(f'<line x1="{x + 12}" y1="{base - settle}" x2="{x + mw - 12}" y2="{base - settle}" stroke="{t["text3"]}" stroke-width="1" stroke-dasharray="3 4"/>')
    c.style(".b{transform-box:fill-box;transform-origin:50% 100%;animation:7s $INOUT infinite}")
    for i in range(n):
        H = rnd.choice([14, 22, 30, 48, 62, 26, 18, 70, 36])
        fill = t["accent"] if H > 44 else t["tile3"]
        k = settle / H
        c.add(f'<rect class="b b{i}" x="{bx + i * (bw + gap)}" y="{base - H}" width="{bw}" height="{H}" rx="2" fill="{fill}"/>')
        calm = f";fill:{t['tile3']}" if H > 44 else ""
        spike = f";fill:{t['accent']}" if H > 44 else ""
        c.style("@keyframes bar%d{0%%,32%%{transform:scaleY(1)%s}52%%,86%%{transform:scaleY(%.3f)%s}100%%{transform:scaleY(1)%s}}"
                ".b%d{animation-name:bar%d;animation-delay:%.2fs}" % (i, spike, k, calm, spike, i, i, i * 0.02))
    c.text(x + mw - 14, y + 24, "60 fps", 12, 600, t["accent_text"], anchor="end", cls="fps")
    c.add("</g>")
    c.style(
        ".fps{animation:fps 7s $INOUT infinite}"
        "@keyframes fps{0%,40%{opacity:0}54%,86%{opacity:1}100%{opacity:0}}"
    )


def croomfs_motif(c: Card, w: int):
    t = c.t
    cid, x, y, mw, mh = motif_area(c, w)
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="{t["tile"]}"/>')
    p1, p2, p3 = (x + 52, y + 64), (x + mw / 2, y + 40), (x + mw - 52, y + 64)
    d = f"M{p1[0]},{p1[1]} L{p2[0]},{p2[1]} L{p3[0]},{p3[1]}"
    length = 2 * ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
    c.add(f'<path class="link" d="{d}" fill="none" stroke="{t["text3"]}" stroke-width="2" stroke-linecap="round" stroke-dasharray="{length:.0f}" stroke-dashoffset="{length:.0f}"/>')
    for i, (px, py) in enumerate((p1, p2, p3)):
        fill = t["accent"] if i == 0 else t["tile3"]
        c.add(f'<circle cx="{px}" cy="{py}" r="15" fill="{fill}"/>')
    c.add(f'<circle class="ring" cx="{p3[0]}" cy="{p3[1]}" r="15" fill="none" stroke="{t["accent"]}" stroke-width="2"/>')
    c.add(f'<circle class="dot" cx="{p3[0]}" cy="{p3[1]}" r="15" fill="{t["accent"]}"/>')
    c.add("</g>")
    c.style(
        ".link{animation:draw 6s %s infinite}" % EASE_INOUT +
        "@keyframes draw{0%%,10%%{stroke-dashoffset:%d}45%%,88%%{stroke-dashoffset:0}100%%{stroke-dashoffset:%d}}" % (length, length) +
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 6s %s infinite}" % EASE_OUT +
        "@keyframes ring{0%,46%{transform:scale(1);opacity:0}50%{opacity:1}72%,100%{transform:scale(1.9);opacity:0}}"
        ".dot{animation:dot 6s linear infinite}"
        "@keyframes dot{0%,46%{opacity:0}52%,88%{opacity:1}94%,100%{opacity:0}}"
    )


def turnitgenz_motif(c: Card, w: int):
    t = c.t
    cid, x, y, mw, mh = motif_area(c, w)
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{mw}" height="{mh}" fill="{t["tile"]}"/>')
    cx, cy = x + mw / 2, y + mh / 2 + 6
    c.text(cx, cy, "That was genuinely impressive.", 15, 500, t["text2"], anchor="middle", cls="t1")
    c.text(cx, cy, "it's giving main character", 16, 600, t["accent_text"], anchor="middle", cls="t2")
    c.add("</g>")
    c.style(
        ".t1,.t2{animation:6s %s infinite}" % EASE_INOUT +
        ".t1{animation-name:t1}.t2{animation-name:t2}"
        "@keyframes t1{0%,38%{opacity:1;transform:none}48%{opacity:0;transform:translateY(-14px)}90%{opacity:0;transform:translateY(14px)}100%{opacity:1;transform:none}}"
        "@keyframes t2{0%,38%{opacity:0;transform:translateY(14px)}48%,88%{opacity:1;transform:none}98%,100%{opacity:0;transform:translateY(-14px)}}"
    )


def product(theme: str, key: str, name: str, lines: list[str], domain: str, wide: bool = False) -> str:
    w = WIDE if wide else COL3
    c = Card(theme, w, 248)
    {"edie": edie_motif, "splitfast": splitfast_motif, "fluttertune": fluttertune_motif,
     "croomfs": croomfs_motif, "turnitgenz": turnitgenz_motif}[key](c, w)
    product_frame(c, w, name, lines, domain)
    return c.render()


def hash_strip(theme: str) -> str:
    key, name, desc, domain, _ = HASH
    c = Card(theme, GRID, 120)
    t = c.t
    # the hash mark draws itself, stroke by stroke
    ox, oy = 34, 30
    strokes = [(ox + 16, oy + 2, ox + 12, oy + 58), (ox + 40, oy + 2, ox + 36, oy + 58),
               (ox + 2, oy + 22, ox + 54, oy + 22), (ox - 2, oy + 40, ox + 50, oy + 40)]
    c.style(".hs{animation:hash 7s $OUT infinite}")
    for i, (x1, y1, x2, y2) in enumerate(strokes):
        c.add(f'<line class="hs h{i}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["accent"]}" stroke-width="7" stroke-linecap="round" stroke-dasharray="70" stroke-dashoffset="75"/>')
        c.style(".h%d{animation-delay:%.2fs}" % (i, i * 0.18))
    c.style(
        "@keyframes hash{0%{stroke-dashoffset:75;opacity:1}18%,80%{stroke-dashoffset:0;opacity:1}90%,100%{stroke-dashoffset:0;opacity:0}}"
    )
    c.text(112, 54, name, 18, 600, t["text"], tracking=-0.3)
    c.text(112, 78, desc, 13, 400, t["text2"])
    c.text(GRID - 24, 66, domain, 12, 500, t["text3"], anchor="end")
    return c.render()


# --------------------------------------------------------------------------- package cards

def pkg_area(c: Card):
    x, y, w, h = 16, 16, COL4 - 32, 84
    cid = c.clip(x, y, w, h, 12)
    c.add(f'<g clip-path="url(#{cid})"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c.t["tile"]}"/>')
    return x, y, w, h


def m_bubbles_sheet(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    c.add(f'<rect class="scrim" x="{x}" y="{y}" width="{w}" height="{h}" fill="{t["ink"]}"/>')
    sheet = "#2a2a30" if c.theme == "dark" else "#ffffff"
    c.add(f'<g transform="translate({x + 32},{y + h})"><g class="sheet">'
          f'<rect x="0" y="0" width="120" height="100" rx="18" fill="{sheet}" stroke="{t["tile3"]}"/>'
          f'<rect x="44" y="8" width="32" height="4" rx="2" fill="{t["text3"]}"/>'
          f'<rect x="14" y="24" width="70" height="7" rx="3.5" fill="{t["tile3"]}"/>'
          f'<rect x="14" y="38" width="50" height="7" rx="3.5" fill="{t["tile2"]}"/>'
          f'<rect x="14" y="56" width="92" height="20" rx="10" fill="{t["accent"]}"/>'
          f'</g></g></g>')
    c.style(
        ".sheet{animation:sheet 6s $OUT infinite}"
        "@keyframes sheet{0%,8%{transform:translateY(0)}22%,42%{transform:translateY(-40px)}58%,82%{transform:translateY(-72px)}96%,100%{transform:translateY(0)}}"
        ".scrim{animation:scrim 6s $OUT infinite}"
        "@keyframes scrim{0%,8%{opacity:0}22%,42%{opacity:.14}58%,82%{opacity:.26}96%,100%{opacity:0}}"
    )


def m_morph_route(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    tx, ty = x + 20, y + 30
    c.add(f'<rect class="tile" x="{tx}" y="{ty}" width="24" height="24" rx="7" fill="{t["accent"]}"/>')
    c.add(f'<g class="big"><rect x="{x + 40}" y="{y + 10}" width="{w - 56}" height="{h - 20}" rx="12" fill="{t["accent"]}"/>'
          f'<rect x="{x + 54}" y="{y + 26}" width="60" height="7" rx="3.5" fill="#fff" fill-opacity=".55"/>'
          f'<rect x="{x + 54}" y="{y + 40}" width="40" height="7" rx="3.5" fill="#fff" fill-opacity=".3"/></g>')
    c.style(
        ".tile{transform-box:fill-box;transform-origin:center;animation:tile 5s $INOUT infinite}"
        "@keyframes tile{0%,18%{transform:scale(1);opacity:1}40%,68%{transform:scale(5);opacity:0}86%,100%{transform:scale(1);opacity:1}}"
        ".big{transform-box:view-box;transform-origin:" + f"{tx + 12}px {ty + 12}px" + ";animation:big 5s $INOUT infinite}"
        "@keyframes big{0%,18%{transform:scale(.2);opacity:0}40%,68%{transform:scale(1);opacity:1}86%,100%{transform:scale(.2);opacity:0}}"
    )
    c.add("</g>")


def m_flywheel(c: Card):
    import math
    t = c.t
    x, y, w, h = pkg_area(c)
    cx, cy, R = x + w / 2, y + 150, 116
    c.add(f'<g class="wheel">')
    for a in range(-75, 76, 15):
        rad = math.radians(a)
        px, py = cx + R * math.sin(rad), cy - R * math.cos(rad)
        fill = t["accent"] if a == 0 else t["tile3"]
        c.add(f'<g transform="translate({px:.1f},{py:.1f}) rotate({a})"><rect x="-11" y="-15" width="22" height="30" rx="5" fill="{fill}"/></g>')
    c.add("</g></g>")
    c.style(
        ".wheel{transform-box:view-box;transform-origin:%.1fpx %.1fpx;animation:spin 5s infinite}" % (cx, cy) +
        "@keyframes spin{0%,6%{transform:rotate(0);animation-timing-function:cubic-bezier(.2,.9,.3,1)}38%,56%{transform:rotate(-30deg);animation-timing-function:cubic-bezier(.65,0,.35,1)}88%,100%{transform:rotate(0)}}"
    )


def m_mesh(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    cols, rows = 9, 4
    for r in range(rows):
        for col in range(cols):
            px, py = x + 20 + col * 18, y + 15 + r * 18
            fill = t["accent"] if (col - r) in (2, 3) else t["tile3"]
            delay = -(col * 0.13 + r * 0.11)
            c.add(f'<circle class="m" cx="{px}" cy="{py}" r="3" fill="{fill}" style="animation-delay:{delay:.2f}s"/>')
    c.add("</g>")
    c.style(
        ".m{animation:mesh 2.6s %s infinite alternate}" % EASE_SPRING +
        "@keyframes mesh{from{transform:translate(0,0)}to{transform:translate(6px,-8px)}}"
    )


def m_arsenal(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    ch = 9
    L, T_, R_, B = x + 18, y + 14, x + w - 18, y + h - 14
    poly = f"{L + ch},{T_} {R_},{T_} {R_},{B - ch} {R_ - ch},{B} {L},{B} {L},{T_ + ch}"
    c.add(f'<g class="gl"><polygon points="{poly}" fill="{t["accent_soft"]}" stroke="{t["accent"]}" stroke-width="1.5"/>')
    for i, bw in enumerate((70, 46, 58)):
        c.add(f'<rect x="{L + 14}" y="{T_ + 14 + i * 13}" width="{bw}" height="5" rx="2.5" fill="{t["accent"]}" fill-opacity="{.85 - i * .25}"/>')
    c.add(f'<path d="M{R_ - 26},{T_ + 12} h14 v14" fill="none" stroke="{t["accent"]}" stroke-width="1.5"/>'
          f'<path d="M{L + 26},{B - 12} h-14 v-14" fill="none" stroke="{t["accent"]}" stroke-width="1.5"/></g>')
    c.define(f'<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["accent"]}" stop-opacity="0"/>'
             f'<stop offset="1" stop-color="{t["accent"]}" stop-opacity=".45"/></linearGradient>')
    c.add(f'<rect class="scan" x="{L}" y="{T_ - 12}" width="{R_ - L}" height="12" fill="url(#scan)"/>')
    c.add("</g>")
    c.style(
        ".scan{animation:scan 3.2s linear infinite}"
        "@keyframes scan{from{transform:translateY(0)}to{transform:translateY(%dpx)}}" % (B - T_ + 12) +
        ".gl{animation:glitch 4s steps(1) infinite}"
        "@keyframes glitch{0%,88%{transform:none}89%{transform:translateX(-3px)}91%{transform:translateX(3px)}93%,100%{transform:none}}"
    )


def m_flip(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    cx, cy = x + w / 2, y + h / 2
    c.add(f'<rect x="{cx - 27 + 8}" y="{cy - 37 + 6}" width="54" height="74" rx="8" fill="{t["tile2"]}"/>')
    c.add(f'<g class="fc"><g class="front"><rect x="{cx - 27}" y="{cy - 37}" width="54" height="74" rx="8" fill="{t["tile3"]}"/>'
          f'<rect x="{cx - 17}" y="{cy - 22}" width="34" height="6" rx="3" fill="{t["text3"]}"/>'
          f'<rect x="{cx - 17}" y="{cy - 10}" width="22" height="6" rx="3" fill="{t["text3"]}"/></g>'
          f'<g class="back"><rect x="{cx - 27}" y="{cy - 37}" width="54" height="74" rx="8" fill="{t["accent"]}"/>'
          f'<circle cx="{cx}" cy="{cy}" r="12" fill="#fff" fill-opacity=".5"/></g></g>')
    c.add("</g>")
    c.style(
        ".fc{transform-box:fill-box;transform-origin:center;animation:flip 4.5s %s infinite}" % EASE_INOUT +
        "@keyframes flip{0%,20%{transform:scaleX(1)}38%,62%{transform:scaleX(-1)}80%,100%{transform:scaleX(1)}}"
        ".front{animation:front 4.5s linear infinite}.back{animation:back 4.5s linear infinite}"
        "@keyframes front{0%,29%{opacity:1}29.1%,70.9%{opacity:0}71%,100%{opacity:1}}"
        "@keyframes back{0%,29%{opacity:0}29.1%,70.9%{opacity:1}71%,100%{opacity:0}}"
    )


def m_debug(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    blocks = [(x + 18, y + 14, 148, 12), (x + 18, y + 34, 66, 36), (x + 94, y + 34, 72, 16), (x + 94, y + 56, 72, 14)]
    for bx, by, bw, bh in blocks:
        c.add(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="4" fill="{t["tile3"]}"/>')
    for i, (bx, by, bw, bh) in enumerate(blocks[1:]):
        c.add(f'<g class="ins i{i}"><rect x="{bx - 2}" y="{by - 2}" width="{bw + 4}" height="{bh + 4}" rx="5" fill="{t["accent_soft"]}" stroke="{t["accent"]}" stroke-width="1.5" stroke-dasharray="4 3"/>')
        c.text(bx + bw / 2, by + bh / 2 + 3, f"{bw} × {bh}", 9, 600, t["accent_text"], anchor="middle")
        c.add("</g>")
    c.add("</g>")
    c.style(
        ".ins{animation:ins 6s steps(1) infinite;opacity:0}"
        ".i0{animation-name:ins0}.i1{animation-name:ins1}.i2{animation-name:ins2}"
        "@keyframes ins0{0%,32%{opacity:1}33%,100%{opacity:0}}"
        "@keyframes ins1{0%,32%{opacity:0}33%,65%{opacity:1}66%,100%{opacity:0}}"
        "@keyframes ins2{0%,65%{opacity:0}66%,98%{opacity:1}99%,100%{opacity:0}}"
    )


def m_textfield(c: Card):
    t = c.t
    x, y, w, h = pkg_area(c)
    fx, fy, fw = x + 22, y + 14, 140
    field = "#2a2a30" if c.theme == "dark" else "#ffffff"
    for i, fh in enumerate((24, 40, 56)):
        c.add(f'<rect class="f f{i}" x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="8" fill="{field}" stroke="{t["accent"]}" stroke-width="1.5"/>')
    for i, lw in enumerate((92, 68, 44)):
        c.add(f'<rect class="ln l{i}" x="{fx + 12}" y="{fy + 9 + i * 16}" width="{lw}" height="6" rx="3" fill="{t["text3"]}"/>')
        c.add(f'<rect class="caret c{i}" x="{fx + 12 + lw + 4}" y="{fy + 6 + i * 16}" width="1.5" height="12" fill="{t["accent"]}"/>')
    c.add("</g>")
    c.style(
        ".f,.ln,.caret{animation:6s linear infinite;opacity:0}"
        ".f0{animation-name:f0}.f1{animation-name:f1}.f2{animation-name:f2}"
        "@keyframes f0{0%,30%{opacity:1}31%,100%{opacity:0}}"
        "@keyframes f1{0%,30%{opacity:0}31%,60%{opacity:1}61%,100%{opacity:0}}"
        "@keyframes f2{0%,60%{opacity:0}61%,92%{opacity:1}93%,100%{opacity:0}}"
        ".l0{animation-name:l0}.l1{animation-name:l1}.l2{animation-name:l2}"
        "@keyframes l0{0%,4%{opacity:0}5%,92%{opacity:1}93%,100%{opacity:0}}"
        "@keyframes l1{0%,31%{opacity:0}32%,92%{opacity:1}93%,100%{opacity:0}}"
        "@keyframes l2{0%,61%{opacity:0}62%,92%{opacity:1}93%,100%{opacity:0}}"
        ".c0{animation-name:c0}.c1{animation-name:c1}.c2{animation-name:c2}"
        "@keyframes c0{0%,5%{opacity:0}6%,10%{opacity:1}11%,15%{opacity:0}16%,20%{opacity:1}21%,25%{opacity:0}26%,30%{opacity:1}31%,100%{opacity:0}}"
        "@keyframes c1{0%,32%{opacity:0}33%,38%{opacity:1}39%,44%{opacity:0}45%,50%{opacity:1}51%,56%{opacity:0}57%,60%{opacity:1}61%,100%{opacity:0}}"
        "@keyframes c2{0%,62%{opacity:0}63%,68%{opacity:1}69%,74%{opacity:0}75%,80%{opacity:1}81%,86%{opacity:0}87%,92%{opacity:1}93%,100%{opacity:0}}"
    )


PKG_MOTIFS = {
    "bubbles_sheet": m_bubbles_sheet, "morph_route": m_morph_route, "flywheel_carousel": m_flywheel,
    "flutter_mesh_transform": m_mesh, "arsenal": m_arsenal, "flip_card_swiper": m_flip,
    "flutter_debug_tools": m_debug, "better_textfield": m_textfield,
}


def package(theme: str, key: str, name: str, lines: list[str], version: str | None) -> str:
    c = Card(theme, COL4, 196)
    t = c.t
    PKG_MOTIFS[key](c)
    c.text(24, 128, name, 14, 600, t["text"], tracking=-0.2)
    if version:
        label = "v" + version
        pw = FONTS.measure(label, 500, 10) + 14
        right = COL4 - 22
        c.add(f'<rect x="{right - pw:.1f}" y="22" width="{pw:.1f}" height="18" rx="9" fill="{t["surface"]}" fill-opacity=".92"/>')
        c.text(right - pw / 2, 34.5, label, 10, 500, t["text3"], anchor="middle")
    for i, line in enumerate(lines):
        c.text(24, 149 + i * 17, line, 12, 400, t["text2"])
    return c.render()


# --------------------------------------------------------------------------- link chips

def chip(theme: str, label: str, primary: bool = False) -> str:
    tw = FONTS.measure(label, 500, 13)
    w = int(tw + 40 + 2 * INSET)
    c = Card(theme, w, 36 + 2 * INSET)
    t = c.t
    # chips are flat pills, not cards: cover the surface with the pill fill
    fill = t["accent"] if primary else t["tile"]
    color = t["on_accent"] if primary else t["text"]
    c.add(f'<rect x="{INSET}" y="{INSET}" width="{w - 2 * INSET}" height="36" rx="18" fill="{t["surface"]}"/>'
          f'<rect x="{INSET}" y="{INSET}" width="{w - 2 * INSET}" height="36" rx="18" fill="{fill}"/>')
    c.text(w / 2, INSET + 23, label, 13, 500, color, anchor="middle")
    return c.render()


# --------------------------------------------------------------------------- live data

def fetch_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "profile-cards", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def refresh_live() -> dict:
    live = dict(DEFAULT_LIVE)
    live["versions"] = {}
    for key, name, _ in PACKAGES:
        try:
            live["versions"][name] = fetch_json(f"https://pub.dev/api/packages/{name}")["latest"]["version"]
        except Exception as e:  # keep going; a stale version beats a broken build
            print(f"warn: pub.dev {name}: {e}", file=sys.stderr)
    token = os.environ.get("GITHUB_TOKEN")
    gh_headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        live["prism_stars"] = fetch_json("https://api.github.com/repos/Hash-Studios/Prism", gh_headers)["stargazers_count"]
    except Exception as e:
        print(f"warn: prism stars: {e}", file=sys.stderr)
    if token:
        try:
            q = {"query": '{ user(login:"LiquidatorCoder"){ contributionsCollection { contributionCalendar { totalContributions } } } }'}
            req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps(q).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "profile-cards"})
            with urllib.request.urlopen(req, timeout=20) as r:
                live["contributions"] = json.load(r)["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        except Exception as e:
            print(f"warn: contributions: {e}", file=sys.stderr)
    LIVE_FILE.write_text(json.dumps(live, indent=2) + "\n")
    return live


def load_live() -> dict:
    if LIVE_FILE.exists():
        return {**DEFAULT_LIVE, **json.loads(LIVE_FILE.read_text())}
    return dict(DEFAULT_LIVE)


def fmt_contributions(n: int) -> str:
    return f"{(n // 500) * 500:,}+"


# --------------------------------------------------------------------------- main

def build(live: dict):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []

    def out(name: str, theme: str, svg: str):
        p = OUT_DIR / f"{name}-{theme}.svg"
        p.write_text(svg)
        written.append((p.name, len(svg)))

    for theme in THEMES:
        out("hero", theme, hero(theme))
        stats = [
            (live["prism_downloads"], "downloads on Prism"),
            (f"{live['prism_stars']:,}", "GitHub stars on Prism"),
            (fmt_contributions(live["contributions"]), "contributions last year"),
            (str(len(PACKAGES)), "packages on pub.dev"),
        ]
        for i, (value, label) in enumerate(stats):
            out(f"stat-{i + 1}", theme, stat(theme, value, label, i))
        for key, name, lines, domain, _ in PRODUCTS:
            out(f"product-{key}", theme, product(theme, key, name, lines, domain, wide=(key == "edie")))
        out("hashstudios", theme, hash_strip(theme))
        for key, name, lines in PACKAGES:
            out(f"pkg-{key}", theme, package(theme, key, name, lines, live["versions"].get(name)))
        for label, primary in (("Portfolio", False), ("LinkedIn", False), ("X", False), ("Contact", True)):
            out(f"chip-{label.lower()}", theme, chip(theme, label, primary))
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="refresh pub.dev versions and GitHub numbers first")
    args = ap.parse_args()
    live = refresh_live() if args.live else load_live()
    files = build(live)
    total = sum(n for _, n in files)
    print(f"wrote {len(files)} files, {total / 1024:.0f} KB total")
    for name, n in files:
        if n > 120_000:
            print(f"  large: {name} {n / 1024:.0f} KB")
