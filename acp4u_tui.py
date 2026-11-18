#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACP4U Parts — full-screen terminal app (mouse + keyboard).

Built on Textual; the API layer and helpers come from acp4u.py (the classic CLI).
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acp4u as core  # noqa: E402
from acp4u import (API, APIError, acct_name, ago, car_line, details_rows, key_name,  # noqa: E402
                   money, names, parse_t, perms_list, s, to_num)

from rich import box  # noqa: E402
from rich.console import Group  # noqa: E402
from rich.json import JSON as RJSON  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402
from textual import on, work  # noqa: E402
from textual.app import App, ComposeResult, SystemCommand  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.screen import ModalScreen, Screen  # noqa: E402
from textual.suggester import SuggestFromList  # noqa: E402
from textual.theme import Theme  # noqa: E402
from textual.widgets import (Button, Collapsible, ContentSwitcher, DataTable, Digits,  # noqa: E402
                             DirectoryTree, Footer, Input, Label, OptionList, Select, Static,
                             Switch, TabbedContent, TabPane, TextArea)
from textual.widgets.option_list import Option  # noqa: E402

VERSION = "2.1.0"
INSTALL_REPO = "eris4444/acp4u-cli"
C1, C2, C3, OK, BAD, MUTED = core.C1, core.C2, core.C3, core.OK, core.BAD, core.MUTED

THEME = Theme(
    name="acp4u", primary="#00d7ff", secondary="#ff5fd7", accent="#ffd75f", warning="#ffaf00",
    error="#ff5f6d", success="#5fff87", foreground="#e4e4f0", background="#0a0a16", surface="#11112a",
    panel="#1c1c3c", boost="#262650", dark=True,
)

BRANDS = ["Toyota", "Kia", "Hyundai", "Nissan", "Honda", "Mazda", "Mitsubishi", "Lexus", "Suzuki", "Subaru",
          "BMW", "Mercedes-Benz", "Audi", "Volkswagen", "Porsche", "Land Rover", "Jeep", "Dodge", "Chevrolet",
          "Ford", "GMC", "Cadillac", "Infiniti", "Peugeot", "Renault", "Citroen", "Iran Khodro", "Saipa", "MVM",
          "Chery", "Geely", "Haval", "Great Wall", "JAC", "Changan", "BYD", "Tesla", "Volvo", "Skoda", "Opel"]
SIDES = ["Front left", "Front right", "Rear left", "Rear right", "Front", "Rear", "Left", "Right"]
FUELS = ["Petrol", "Diesel", "Electric", "Hybrid"]
VTYPES = ["Saloon / Sedan", "SUV / 4x4", "Hatchback", "Pickup", "Van", "Coupe", "Truck", "Bus"]
IMG_EXT = core.IMG_EXT

MINI_FONT = {
    "A": ["▄▀█", "█▀█"], "C": ["█▀▀", "█▄▄"], "P": ["█▀█", "█▀▀"], "4": ["█ █", "▀▀█"], "U": ["█ █", "█▄█"],
}


def mini_logo() -> Text:
    lines = [" ".join(MINI_FONT[c][i] for c in "ACP4U") for i in range(2)]
    t = core.grad_text(lines)
    t.append("\n")
    t.append("  P A R T S", style=f"bold {C3}")
    return t


def big_logo() -> Text:
    return core.grad_text(["".join(core.FONT[ch][i] for ch in "ACP4U") for i in range(6)])


def badge(st) -> Text:
    return core.badge(st)


def badge_short(st) -> Text:
    st = s(st) or "?"
    col = core.STATUS_COLOR.get(st, "#8a8a8a")
    return Text(f" {core.STATUS_ICON.get(st, '•')} {core.STATUS_SHORT.get(st, st)} ", style=f"bold #0a0a16 on {col}")


def short(v, n: int = 40) -> str:
    v = s(v)
    return v if len(v) <= n else v[: n - 1] + "…"


def render_img(data: bytes, w: int, h: int) -> Text | None:
    """Half-block image: every cell shows two pixels, so photos look square."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        return None
    w, h = max(4, w), max(2, h)
    W, H = im.size
    sc = min(w / W, (h * 2) / H)
    nw, nh = max(2, int(W * sc)), max(2, int(H * sc))
    nh -= nh % 2
    im = im.resize((nw, nh), Image.LANCZOS)
    px = im.load()
    t = Text()
    for y in range(0, nh, 2):
        for x in range(nw):
            a, b = px[x, y], px[x, y + 1]
            t.append("▀", style=f"rgb({a[0]},{a[1]},{a[2]}) on rgb({b[0]},{b[1]},{b[2]})")
        if y + 2 < nh:
            t.append("\n")
    return t


# ── image engine: Sixel / Kitty graphics when the terminal can, colour blocks otherwise ──
IMG_MODE = "detailed"
SHARP_CLS = None
TEXT_MODE = "detailed"      # how photos are drawn without Sixel / Kitty: "detailed" or "half"
IMG_MODES = {"auto": "auto-detect (best the terminal can)",
             "sixel": "Sixel — real pixels (Konsole, WezTerm, foot, mlterm…)",
             "kitty": "Kitty graphics — real pixels (Kitty, Ghostty)",
             "detailed": "detailed blocks — sharpest text mode, any terminal",
             "blocks": "half blocks — smoothest, any terminal"}


def setup_images(pref: str | None) -> None:
    """Pick how photos are drawn. Must run before the app starts (it asks the terminal)."""
    global IMG_MODE, SHARP_CLS, TEXT_MODE
    pref = (os.environ.get("ACP4U_IMAGES") or pref or "auto").lower()
    TEXT_MODE = "half" if pref in ("blocks", "half") else "detailed"
    IMG_MODE, SHARP_CLS = ("blocks" if TEXT_MODE == "half" else "detailed"), None
    if pref in ("blocks", "half", "detailed") or not (sys.stdout.isatty() and sys.stdin.isatty()):
        return
    try:
        import textual_image.renderable as R
        import textual_image.widget as W
    except Exception:
        return
    if pref == "sixel":
        IMG_MODE, SHARP_CLS = "sixel", W.SixelImage
    elif pref in ("kitty", "tgp"):
        IMG_MODE, SHARP_CLS = "kitty", W.TGPImage
    elif R.Image is R.SixelImage:
        IMG_MODE, SHARP_CLS = "sixel", W.SixelImage
    elif R.Image is R.TGPImage:
        IMG_MODE, SHARP_CLS = "kitty", W.TGPImage


# ── "HD window": the same app in a terminal that draws real pixels ──
APP_DIR = Path(__file__).resolve().parent
KONSOLE_DIR = Path.home() / ".local" / "share" / "konsole"


def ensure_konsole_profile() -> None:
    """An ACP4U profile for Konsole: opaque dark background, Fira Code, big window, no scrollback."""
    KONSOLE_DIR.mkdir(parents=True, exist_ok=True)
    scheme = KONSOLE_DIR / "ACP4U.colorscheme"
    base = Path("/usr/share/konsole/Kali-Dark.colorscheme")
    if not scheme.exists():
        txt = base.read_text() if base.exists() else "[General]\n\n[Foreground]\nColor=228,228,240\n"
        txt = re.sub(r"Description=.*", "Description=ACP4U", txt)
        txt = re.sub(r"Opacity=.*", "Opacity=1", txt)
        txt = re.sub(r"Blur=.*", "Blur=false", txt)
        txt = re.sub(r"(\[Background(?:Intense|Faint)?\]\nColor=)[0-9,]+", r"\g<1>10,10,22", txt)
        if "[Background]" not in txt:
            txt += "\n[Background]\nColor=10,10,22\n"
        scheme.write_text(txt)
    prof = KONSOLE_DIR / "ACP4U.profile"
    if not prof.exists():
        font = "Fira Code" if shutil.which("fc-list") and "Fira Code" in subprocess.run(
            ["fc-list", ":family"], capture_output=True, text=True).stdout else "Monospace"
        prof.write_text(f"""[Appearance]
ColorScheme=ACP4U
Font={font},10,-1,5,50,0,0,0,0,0

[General]
Name=ACP4U
Parent=FALLBACK/
TerminalColumns=170
TerminalRows=50
ShowTerminalSizeHint=false

[Scrolling]
HistoryMode=0
ScrollBarPosition=2

[Terminal Features]
BlinkingCursorEnabled=false
""")


def hd_command() -> list[str] | None:
    """Terminal that can show real pixels, ready to run a command (`… + [program, args]`)."""
    if shutil.which("kitty"):
        return ["kitty", "--class", "acp4u", "--title", "ACP4U Parts", "-o", "background=#0a0a16",
                "-o", "initial_window_width=170c", "-o", "initial_window_height=50c"]
    if shutil.which("konsole"):
        ensure_konsole_profile()
        return ["konsole", "--profile", "ACP4U", "--hide-menubar", "--hide-tabbar", "--separate", "-e"]
    if shutil.which("wezterm"):
        return ["wezterm", "start", "--always-new-process", "--"]
    if shutil.which("foot"):
        return ["foot", "--title", "ACP4U Parts"]
    if shutil.which("mlterm"):
        return ["mlterm", "-e"]
    return None


def launch_hd(args: list[str]) -> str | None:
    """Open ACP4U in a sharp-photo terminal. Returns its name, or None when there is none."""
    term = hd_command()
    if not term:
        return None
    me = [str(APP_DIR / ".venv" / "bin" / "python"), str(APP_DIR / "acp4u_tui.py")]
    if not Path(me[0]).exists():
        me[0] = sys.executable
    extra = []
    if core.PROXY_OVERRIDE is not None:
        extra = ["--no-proxy"] if core.PROXY_OVERRIDE.get("mode") == "none" else \
            (["--proxy", core.proxy_url(core.PROXY_OVERRIDE)] if core.proxy_url(core.PROXY_OVERRIDE) else [])
    subprocess.Popen(term + me + list(args) + extra, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return term[0]


def image_facts(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        return f"{im.width}×{im.height} px · {im.format or '?'} · {len(data) / 1024:,.0f} KB"
    except Exception:
        return f"{len(data) / 1024:,.0f} KB"


def flatten(v, prefix: str = "") -> list[tuple[str, str]]:
    """Every value of a JSON object as (path, value) rows — nothing hidden."""
    out = []
    if isinstance(v, dict):
        if not v and prefix:
            out.append((prefix, "{}"))
        for k, x in v.items():
            out += flatten(x, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(v, list):
        if not v and prefix:
            out.append((prefix, "[]"))
        for i, x in enumerate(v):
            out += flatten(x, f"{prefix}[{i}]")
    else:
        out.append((prefix, "null" if v is None else str(v)))
    return out


ORDER_KEYS = {"id", "status", "brand", "model", "year", "vin", "part", "fuel", "vehicle_type", "origin", "engine_no",
              "gvw", "part_number", "side", "colour", "info", "details", "ref", "source", "customer", "photos",
              "created_at", "updated_at", "created_by", "quotes"}
CUSTOMER_KEYS = {"id", "handle", "name", "platform", "phone", "location", "notes", "created_at", "orders",
                 "orders_count", "order_count"}


def pretty_key(k: str) -> str:
    return k.replace("_", " ").capitalize() if re.fullmatch(r"[a-z0-9_]+", k) else k


def extra_rows(d: dict, known: set) -> list[tuple[str, str]]:
    rows = []
    for k, v in d.items():
        if k in known:
            continue
        rows.append((pretty_key(k), json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else s(v)))
    return rows


# ── detailed text rendering: every cell = 4×8 pixels, best of 17 block shapes (like chafa) ──
def _glyph_masks():
    import numpy as np
    def m(fn):
        return np.array([[fn(x, y) for x in range(4)] for y in range(8)], dtype=bool).reshape(32)
    g = [("▀", m(lambda x, y: y < 4))]
    for k, ch in ((1, "▁"), (2, "▂"), (3, "▃"), (5, "▅"), (6, "▆"), (7, "▇")):
        g.append((ch, m(lambda x, y, k=k: y >= 8 - k)))
    for k, ch in ((1, "▎"), (2, "▌"), (3, "▊")):
        g.append((ch, m(lambda x, y, k=k: x < k)))
    q = {"▘": (0, 0), "▝": (1, 0), "▖": (0, 1), "▗": (1, 1)}
    for ch, (qx, qy) in q.items():
        g.append((ch, m(lambda x, y, qx=qx, qy=qy: x // 2 == qx and y // 4 == qy)))
    g.append(("▚", m(lambda x, y: (x // 2) == (y // 4))))
    g.append(("▞", m(lambda x, y: (x // 2) != (y // 4))))
    return [c for c, _ in g], np.stack([mk for _, mk in g]).astype("float32")


_GLYPHS = None


def load_image(data: bytes):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (10, 10, 22, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def render_detailed(im, w: int, h: int) -> Text | None:
    """Sharpest picture plain text can give: per cell pick the block shape + 2 colours that match best."""
    global _GLYPHS
    try:
        import numpy as np
        from PIL import Image, ImageFilter
        from rich.color import Color
        from rich.style import Style
    except ImportError:
        return None
    if _GLYPHS is None:
        _GLYPHS = _glyph_masks()
    chars, masks = _GLYPHS
    W, H = im.size
    sc = min(max(1, w) * 4 / W, max(1, h) * 8 / H)
    cols, rows = max(1, int(W * sc / 4)), max(1, int(H * sc / 8))
    small = im.resize((cols * 4, rows * 8), Image.LANCZOS, reducing_gap=3.0)
    small = small.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=2))
    a = np.asarray(small, dtype=np.float32).reshape(rows, 8, cols, 4, 3).transpose(0, 2, 1, 3, 4).reshape(rows, cols, 32, 3)
    S = a.sum(axis=2)                                        # rows, cols, 3
    Q = (a * a).sum(axis=(2, 3))                             # rows, cols
    n1 = masks.sum(axis=1)                                   # K
    n0 = 32.0 - n1
    S1 = np.einsum("rcpx,kp->rckx", a, masks)                # rows, cols, K, 3
    S0 = S[:, :, None, :] - S1
    err = Q[:, :, None] - (S1 ** 2).sum(-1) / n1 - (S0 ** 2).sum(-1) / n0
    flat = Q - (S ** 2).sum(-1) / 32.0                       # one colour for the whole cell
    err = np.concatenate([err + 64.0, flat[:, :, None]], axis=2)   # small bias toward calm cells
    best = err.argmin(axis=2)
    K = len(chars)
    idx = np.minimum(best, K - 1)
    fg = np.take_along_axis(S1, idx[:, :, None, None], 2)[:, :, 0, :] / n1[idx][:, :, None]
    bg = np.take_along_axis(S0, idx[:, :, None, None], 2)[:, :, 0, :] / n0[idx][:, :, None]
    uni = best == K
    bg[uni] = S[uni] / 32.0
    fg = np.clip(fg, 0, 255).astype(np.uint8)
    bg = np.clip(bg, 0, 255).astype(np.uint8)
    t = Text()
    cache: dict = {}
    for y in range(rows):
        for x in range(cols):
            b = tuple(bg[y, x])
            if uni[y, x]:
                key = (None, b)
                ch = " "
            else:
                key = (tuple(fg[y, x]), b)
                ch = chars[best[y, x]]
            st = cache.get(key)
            if st is None:
                st = cache[key] = Style(color=Color.from_rgb(*key[0]) if key[0] else None, bgcolor=Color.from_rgb(*b))
            t.append(ch, style=st)
        if y < rows - 1:
            t.append("\n")
    return t


def render_half(im, w: int, h: int) -> Text:
    from PIL import Image
    from rich.color import Color
    from rich.style import Style
    W, H = im.size
    sc = min(max(1, w) / W, (max(1, h) * 2) / H)
    nw, nh = max(2, int(W * sc)), max(2, int(H * sc))
    nh -= nh % 2
    px = im.resize((nw, nh), Image.LANCZOS).load()
    t = Text()
    for y in range(0, nh, 2):
        for x in range(nw):
            a, b = px[x, y], px[x, y + 1]
            t.append("▀", style=Style(color=Color.from_rgb(*a), bgcolor=Color.from_rgb(*b)))
        if y + 2 < nh:
            t.append("\n")
    return t


def render_text_image(im, w: int, h: int) -> Text | None:
    if TEXT_MODE == "half":
        return render_half(im, w, h)
    return render_detailed(im, w, h) or render_half(im, w, h)


def panel(body, title: str, color: str = C1) -> Panel:
    return Panel(body, title=Text(f" {title} ", style=f"bold {color}"), title_align="left",
                 border_style="#2e2e5a", box=box.ROUNDED, padding=(0, 1))


def kv(rows) -> Table | Text:
    return core.kv(rows)


def parse_details(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            a, b = line.split(":", 1)
            if a.strip():
                out[a.strip()] = b.strip()
        elif line.strip():
            out[line.strip()] = ""
    return out


def split_inputs(text: str) -> tuple[list[Path], list[str], list[str]]:
    """Paths / links typed or pasted → (files, urls, problems)."""
    files, urls, bad = [], [], []
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    for p in parts:
        if re.match(r"^(https?://|data:image/)", p, re.I):
            urls.append(p)
            continue
        f = Path(re.sub(r"^file://", "", p)).expanduser()
        if f.is_dir():
            files += sorted(x for x in f.iterdir() if x.suffix.lower() in IMG_EXT)
        elif f.is_file() and f.suffix.lower() in IMG_EXT:
            files.append(f)
        else:
            bad.append(p)
    return files, urls, bad


def file_tuple(field: str, f: Path):
    return (field, (f.name, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream"))


def sel_value(sel: Select) -> str:
    v = sel.value
    return "" if v is Select.NULL or v is None else str(v)


CSS = """
Screen { background: $background; }
#topbar { height: 1; background: $panel; }
#topbar #crumb { width: 1fr; padding: 0 1; }
#topbar #status { width: auto; padding: 0 1; }

/* ── sidebar ── */
#sidebar { width: 30; background: $surface; border-right: tall $panel; }
#logo { height: 4; padding: 1 0 0 2; }
.side-title { color: $text-muted; padding: 1 2 0 2; text-style: bold; }
#sidebar OptionList { border: none; background: $surface; padding: 0 1; height: auto; }
#sidebar OptionList:focus { border: none; }
#side-foot { dock: bottom; height: auto; padding: 0 2 1 2; color: $text-muted; }
#views { width: 1fr; }

/* ── dashboard ── */
#dash { padding: 1 2; }
#hello { height: auto; margin-bottom: 1; }
#cards { layout: grid; grid-size: 5; grid-gutter: 1 2; height: auto; }
.card { height: 7; border: round $panel; background: $surface; padding: 0 1; }
.card:hover { border: round $primary; background: $boost; }
.card .card-title { width: 100%; content-align: center middle; text-style: bold; }
.card Digits { width: 100%; text-align: center; }
#dash-bottom { height: auto; margin-top: 1; }
#bars { width: 40; height: auto; }
#recent-box { width: 1fr; height: auto; margin-left: 2; }
#recent { height: 14; }
.box-title { color: $secondary; text-style: bold; padding: 0 1; }

/* ── lists ── */
.toolbar { height: 3; padding: 0 1; margin-top: 1; }
.toolbar Input { width: 1fr; min-width: 24; }
.toolbar Select { width: 22; }
#o-sort { width: 19; }
.toolbar Button { min-width: 10; margin-left: 1; }
#chips { height: auto; padding: 0 2; }
DataTable { height: 1fr; margin: 0 1; background: $surface; }
DataTable > .datatable--header { background: $panel; color: $secondary; text-style: bold; }
DataTable > .datatable--cursor { background: $primary 30%; }
.pager { height: 3; padding: 0 1; }
.pager .pageinfo { width: 1fr; content-align: center middle; height: 3; }
.pager Button { min-width: 8; }

/* ── watch / console / info / settings ── */
#watchbar { height: 3; padding: 0 2; margin-top: 1; }
#watchbar Label { height: 3; content-align: center middle; padding: 0 1; }
#watchbar Input { width: 8; }
#watchstate { width: 1fr; height: 3; content-align: right middle; padding: 0 1; }
#console-view, #info-view, #settings-view { padding: 1 2; }
#c-row { height: 3; }
#c-method { width: 14; }
#c-route { width: 1fr; }
#c-params { width: 1fr; }
#c-body { height: 6; margin-top: 1; }
#c-out { height: 1fr; border: round $panel; margin-top: 1; }
.fgrid { layout: grid; grid-size: 4; grid-columns: 18 1fr 18 1fr; grid-rows: 3; grid-gutter: 0 1; height: auto; }
.fgrid Label { width: 100%; height: 3; content-align: right middle; color: $text-muted; }
.fgrid Label.req { color: $secondary; text-style: bold; }
.section { color: $secondary; text-style: bold; margin: 1 0 0 0; padding: 0 1; border-bottom: solid $panel; width: 100%; }
.btnrow { height: 3; margin-top: 1; }
.btnrow Button { margin-right: 1; }
Input.-invalid { border: tall $error; }

/* ── order screen ── */
#o-head { height: auto; padding: 1 2 0 2; }
#o-body { height: 1fr; }
#o-left { width: 2fr; padding: 0 1 0 2; }
#o-right { width: 1fr; min-width: 34; padding: 0 2 0 1; }
#o-panels { height: auto; }
#o-panels Static { width: 1fr; height: auto; }
#thumbs { layout: grid; grid-size: 2; grid-gutter: 1 1; grid-rows: 12; height: auto; }
.thumb { height: 12; border: round $panel; content-align: center middle; background: $surface; }
.thumb:hover { border: round $primary; }
.empty-slot { color: $text-muted; }
.empty-slot:hover { color: $success; border: round $success; }
.actions { height: 3; padding: 0 1; dock: bottom; margin-bottom: 1; }
.actions Button { margin-right: 1; min-width: 6; }

/* ── modals ── */
ModalScreen { align: center middle; background: $background 70%; }
.dialog { width: 72; height: auto; max-height: 90%; border: thick $primary; background: $surface; padding: 1 2; }
.dialog.wide { width: 110; }
.dialog.tall { height: 90%; }
.dialog .title { text-style: bold; color: $primary; width: 100%; margin-bottom: 1; }
.dialog.danger { border: thick $error; }
.dialog.danger .title { color: $error; }
.dialog OptionList { height: auto; max-height: 20; border: none; background: $surface; }
#viewer { width: 100%; height: 100%; border: thick $secondary; background: $background; padding: 0 1; }
#v-img { height: 1fr; content-align: center middle; }
#v-title, #v-help { height: 1; }
#fp-tree { width: 1fr; height: 1fr; background: $background; }
#fp-preview { width: 44; height: 1fr; content-align: center middle; border: round $panel; }
#fp-mid { height: 1fr; }
#fp-nav { height: 3; }
#fp-nav Input { width: 1fr; }
#fp-sel { height: auto; max-height: 4; color: $success; padding: 0 1; }
#form { padding: 0 2; }
#form-head { height: auto; padding: 1 2 0 2; }
#f-details { height: 6; }
#f-photos { height: auto; padding: 0 1; color: $success; }
#q-thumbs { layout: grid; grid-size: 3; grid-gutter: 0 1; grid-rows: 10; height: auto; }
#q-thumbs .thumb { height: 10; }
.fgrid Horizontal { height: 3; }
.fgrid Horizontal Input { width: 1fr; }
.fgrid Horizontal Button { min-width: 5; width: 5; }

/* ── photos ── */
.pic { align: center middle; }
.pic > .pic-blocks { width: auto; height: auto; }
.pic > .pic-sharp { width: auto; height: auto; }
#gallery { layout: grid; grid-size: 2; grid-gutter: 1 2; grid-rows: 24; height: auto; padding: 0 1; }
.thumb.big { height: 24; }
#quotes-box { padding: 0 1; }
.qcard { height: auto; border: round $panel; padding: 0 1; margin-bottom: 1; background: $surface; }
.qgrid { layout: grid; grid-size: 3; grid-gutter: 0 1; grid-rows: 16; height: auto; }
.thumb.qbig { height: 16; }
#o-tabs { height: 1fr; }
#o-tabs TabPane { padding: 0; }
#o-main { height: 1fr; }
#o-table { width: 1fr; }
#o-preview { width: 46; border-left: tall $panel; padding: 0 1; background: $surface; }
#pv-pic { height: 16; }
#pv-strip { height: 1; margin: 1 0; }
#all-data { padding: 0 2; }

/* ── proxy ── */
.proxyform { height: auto; }
.px-row { height: 3; }
.px-row Select { width: 1fr; }
#px-url { margin: 0 0 0 0; }
#px-status { height: auto; padding: 0 1; }
#px-show { width: 100%; }

/* ── login ── */
#login { align: center middle; }
#login-card { width: 96; height: auto; max-height: 100%; overflow-y: auto; border: thick $primary; background: $surface; padding: 1 3; }
#login-logo { width: 100%; content-align: center middle; height: 7; }
#login-msg { margin: 1 0; }
#login-err { color: $error; height: auto; }
#splash { align: center middle; }
#splash-box { width: auto; height: auto; }
#splash-logo { content-align: center middle; width: 100%; }
#splash-msg { content-align: center middle; width: 100%; color: $text-muted; margin-top: 1; }
"""


# ════════════════════════════ shared widgets ═════════════════════════════
class Pic(Vertical):
    """One image: real pixels (Sixel / Kitty) when the terminal can, detailed blocks otherwise."""

    def __init__(self, *, classes: str = "", id: str | None = None) -> None:
        super().__init__(classes=("pic " + classes).strip(), id=id)
        self.data: bytes | None = None
        self.im = None
        self._drawn: tuple | None = None
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
        self.zoomable = False

    def compose(self) -> ComposeResult:
        yield Static(classes="pic-blocks")

    def view(self):
        """The part of the photo on show (whole photo, or the zoomed crop)."""
        if self.im is None or self.zoom <= 1.0:
            return self.im
        W, H = self.im.size
        cw, ch = W / self.zoom, H / self.zoom
        self.cx = min(max(self.cx, cw / 2 / W), 1 - cw / 2 / W)
        self.cy = min(max(self.cy, ch / 2 / H), 1 - ch / 2 / H)
        x0, y0 = self.cx * W - cw / 2, self.cy * H - ch / 2
        return self.im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch)))

    def set_zoom(self, zoom: float | None = None, dx: float = 0, dy: float = 0) -> None:
        if zoom is not None:
            self.zoom = min(16.0, max(1.0, zoom))
            if self.zoom == 1.0:
                self.cx = self.cy = 0.5
        step = 0.25 / self.zoom
        self.cx += dx * step
        self.cy += dy * step
        self._drawn = None
        self.draw()

    def on_mouse_scroll_up(self, e) -> None:
        if self.zoomable:
            self.set_zoom(self.zoom * 1.25)
            e.stop()

    def on_mouse_scroll_down(self, e) -> None:
        if self.zoomable:
            self.set_zoom(self.zoom / 1.25)
            e.stop()

    def say(self, t) -> None:
        self.data, self.im, self._drawn = None, None, None
        for w in self.query(".pic-sharp"):
            w.remove()
        st = self.query_one(".pic-blocks", Static)
        st.display = True
        st.update(t if isinstance(t, Text) else Text(str(t), style=MUTED, justify="center"))

    def set_data(self, data: bytes | None) -> None:
        if not data:
            self.say(Text("(could not load the photo)", style=MUTED))
            return
        try:
            im = load_image(data)
        except Exception:
            self.say(Text("(cannot show this image)", style=MUTED))
            return
        self.data, self.im, self._drawn = data, im, None
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
        self.draw()

    def draw(self) -> None:
        if self.im is None or not self.is_mounted:
            return
        st = self.query_one(".pic-blocks", Static)
        if SHARP_CLS is not None:
            st.display = False
            ex = list(self.query(".pic-sharp"))
            if ex:
                ex[0].image = self.view()
            else:
                self.mount(SHARP_CLS(self.view(), classes="pic-sharp"))
            return
        w, h = self.content_size
        key = (w, h, id(self.im), self.zoom, round(self.cx, 4), round(self.cy, 4))
        if w < 2 or h < 1 or self._drawn == key:
            return
        self._drawn = key
        self._paint(self.im, self.view(), w, h)

    @work(exclusive=True, group="pic")
    async def _paint(self, im, part, w: int, h: int) -> None:
        txt = await asyncio.to_thread(render_text_image, part, w, h)
        if im is self.im:
            st = self.query_one(".pic-blocks", Static)
            st.display = True
            st.update(txt or Text("(cannot show this image)", style=MUTED))

    def on_resize(self) -> None:
        if SHARP_CLS is None:
            self.draw()


class TopBar(Horizontal):
    def __init__(self, crumb: str = "") -> None:
        super().__init__(id="topbar")
        self._crumb = crumb

    def compose(self) -> ComposeResult:
        yield Static(id="crumb")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.set_crumb(self._crumb)
        self.tick()
        self.set_interval(1, self.tick)

    def set_crumb(self, crumb: str) -> None:
        t = Text.assemble((" ACP4U ", f"bold #0a0a16 on {C1}"), (" PARTS ", f"bold #0a0a16 on {C2}"), "  ")
        for i, p in enumerate([x for x in crumb.split(" › ") if x]):
            if i:
                t.append("  ›  ", style=MUTED)
            t.append(p, style="bold")
        self.query_one("#crumb", Static).update(t)

    def tick(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        t = Text()
        if getattr(app, "watching", False):
            t.append(" ◉ LIVE ", style=f"bold #0a0a16 on {C2}")
            t.append("  ")
        t.append("● ", style=OK if app.online else BAD)
        t.append(acct_name(app.info) if app.info else "offline", style="bold")
        host = re.sub(r"^https?://", "", app.api.base).split("/")[0] if app.api else ""
        t.append(f"  {host}", style=MUTED)
        if app.api and app.api.base.startswith("http://"):
            t.append(" ⚠", style=C3)
        if app.api:
            px = app.api.proxy
            if px.get("mode") == "custom":
                t.append(f"  ⛨ {px.get('type')} {px.get('host')}:{px.get('port')}", style=f"bold {C1}")
            elif px.get("mode") == "system" and core.env_proxy():
                t.append("  ⛨ system", style=C1)
        t.append(f"   {dt.datetime.now():%H:%M:%S}", style=f"bold {C1}")
        self.query_one("#status", Static).update(t)


class Card(Vertical):
    def __init__(self, key: str, title: str, color: str, filters: dict) -> None:
        super().__init__(classes="card")
        self.key, self.title, self.color, self.filters = key, title, color, filters

    def compose(self) -> ComposeResult:
        yield Static(Text(self.title, style=self.color), classes="card-title")
        yield Digits("–")

    def on_mount(self) -> None:
        self.query_one(Digits).styles.color = self.color
        self.tooltip = "Click to open these orders"

    def set_count(self, n) -> None:
        self.query_one(Digits).update("–" if n is None else str(n))
        self.styles.border = ("round", self.color if n else "#1c1c3c")

    def on_click(self) -> None:
        self.screen.open_orders(self.filters, self.title)  # type: ignore


class Thumb(Pic):
    """Clickable photo (or an empty slot). `full` loads the big image when the terminal shows it sharp."""

    def __init__(self, photo: dict | None, slot: int, route: str = "photos", full: bool = False,
                 owner=None, group: list | None = None, classes: str = "thumb") -> None:
        super().__init__(classes=classes + ("" if photo else " empty-slot"))
        self.photo, self.slot, self.route, self.full = photo, slot, route, full
        self.owner, self.group = owner, group

    def on_mount(self) -> None:
        self.border_title = f"slot {self.slot}"
        if self.photo:
            self.border_subtitle = f"#{self.photo.get('id')}"
            self.say(Text("loading…", style=MUTED))
            self.tooltip = "Click to open"
            self.load()
        else:
            self.say(Text("＋\nadd photo", style=MUTED, justify="center"))
            self.tooltip = f"Click to put a photo into slot {self.slot}"

    @work
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        thumb = not self.full
        data = await app.image(self.route, self.photo["id"], thumb=thumb, quiet=True)
        if data is None and thumb:
            data = await app.image(self.route, self.photo["id"], thumb=False)
        self.set_data(data)

    def on_click(self) -> None:
        (self.owner or self.screen).thumb_clicked(self)  # type: ignore


# ═══════════════════════════════ modals ══════════════════════════════════
class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "Cancel"), Binding("y", "yes", "Yes", show=False)]

    def __init__(self, title: str, message: str = "", yes: str = "Yes", danger: bool = True) -> None:
        super().__init__()
        self.t, self.m, self.yes, self.danger = title, message, yes, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog" + (" danger" if self.danger else "")):
            yield Static(self.t, classes="title")
            if self.m:
                yield Static(self.m)
            with Horizontal(classes="btnrow"):
                yield Button(self.yes, id="yes", variant="error" if self.danger else "primary")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed, "#yes")
    def action_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_no(self) -> None:
        self.dismiss(False)


class TextModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", value: str = "", hint: str = "") -> None:
        super().__init__()
        self.t, self.ph, self.v, self.hint = title, placeholder, value, hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.t, classes="title")
            yield Input(value=self.v, placeholder=self.ph, id="tm-in")
            if self.hint:
                yield Static(Text(self.hint, style=MUTED))

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def done(self, e: Input.Submitted) -> None:
        self.dismiss(e.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class StatusModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str | None, title: str = "Set status") -> None:
        super().__init__()
        self.current, self.t = current, title

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.t, classes="title")
            opts = []
            for i, st in enumerate(core.STATUSES, 1):
                lab = Text(f" {i} ", style=MUTED) + core.status_label(st)
                if st == self.current:
                    lab.append("   ← now", style=MUTED)
                opts.append(Option(lab, id=st))
            yield OptionList(*opts, id="st-list")
            yield Static(Text("↑↓ / 1-8 · enter · esc", style=MUTED))

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.focus()
        if self.current in core.STATUSES:
            ol.highlighted = core.STATUSES.index(self.current)

    def on_key(self, e) -> None:
        if e.character and e.character.isdigit() and 1 <= int(e.character) <= len(core.STATUSES):
            self.dismiss(core.STATUSES[int(e.character) - 1])

    @on(OptionList.OptionSelected)
    def picked(self, e: OptionList.OptionSelected) -> None:
        self.dismiss(e.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ResultModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("enter", "close", "Close", show=False)]

    def __init__(self, title: str, body) -> None:
        super().__init__()
        self.t, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Static(self.t, classes="title")
            with VerticalScroll():
                yield Static(self.body)
            yield Button("Close", id="close")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class HelpModal(ResultModal):
    def __init__(self) -> None:
        t = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {C2}", expand=True)
        t.add_column("Where")
        t.add_column("Key", style=f"bold {C1}")
        t.add_column("Does")
        rows = [
            ("anywhere", "ctrl+p", "command palette — type what you want"), ("anywhere", "g", "go to order (#id, ref, text)"),
            ("anywhere", "n", "new order"), ("anywhere", "1 … 9", "sidebar pages"), ("anywhere", "f1", "this help"),
            ("anywhere", "ctrl+q", "quit"), ("lists", "enter / click", "open"), ("lists", "/", "search"),
            ("lists", "[  ]", "previous / next page"), ("lists", "s", "change status of the row"),
            ("lists", "r", "refresh"), ("lists", "x", "export CSV"), ("order", "s e c p m", "status · edit · customer · photos · my price"),
            ("order", "w", "download photos"), ("order", "del", "delete"), ("order", "esc", "back"),
            ("photo viewer", "← →", "previous / next photo"), ("photo viewer", "d / x / r", "download · remove · replace"),
            ("anywhere", "f2", "open in a sharp window (real-pixel photos)"), ("photo viewer", "+ − 0 h j k l", "zoom · whole · move"),
            ("forms", "ctrl+s", "save"), ("forms", "tab", "next field"), ("everywhere", "mouse", "click anything"),
        ]
        for r in rows:
            t.add_row(*r)
        super().__init__("⌨  Keys", t)


class FilterModal(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "apply", "Apply")]

    def __init__(self, f: dict) -> None:
        super().__init__()
        self.f = f

    def compose(self) -> ComposeResult:
        f = self.f
        with Vertical(classes="dialog wide"):
            yield Static("⚲  More filters", classes="title")
            with Grid(classes="fgrid"):
                yield Label("Brand")
                yield Input(s(f.get("brand")), id="m-brand", suggester=SuggestFromList(BRANDS, case_sensitive=False))
                yield Label("Ref")
                yield Input(s(f.get("ref")), id="m-ref")
                yield Label("Customer id")
                yield Input(s(f.get("customer_id")), id="m-customer_id", type="integer")
                yield Label("Source")
                yield Select([("form — panel", "form"), ("excel — imported", "excel"), ("api — keys", "api")],
                             prompt="any", value=f.get("source") or Select.NULL, id="m-source")
                yield Label("Created since")
                yield Input(s(f.get("created_since")), id="m-created_since", placeholder="2026-10-01 · today · 7d")
                yield Label("Updated since")
                yield Input(s(f.get("updated_since")), id="m-updated_since", placeholder="2026-10-01 · today · 24h")
                yield Label("Iranian cars")
                yield Select([("exclude Iranian cars", "exclude"), ("only Iranian cars", "only")],
                             prompt="any", value=f.get("iranian") or Select.NULL, id="m-iranian")
            with Horizontal(classes="btnrow"):
                yield Button("✔ Apply", id="apply", variant="primary")
                yield Button("⌫ Clear", id="clear", variant="warning")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        out = {}
        for k in ("brand", "ref", "customer_id", "created_since", "updated_since"):
            v = self.query_one(f"#m-{k}", Input).value.strip()
            if v:
                out[k] = core.rel_date(v) if k.endswith("_since") else v
        for k in ("source", "iranian"):
            v = sel_value(self.query_one(f"#m-{k}", Select))
            if v:
                out[k] = v
        self.dismiss(out)

    @on(Button.Pressed, "#clear")
    def clear(self) -> None:
        self.dismiss({})

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# ─────────────────────────────── file picker ──────────────────────────────
class PickTree(DirectoryTree):
    def __init__(self, path, exts: set[str], picked: list, **kw) -> None:
        super().__init__(path, **kw)
        self.exts, self.picked = exts, picked

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() in self.exts)]

    def render_label(self, node, base_style, style) -> Text:
        t = super().render_label(node, base_style, style)
        if node.data and node.data.path in self.picked:
            return Text("✔ ", style=f"bold {OK}") + t
        return t


class FilePicker(ModalScreen[tuple | None]):
    """Pick images (or one file). Returns (files, urls) or None."""
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "done", "Done"),
                Binding("backspace", "up", "Up a folder", show=False)]

    def __init__(self, title: str = "Pick photos", exts: set[str] | None = None, max_files: int = 8,
                 allow_links: bool = True) -> None:
        super().__init__()
        self.t, self.exts, self.maxn, self.allow_links = title, exts or IMG_EXT, max_files, allow_links
        self.picked: list[Path] = []
        start = Path.home() / "Pictures"
        self.start = start if start.is_dir() else Path.home() if Path.home().is_dir() else Path("/")

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Static(f"{self.t}  ·  up to {self.maxn}", classes="title")
            with Horizontal(id="fp-nav"):
                yield Button("⌂", id="fp-home", tooltip="Home")
                yield Button("⇩ Downloads", id="fp-dl")
                yield Button("▣ Pictures", id="fp-pics")
                yield Button("↑", id="fp-up", tooltip="Up a folder")
                yield Input(str(self.start), id="fp-path", placeholder="folder — enter to open")
            with Horizontal(id="fp-mid"):
                yield PickTree(self.start, self.exts, self.picked, id="fp-tree")
                yield Pic(id="fp-preview")
            yield Static(id="fp-sel")
            if self.allow_links:
                yield Input(placeholder="…or paste links / paths (drag files here) — separated by spaces", id="fp-links")
            with Horizontal(classes="btnrow"):
                yield Button("✔ Use these", id="fp-done", variant="primary")
                yield Button("⌫ Clear", id="fp-clear")
                yield Button("Cancel", id="fp-cancel")

    def on_mount(self) -> None:
        self.query_one("#fp-tree").focus()
        self.refresh_sel()

    def refresh_sel(self) -> None:
        t = Text()
        if self.picked:
            t.append(f"✔ {len(self.picked)} picked: ", style=f"bold {OK}")
            t.append(", ".join(p.name for p in self.picked), style=OK)
        else:
            t.append("enter / click a file to pick it (again to un-pick) · enter on a folder opens it", style=MUTED)
        self.query_one("#fp-sel", Static).update(t)
        self.query_one(PickTree).refresh()

    def go(self, path: Path) -> None:
        if path.is_dir():
            self.query_one(PickTree).path = path
            self.query_one("#fp-path", Input).value = str(path)

    @on(Button.Pressed, "#fp-home")
    def b_home(self) -> None:
        self.go(Path.home())

    @on(Button.Pressed, "#fp-dl")
    def b_dl(self) -> None:
        self.go(Path.home() / "Downloads")

    @on(Button.Pressed, "#fp-pics")
    def b_pics(self) -> None:
        self.go(Path.home() / "Pictures")

    @on(Button.Pressed, "#fp-up")
    def action_up(self) -> None:
        cur = Path(self.query_one(PickTree).path)
        self.go(cur.parent)

    @on(Input.Submitted, "#fp-path")
    def path_go(self, e: Input.Submitted) -> None:
        p = Path(e.value).expanduser()
        if p.is_dir():
            self.go(p)
        elif p.is_file():
            self.toggle(p)
        else:
            self.notify("No such folder", severity="warning")

    @on(Input.Submitted, "#fp-links")
    def links_go(self, e: Input.Submitted) -> None:
        self.action_done()

    @on(DirectoryTree.FileSelected)
    def file_sel(self, e: DirectoryTree.FileSelected) -> None:
        self.toggle(Path(e.path))

    def toggle(self, p: Path) -> None:
        if not p.is_file():
            self.notify(f"Not a file: {p}", severity="warning")
            return
        if p in self.picked:
            self.picked.remove(p)
        else:
            if p.stat().st_size > 20 * 1024 * 1024 and p.suffix.lower() in IMG_EXT:
                self.notify(f"{p.name} is bigger than 20 MB", severity="error")
                return
            if self.maxn == 1:
                self.picked[:] = [p]
                self.action_done()
                return
            if len(self.picked) >= self.maxn:
                self.notify(f"Only {self.maxn} fit", severity="warning")
                return
            self.picked.append(p)
        self.refresh_sel()

    @on(DirectoryTree.NodeHighlighted)
    def hl(self, e) -> None:
        node = e.node
        if node.data and Path(node.data.path).is_file() and Path(node.data.path).suffix.lower() in IMG_EXT:
            self.preview(Path(node.data.path))
        else:
            self.query_one("#fp-preview", Pic).say(Text("📁  folder" if node.data else "", style=MUTED, justify="center"))

    @work(exclusive=True, group="preview")
    async def preview(self, p: Path) -> None:
        pic = self.query_one("#fp-preview", Pic)
        try:
            data = await asyncio.to_thread(p.read_bytes)
        except OSError:
            data = None
        pic.set_data(data)
        pic.border_title = p.name
        pic.border_subtitle = image_facts(data)

    @on(Button.Pressed, "#fp-clear")
    def clear(self) -> None:
        self.picked.clear()
        self.refresh_sel()

    @on(Button.Pressed, "#fp-done")
    def action_done(self) -> None:
        files, urls = list(self.picked), []
        if self.allow_links:
            txt = self.query_one("#fp-links", Input).value.strip()
            if txt:
                f2, urls, bad = split_inputs(txt)
                files += [f for f in f2 if f not in files]
                if bad:
                    self.notify("Not found / not an image:\n" + "\n".join(bad), severity="warning")
        files = files[: self.maxn]
        urls = urls[: max(0, self.maxn - len(files))]
        self.dismiss((files, urls) if files or urls else None)

    @on(Button.Pressed, "#fp-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# ─────────────────────────────── photo viewer ─────────────────────────────
class PhotoViewer(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("left", "prev", "Prev"), Binding("right", "next", "Next"),
                Binding("d", "download", "Download"), Binding("o", "open", "Open outside"),
                Binding("x", "remove", "Remove"), Binding("r", "replace", "Replace"), Binding("i", "info", "Info"),
                Binding("plus,equals_sign", "zoom(1.5)", "Zoom in"), Binding("minus", "zoom(0.6667)", "Zoom out"),
                Binding("0", "zoom(0)", "Whole photo"),
                Binding("h,shift+left", "pan(-1,0)", "Pan", show=False), Binding("l,shift+right", "pan(1,0)", show=False),
                Binding("k,shift+up", "pan(0,-1)", show=False), Binding("j,shift+down", "pan(0,1)", show=False)]

    def __init__(self, photos: list, index: int, route: str, owner=None, folder: str = "photos",
                 title: str = "") -> None:
        super().__init__()
        self.photos, self.i, self.route, self.owner = photos, index, route, owner
        self.folder, self.t = folder, title
        self.data: bytes | None = None
        self.changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="viewer"):
            yield Static(id="v-title")
            yield Pic(id="v-img")
            yield Static(id="v-help")

    def action_zoom(self, f: float) -> None:
        pic = self.query_one("#v-img", Pic)
        pic.set_zoom(1.0 if f == 0 else pic.zoom * f)
        self.query_one("#v-title", Static).update(self.title_text(image_facts(self.data)))

    def action_pan(self, dx: int, dy: int) -> None:
        pic = self.query_one("#v-img", Pic)
        if pic.zoom > 1.0:
            pic.set_zoom(None, dx, dy)

    def on_mount(self) -> None:
        self.query_one("#v-img", Pic).zoomable = True
        keys = "← → browse · + − zoom (or mouse wheel) · h j k l move · 0 whole · d download · o open outside · i info"
        if self.owner is not None:
            keys += " · r replace · x remove"
        help_ = Text(keys + " · esc close", style=MUTED)
        if SHARP_CLS is None:
            help_.append("   F2 sharp window", style=f"bold {C3}")
        self.query_one("#v-help", Static).update(help_)
        self.show()

    def check_action(self, action: str, parameters) -> bool | None:
        if action in ("remove", "replace") and self.owner is None:
            return False
        return True

    def title_text(self, facts: str = "") -> Text:
        p = self.photos[self.i]
        t = Text.assemble((f" slot {p.get('slot')} ", f"bold #0a0a16 on {C2}"), (f"  photo #{p.get('id')}", "bold"),
                          (f"   {self.i + 1}/{len(self.photos)}", MUTED))
        if self.t:
            t.append(f"   {self.t}", style=C1)
        if facts:
            t.append(f"   {facts}", style=C3)
        try:
            z = self.query_one("#v-img", Pic).zoom
            if z > 1.0:
                t.append(f"   🔍 {z:.1f}×", style=f"bold {C2}")
        except Exception:
            pass
        for k, v in p.items():
            if k not in ("id", "slot", "url", "thumb_url"):
                t.append(f"   {pretty_key(k)}: ", style=MUTED)
                t.append(s(v), style="bold")
        t.append(f"   [{'real pixels' if SHARP_CLS else IMG_MODE}]", style=MUTED)
        return t

    @work(exclusive=True)
    async def show(self) -> None:
        p = self.photos[self.i]
        self.query_one("#v-title", Static).update(self.title_text())
        pic = self.query_one("#v-img", Pic)
        pic.say(Text("loading…", style=MUTED))
        self.data = await self.app.image(self.route, p["id"])  # type: ignore
        pic.set_data(self.data)
        self.query_one("#v-title", Static).update(self.title_text(image_facts(self.data)))

    def action_prev(self) -> None:
        self.i = (self.i - 1) % len(self.photos)
        self.show()

    def action_next(self) -> None:
        self.i = (self.i + 1) % len(self.photos)
        self.show()

    def action_info(self) -> None:
        p = self.photos[self.i]
        rows = flatten(p) + [("image", image_facts(self.data))]
        self.app.push_screen(ResultModal(f"ⓘ  Photo #{p.get('id')}", kv(rows)))

    def action_download(self) -> None:
        self.app.save_photos([self.photos[self.i]], self.route, self.folder)  # type: ignore

    def action_open(self) -> None:
        if self.data is None:
            return
        folder = Path("/tmp") / f"acp4u-{os.getuid()}"
        folder.mkdir(exist_ok=True, mode=0o700)
        f = folder / f"photo-{self.photos[self.i]['id']}.jpg"
        f.write_bytes(self.data)
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(f)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def action_remove(self) -> None:
        p = self.photos[self.i]

        def go(ok):
            if ok:
                self.do_remove(p)
        self.app.push_screen(ConfirmModal(f"Remove photo in slot {p.get('slot')}?", yes="Remove"), go)

    @work
    async def do_remove(self, p) -> None:
        if await self.owner.remove_photo(p):
            self.changed = True
            self.photos.pop(self.i)
            if not self.photos:
                self.dismiss(True)
                return
            self.i %= len(self.photos)
            self.show()

    def action_replace(self) -> None:
        p = self.photos[self.i]

        def go(res):
            if res:
                self.do_replace(p, res)
        self.app.push_screen(FilePicker(f"Replace slot {p.get('slot')}", max_files=1), go)

    @work
    async def do_replace(self, p, res) -> None:
        files, urls = res
        if await self.owner.put_photo(p.get("slot"), files, urls):
            self.changed = True
            self.dismiss(True)

    def action_close(self) -> None:
        self.dismiss(self.changed)


# ─────────────────────────────── customers ───────────────────────────────
class CustomerForm(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, c: dict | None = None, title: str = "Customer") -> None:
        super().__init__()
        self.c, self.t = c or {}, title

    def compose(self) -> ComposeResult:
        c = self.c
        with Vertical(classes="dialog wide"):
            yield Static(self.t, classes="title")
            with Grid(classes="fgrid"):
                yield Label("Customer ID")
                yield Input(s(c.get("handle")), id="c-handle", placeholder="ali.cars")
                yield Label("Platform")
                yield Input(s(c.get("platform")), id="c-platform", placeholder="Instagram, WhatsApp…",
                            suggester=SuggestFromList(core.PLATFORMS, case_sensitive=False))
                yield Label("Name")
                yield Input(s(c.get("name")), id="c-name")
                yield Label("Phone")
                yield Input(s(c.get("phone")), id="c-phone", placeholder="09121234567")
                yield Label("Location")
                yield Input(s(c.get("location")), id="c-location", placeholder="Country / City")
                yield Label("Notes")
                yield Input(s(c.get("notes")), id="c-notes")
            yield Static(Text("Same Customer ID + platform = same customer (reused, never doubled).", style=MUTED))
            with Horizontal(classes="btnrow"):
                yield Button("✔ Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#c-handle").focus()

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        self.dismiss({k: self.query_one(f"#c-{k}", Input).value.strip()
                      for k in ("handle", "platform", "name", "phone", "location", "notes")})

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def customers_columns(t: DataTable) -> None:
    t.add_columns("#", "Customer ID", "Platform", "Name", "Phone", "Location", "Orders")


def customer_row(c: dict) -> list:
    n = c.get("orders_count", c.get("order_count", c.get("orders")))
    if isinstance(n, list):
        n = len(n)
    return [Text(s(c.get("id")), style=f"bold {C1}", justify="right"), Text(short(c.get("handle"), 28), style="bold"),
            Text(s(c.get("platform")), style=C3), short(c.get("name"), 26), s(c.get("phone")),
            Text(short(c.get("location"), 26), style=MUTED), Text(s(n), style=OK, justify="right")]


class CustomerPicker(ModalScreen[int | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Static("⌕  Pick a customer", classes="title")
            yield Input(placeholder="type to search — ID, name, phone…", id="cp-q")
            yield DataTable(id="cp-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        customers_columns(self.query_one(DataTable))
        self.query_one(Input).focus()
        self.search("")

    @on(Input.Changed)
    def changed(self, e: Input.Changed) -> None:
        self.search(e.value)

    @on(Input.Submitted)
    def sub(self) -> None:
        self.query_one(DataTable).focus()

    @work(exclusive=True)
    async def search(self, q: str) -> None:
        await asyncio.sleep(0.3)
        t = self.query_one(DataTable)
        res = await self.app.call("GET", "customers", params={"q": q, "per_page": 50})  # type: ignore
        t.clear()
        for c in (res or {}).get("data") or []:
            t.add_row(*customer_row(c), key=str(c.get("id")))

    @on(DataTable.RowSelected)
    def picked(self, e: DataTable.RowSelected) -> None:
        self.dismiss(int(e.row_key.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


# ═══════════════════════════ order table helpers ═════════════════════════
def orders_columns(t: DataTable) -> None:
    t.add_column("#", key="id")
    t.add_column("Status", key="status")
    t.add_column("Car", key="car")
    t.add_column("Part", key="part")
    t.add_column("Customer", key="cust")
    t.add_column("▣", key="ph")
    t.add_column("Price", key="price")
    t.add_column("Updated", key="upd")


def order_row(o: dict) -> list:
    quotes = o.get("quotes") or []
    price = Text("")
    if quotes:
        mine = [q for q in quotes if q.get("mine")]
        q = (mine or quotes)[0]
        tot = q.get("total") if q.get("total") not in (None, "") else q.get("price")
        price = Text(money(tot, s(q.get("currency"))), style=f"bold {OK}" if mine else "bold", justify="right")
        if len(quotes) > 1:
            price.append(f" +{len(quotes) - 1}", style=MUTED)
    car = Text(short(" ".join(x for x in (s(o.get("brand")), s(o.get("model"))) if x), 20), style="bold")
    if o.get("year"):
        car.append(f" {o['year']}", style=MUTED)
    cust = core.cust_short(o.get("customer"))
    cust.truncate(20, overflow="ellipsis")
    ph = len(o.get("photos") or [])
    return [Text(s(o.get("id")), style=f"bold {C1}", justify="right"), badge_short(o.get("status")), car,
            Text(short(o.get("part"), 30), style=C2), cust, Text(str(ph) if ph else "", style=MUTED, justify="right"),
            price, Text(ago(o.get("updated_at")), style=MUTED)]


def table_key(t: DataTable) -> str | None:
    if not t.row_count:
        return None
    try:
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
    except Exception:
        return None


# ═══════════════════════════════ main views ══════════════════════════════
class DashboardView(VerticalScroll):
    loaded = False

    def compose(self) -> ComposeResult:
        yield Static(id="hello")
        with Grid(id="cards"):
            for st in core.STATUSES:
                yield Card(st, f"{core.STATUS_ICON.get(st, '•')} {core.STATUS_SHORT.get(st, st)}",
                           core.STATUS_COLOR.get(st, "#aaaaaa"), {"status": st})
            yield Card("__mine", "$ Need my price", C2, {"mine": "waiting"})
            yield Card("__all", "∑ All orders", C3, {})
        with Horizontal(id="dash-bottom"):
            yield Static(id="bars")
            with Vertical(id="recent-box"):
                yield Static("◷  Recently updated  ·  enter / click to open", classes="box-title")
                yield DataTable(id="recent", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        orders_columns(self.query_one("#recent", DataTable))

    def activate(self, force: bool = False) -> None:
        if force or not self.loaded:
            self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        hour = dt.datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        self.query_one("#hello", Static).update(Text.assemble((f"{greet}, ", "bold"), (acct_name(app.info), f"bold {C1}"),
                                                              ("  ·  loading…", MUTED)))
        jobs = {st: {"status": st} for st in core.STATUSES}
        jobs["__mine"] = {"mine": "waiting"}
        jobs["__all"] = {}
        keys = list(jobs)
        vals = await asyncio.gather(*(asyncio.to_thread(app.api.total, **jobs[k]) for k in keys))
        counts = dict(zip(keys, vals))
        app.counts = counts
        for card in self.query(Card):
            card.set_count(counts.get(card.key))
        self.screen.update_statuslist()  # type: ignore
        total = counts.get("__all")
        self.query_one("#hello", Static).update(Text.assemble(
            (f"{greet}, ", "bold"), (acct_name(app.info), f"bold {C1}"),
            ("  ·  ", MUTED), (str(total if total is not None else "?"), f"bold {C3}"), (" orders", MUTED),
            ("  ·  ", MUTED), (str(counts.get("__mine") if counts.get("__mine") is not None else "?"), f"bold {C2}"),
            (" waiting for your price", MUTED),
            ("" if total is not None else "   (this key cannot read orders)", BAD)))
        # distribution bars
        mx = max([v for k, v in counts.items() if k in core.STATUSES and v] or [1])
        bars = Table.grid(padding=(0, 1))
        bars.add_column(no_wrap=True)
        bars.add_column(no_wrap=True)
        bars.add_column(justify="right", no_wrap=True)
        for st in core.STATUSES:
            n = counts.get(st) or 0
            col = core.STATUS_COLOR.get(st, "#aaaaaa")
            w = int(round(14 * n / mx)) if n else 0
            bars.add_row(Text(f"{core.STATUS_ICON.get(st, '•')} {core.STATUS_SHORT.get(st, st)}", style=col, no_wrap=True),
                         Text("█" * w, style=col) + Text("░" * (14 - w), style="#2a2a4a"),
                         Text(str(n), style=f"bold {col}"))
        self.query_one("#bars", Static).update(panel(bars, "◔ By status", C2))
        res = await app.call("GET", "orders", params={"sort": "updated", "per_page": 12}, quiet=True)
        t = self.query_one("#recent", DataTable)
        t.clear()
        for o in (res or {}).get("data") or []:
            t.add_row(*order_row(o), key=str(o.get("id")))
        self.loaded = True

    @on(DataTable.RowSelected, "#recent")
    def open_recent(self, e: DataTable.RowSelected) -> None:
        self.app.open_order(e.row_key.value)  # type: ignore


class OrdersView(Vertical):
    BINDINGS = [Binding("slash", "focus_search", "Search"), Binding("s", "quick_status", "Status"),
                Binding("right_square_bracket", "next_page", "Next page"),
                Binding("left_square_bracket", "prev_page", "Prev page"),
                Binding("r", "reload", "Refresh"), Binding("x", "export", "CSV"), Binding("f", "more", "Filters"),
                Binding("v", "toggle_preview", "Preview"),
                Binding("delete", "delete_row", "Delete", show=False)]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.extra: dict = {}
        self.page, self.pages, self.loaded, self._timer = 1, 1, False, None
        self._quiet = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="⌕  search car, VIN, part, customer, #152 …   ( / )", id="o-q")
            yield Select([(core.status_label(st), st) for st in core.STATUSES], prompt="all statuses", id="o-status")
            yield Select([("waiting for my price", "waiting"), ("I gave a price", "priced")], prompt="any price", id="o-mine")
            yield Select([("newest first", "newest"), ("oldest first", "oldest"), ("last updated", "updated")],
                         value="newest", allow_blank=False, id="o-sort")
            yield Button("⚲ More", id="o-more")
            yield Button("✚ New", id="o-new", variant="success")
        yield Static(id="chips")
        with Horizontal(id="o-main"):
            yield DataTable(id="o-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="o-preview"):
                yield Pic(id="pv-pic")
                yield Static(id="pv-strip")
                yield Static(id="pv-info")
        with Horizontal(classes="pager"):
            yield Button("◀", id="o-prev")
            yield Static(classes="pageinfo", id="o-pageinfo")
            yield Button("▶", id="o-next")
            yield Button("⇩ CSV", id="o-csv")

    def on_mount(self) -> None:
        orders_columns(self.query_one(DataTable))
        self.rows: dict = {}
        self.pv_id = None
        self.query_one("#o-preview").display = self.app.size.width >= 130
        self.query_one("#pv-pic", Pic).say(Text("highlight an order\nto see it here", style=MUTED, justify="center"))

    def activate(self, force: bool = False) -> None:
        if force or not self.loaded:
            self.load()
        self.query_one(DataTable).focus()

    def action_toggle_preview(self) -> None:
        pv = self.query_one("#o-preview")
        pv.display = not pv.display
        if pv.display:
            k = table_key(self.query_one(DataTable))
            if k:
                self.show_preview(k)

    @on(DataTable.RowHighlighted, "#o-table")
    def row_hl(self, e: DataTable.RowHighlighted) -> None:
        if self.query_one("#o-preview").display and e.row_key and e.row_key.value:
            self.show_preview(e.row_key.value)

    @work(exclusive=True, group="preview")
    async def show_preview(self, oid: str) -> None:
        await asyncio.sleep(0.15)
        o = self.rows.get(str(oid))
        if not o:
            return
        app: ACP4UApp = self.app  # type: ignore
        photos = sorted(o.get("photos") or [], key=lambda x: x.get("slot") or 0)
        strip = Text()
        taken = {x.get("slot") for x in photos}
        for i in range(1, 9):
            strip.append(f" {i} ", style=f"bold #0a0a16 on {C1}" if i in taken else f"{MUTED} on #1c1c2a")
        strip.append(f"  {len(photos)}/8", style=MUTED)
        self.query_one("#pv-strip", Static).update(strip)
        c = o.get("customer") if isinstance(o.get("customer"), dict) else {}
        head = Text.assemble((f" #{o.get('id')} ", f"bold #0a0a16 on {C1}"), " ")
        head.append_text(badge_short(o.get("status")))
        head.append(f"\n{car_line(o)}", style="bold")
        head.append(f"\n{s(o.get('part'))}", style=f"bold {C2}")
        rows = [("VIN", o.get("vin")), ("Part No.", o.get("part_number")), ("Side", o.get("side")),
                ("Colour", o.get("colour")), ("Fuel", o.get("fuel")), ("Type", o.get("vehicle_type")),
                ("Origin", o.get("origin")), ("Info", o.get("info"))]
        rows += [(a, b) for a, b in details_rows(o.get("details"))]
        rows += [("Customer", c.get("handle") or c.get("name")), ("Platform", c.get("platform")),
                 ("Phone", c.get("phone")), ("Location", c.get("location")), ("Ref", o.get("ref")),
                 ("Source", o.get("source")), ("By", o.get("created_by")), ("Created", ago(o.get("created_at"))),
                 ("Updated", ago(o.get("updated_at")))]
        parts = [head, kv(rows)]
        for q in o.get("quotes") or []:
            tot = q.get("total") if q.get("total") not in (None, "") else q.get("price")
            parts.append(Text.assemble(("$ ", OK), (f"{s(q.get('by'))}  ", "bold" if not q.get("mine") else f"bold {C2}"),
                                       (money(tot, s(q.get("currency"))), f"bold {OK}"), ("  ", ""),
                                       core.stock_text(q.get("stock"))))
        parts.append(Text("enter opens · v hides this", style=MUTED))
        self.query_one("#pv-info", Static).update(Group(*parts))
        pic = self.query_one("#pv-pic", Pic)
        self.pv_id = oid
        if not photos:
            pic.say(Text("no photos", style=MUTED, justify="center"))
            return
        pic.say(Text("loading…", style=MUTED, justify="center"))
        data = await app.image("photos", photos[0]["id"], quiet=True)
        if data is None:
            data = await app.image("photos", photos[0]["id"], thumb=True, quiet=True)
        if self.pv_id == oid:
            pic.set_data(data)

    def filters(self) -> dict:
        f = dict(self.extra)
        q = self.query_one("#o-q", Input).value.strip()
        if q:
            f["q"] = q
        for k in ("status", "mine", "sort"):
            v = sel_value(self.query_one(f"#o-{k}", Select))
            if v and not (k == "sort" and v == "newest"):
                f[k] = v
        return f

    def set_filters(self, f: dict) -> None:
        """Replace all filters (from cards, sidebar, search)."""
        self._quiet = True
        self.extra = {k: v for k, v in f.items() if k not in ("q", "status", "mine", "sort")}
        self.query_one("#o-q", Input).value = f.get("q", "")
        self.query_one("#o-status", Select).value = f.get("status") or Select.NULL
        self.query_one("#o-mine", Select).value = f.get("mine") or Select.NULL
        self.query_one("#o-sort", Select).value = f.get("sort") or "newest"
        self.page = 1
        self.call_after_refresh(self._unquiet)
        self.load()

    def _unquiet(self) -> None:
        self._quiet = False

    def schedule(self) -> None:
        if self._quiet:
            return
        if self._timer:
            self._timer.stop()
        self.page = 1
        self._timer = self.set_timer(0.35, self.load)

    @on(Input.Changed, "#o-q")
    def q_changed(self) -> None:
        self.schedule()

    @on(Input.Submitted, "#o-q")
    def q_sub(self) -> None:
        self.query_one(DataTable).focus()

    @on(Select.Changed)
    def sel_changed(self) -> None:
        self.schedule()

    @work(exclusive=True, group="orders")
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        t = self.query_one(DataTable)
        keep = table_key(t)
        t.loading = True
        f = self.filters()
        res = await app.call("GET", "orders", params={"per_page": app.per_page, **f, "page": self.page})
        t.loading = False
        chips = Text()
        for k, v in self.extra.items():
            chips.append(f" {k.replace('_', ' ')}: {v} ", style=f"bold #0a0a16 on {C3}")
            chips.append(" ")
        if self.extra:
            chips.append("  f = edit · ", style=MUTED)
        self.query_one("#chips", Static).update(chips)
        if res is None:
            return
        rows = res.get("data") or []
        meta = res.get("meta") or {}
        self.pages = int(meta.get("pages") or 1)
        t.clear()
        self.rows = {str(o.get("id")): o for o in rows}
        for o in rows:
            t.add_row(*order_row(o), key=str(o.get("id")))
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep))
            except Exception:
                pass
        total = meta.get("total", len(rows))
        self.query_one("#o-pageinfo", Static).update(Text.assemble(
            ("page ", MUTED), (f"{self.page}/{self.pages}", f"bold {C1}"), ("   ·   ", MUTED), (str(total), f"bold {C1}"),
            (" orders" if rows else " orders — nothing matches", MUTED), ("   ·   [ ] pages · enter opens · s status", MUTED)))
        self.query_one("#o-prev", Button).disabled = self.page <= 1
        self.query_one("#o-next", Button).disabled = self.page >= self.pages
        self.loaded = True

    @on(DataTable.RowSelected, "#o-table")
    def open_row(self, e: DataTable.RowSelected) -> None:
        self.app.open_order(e.row_key.value)  # type: ignore

    @on(Button.Pressed, "#o-prev")
    def action_prev_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.load()

    @on(Button.Pressed, "#o-next")
    def action_next_page(self) -> None:
        if self.page < self.pages:
            self.page += 1
            self.load()

    @on(Button.Pressed, "#o-new")
    def new(self) -> None:
        self.app.new_order()  # type: ignore

    @on(Button.Pressed, "#o-more")
    def action_more(self) -> None:
        def done(f):
            if f is not None:
                self.extra = f
                self.page = 1
                self.load()
        self.app.push_screen(FilterModal(self.extra), done)

    def action_focus_search(self) -> None:
        self.query_one("#o-q", Input).focus()

    def action_reload(self) -> None:
        self.load()

    def action_quick_status(self) -> None:
        oid = table_key(self.query_one(DataTable))
        if oid:
            self.app.change_status(oid, None, self.load)  # type: ignore

    def action_delete_row(self) -> None:
        oid = table_key(self.query_one(DataTable))
        if oid:
            self.app.delete_order(oid, self.load)  # type: ignore

    @on(Button.Pressed, "#o-csv")
    def action_export(self) -> None:
        self.export()

    @work(exclusive=True, group="export")
    async def export(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        f = self.filters()
        rows, page = [], 1
        app.notify("Collecting orders for CSV…", timeout=3)
        while True:
            res = await app.call("GET", "orders", params={**f, "per_page": 100, "page": page})
            if res is None:
                return
            rows += res.get("data") or []
            if page >= int((res.get("meta") or {}).get("pages") or 1):
                break
            page += 1
        app.dl_dir.mkdir(parents=True, exist_ok=True)
        out = app.dl_dir / f"orders-{dt.datetime.now():%Y%m%d-%H%M%S}.csv"
        cols = ["id", "status", "brand", "model", "year", "vin", "part", "part_number", "side", "colour", "info",
                "vehicle_type", "fuel", "origin", "engine_no", "gvw", "ref", "source", "customer_id", "customer_handle",
                "customer_platform", "customer_phone", "customer_location", "photos", "quotes", "created_at",
                "updated_at", "created_by"]
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for o in rows:
                c = o.get("customer") or {}
                row = {**o, "customer_id": c.get("id"), "customer_handle": c.get("handle"),
                       "customer_platform": c.get("platform"), "customer_phone": c.get("phone"),
                       "customer_location": c.get("location"), "photos": len(o.get("photos") or []),
                       "quotes": len(o.get("quotes") or [])}
                w.writerow([s(row.get(k)) for k in cols])
        app.notify(f"{len(rows)} orders → {out}", title="✔ CSV saved", severity="information", timeout=8)


class CustomersView(Vertical):
    BINDINGS = [Binding("slash", "focus_search", "Search"), Binding("a", "add", "Add customer"),
                Binding("right_square_bracket", "next_page", "Next page"),
                Binding("left_square_bracket", "prev_page", "Prev page"), Binding("r", "reload", "Refresh")]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.page, self.pages, self.loaded, self._timer = 1, 1, False, None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="⌕  search customers — ID, name, phone …   ( / )", id="cu-q")
            yield Button("✚ Add customer", id="cu-add", variant="success")
        yield DataTable(id="cu-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="pager"):
            yield Button("◀", id="cu-prev")
            yield Static(classes="pageinfo", id="cu-pageinfo")
            yield Button("▶", id="cu-next")

    def on_mount(self) -> None:
        customers_columns(self.query_one(DataTable))

    def activate(self, force: bool = False) -> None:
        if force or not self.loaded:
            self.load()
        self.query_one(DataTable).focus()

    @on(Input.Changed, "#cu-q")
    def q_changed(self) -> None:
        if self._timer:
            self._timer.stop()
        self.page = 1
        self._timer = self.set_timer(0.35, self.load)

    @on(Input.Submitted, "#cu-q")
    def q_sub(self) -> None:
        self.query_one(DataTable).focus()

    @work(exclusive=True)
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        t = self.query_one(DataTable)
        t.loading = True
        res = await app.call("GET", "customers", params={"q": self.query_one("#cu-q", Input).value.strip(),
                                                         "page": self.page, "per_page": app.per_page})
        t.loading = False
        if res is None:
            return
        rows = res.get("data") or []
        meta = res.get("meta") or {}
        self.pages = int(meta.get("pages") or 1)
        t.clear()
        for c in rows:
            t.add_row(*customer_row(c), key=str(c.get("id")))
        self.query_one("#cu-pageinfo", Static).update(Text.assemble(
            ("page ", MUTED), (f"{self.page}/{self.pages}", f"bold {C1}"), ("   ·   ", MUTED),
            (s(meta.get("total", len(rows))), f"bold {C1}"), (" customers   ·   enter opens · a adds", MUTED)))
        self.query_one("#cu-prev", Button).disabled = self.page <= 1
        self.query_one("#cu-next", Button).disabled = self.page >= self.pages
        self.loaded = True

    @on(DataTable.RowSelected, "#cu-table")
    def open_row(self, e: DataTable.RowSelected) -> None:
        self.app.push_screen(CustomerScreen(e.row_key.value))

    @on(Button.Pressed, "#cu-prev")
    def action_prev_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.load()

    @on(Button.Pressed, "#cu-next")
    def action_next_page(self) -> None:
        if self.page < self.pages:
            self.page += 1
            self.load()

    def action_focus_search(self) -> None:
        self.query_one("#cu-q", Input).focus()

    def action_reload(self) -> None:
        self.load()

    @on(Button.Pressed, "#cu-add")
    def action_add(self) -> None:
        def done(d):
            if d:
                self.add(d)
        self.app.push_screen(CustomerForm(title="✚  New customer"), done)

    @work
    async def add(self, d: dict) -> None:
        body = {k: v for k, v in d.items() if v}
        res = await self.app.call("POST", "customers", json_body=body)  # type: ignore
        if res:
            c = res.get("data") or {}
            if (res.get("meta") or {}).get("created") is False:
                self.app.notify(f"Already exists — opened customer #{c.get('id')}", severity="warning")
            else:
                self.app.notify(f"Customer #{c.get('id')} added", title="✔ Saved")
            self.load()
            if c.get("id"):
                self.app.push_screen(CustomerScreen(c["id"]))


class WatchView(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(id="watchbar"):
            yield Label("◉ Live")
            yield Switch(id="w-on")
            yield Label("check every")
            yield Input(str(self.app.conf.get("watch_interval", 20)), id="w-int", type="integer")  # type: ignore
            yield Label("seconds")
            yield Static(id="watchstate")
        yield DataTable(id="w-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Time", "Event", "#", "Status", "Car", "Part", "Customer")
        self.seen: dict = {}
        self.since = ""
        self.state(Text("off — flip the switch. New orders ring the bell and pop a notification.", style=MUTED))

    def activate(self, force: bool = False) -> None:
        self.query_one(Switch).focus()

    def state(self, t: Text) -> None:
        self.query_one("#watchstate", Static).update(t)

    @on(Switch.Changed, "#w-on")
    def toggled(self, e: Switch.Changed) -> None:
        app: ACP4UApp = self.app  # type: ignore
        app.watching = e.value
        if e.value:
            self.loop()
        else:
            self.workers.cancel_group(self, "watch")
            self.state(Text("◼ stopped", style=MUTED))

    def add_event(self, o: dict, kind: str) -> None:
        tag = {"new": Text(" NEW ", style=f"bold #0a0a16 on {C2}"), "upd": Text(" UPD ", style=f"bold #0a0a16 on {C1}"),
               "old": Text(" ··· ", style=MUTED)}[kind]
        t = parse_t(o.get("updated_at"))
        tbl = self.query_one(DataTable)
        key = f"{o.get('id')}-{o.get('updated_at')}"
        if key in tbl.rows:
            return
        tbl.add_row(Text(f"{t:%H:%M:%S}" if t else "", style=MUTED), tag, Text(s(o.get("id")), style=f"bold {C1}"),
                    badge_short(o.get("status")), Text(car_line(o), style="bold"), Text(short(o.get("part"), 40), style=C2),
                    core.cust_short(o.get("customer")), key=key)
        tbl.move_cursor(row=tbl.row_count - 1)

    @work(exclusive=True, group="watch")
    async def loop(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        try:
            iv = max(5, int(self.query_one("#w-int", Input).value or 20))
        except ValueError:
            iv = 20
        if not self.since:
            res = await app.call("GET", "orders", params={"sort": "updated", "per_page": 5})
            rows = (res or {}).get("data") or []
            for o in reversed(rows):
                self.seen[o.get("id")] = o.get("updated_at")
                self.add_event(o, "old")
            self.since = max((s(o.get("updated_at")) for o in rows), default="") or \
                dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        notify = shutil.which("notify-send")
        while True:
            for left in range(iv, 0, -1):
                self.state(Text.assemble(("● watching", f"bold {OK}"), (f"  ·  next check in {left:>2}s", MUTED)))
                await asyncio.sleep(1)
            self.state(Text("⟳ checking…", style=f"bold {C2}"))
            res = await app.call("GET", "orders", params={"updated_since": self.since, "sort": "updated",
                                                          "per_page": 100}, quiet=True)
            if res is None:
                self.state(Text("⚠ server did not answer — retrying", style=C3))
                await asyncio.sleep(2)
                continue
            fresh = [o for o in res.get("data") or [] if self.seen.get(o.get("id")) != o.get("updated_at")]
            for o in sorted(fresh, key=lambda x: s(x.get("updated_at"))):
                is_new = o.get("id") not in self.seen and o.get("created_at") == o.get("updated_at")
                self.seen[o.get("id")] = o.get("updated_at")
                self.add_event(o, "new" if is_new else "upd")
                self.since = max(self.since, s(o.get("updated_at")))
                if is_new:
                    app.bell()
                    app.notify(f"{car_line(o)} — {s(o.get('part'))}", title=f"✦ New order #{o.get('id')}", timeout=10)
                    if notify:
                        subprocess.Popen([notify, "-a", "ACP4U", f"New order #{o.get('id')}",
                                          f"{car_line(o)} — {s(o.get('part'))}"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @on(DataTable.RowSelected, "#w-table")
    def open_row(self, e: DataTable.RowSelected) -> None:
        self.app.open_order(e.row_key.value.split("-")[0])  # type: ignore


class InfoView(VerticalScroll):
    loaded = False

    def compose(self) -> ComposeResult:
        yield Static(id="info-body")
        with Horizontal(classes="btnrow"):
            yield Button("⟳ Refresh", id="i-refresh")
            yield Button("{ } Raw JSON", id="i-raw")

    def activate(self, force: bool = False) -> None:
        if force or not self.loaded:
            self.load()

    @on(Button.Pressed, "#i-refresh")
    def refresh_btn(self) -> None:
        self.load()

    @on(Button.Pressed, "#i-raw")
    def raw(self) -> None:
        self.app.push_screen(ResultModal("{ }  info", RJSON(json.dumps(self.app.info, ensure_ascii=False))))  # type: ignore

    @work(exclusive=True)
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        res = await app.call("GET", "info")
        if res:
            app.set_info(res.get("data") or {})
        info = app.info
        parts = [panel(kv([("Account", acct_name(info)), ("Key name", key_name(info)),
                           ("Key", Text(core.mask(app.api.key), style=C3)), ("Server", app.api.base),
                           ("Saved in", str(core.CONF_FILE))]), "Connection", C1)]
        perms = perms_list(info)
        if perms:
            g = Table.grid(padding=(0, 3))
            g.add_column()
            g.add_column()
            cells = [Text.assemble(("✔ " if ok else "✖ ", f"bold {OK}" if ok else BAD), (lab, "bold" if ok else MUTED))
                     for lab, ok in perms]
            for i in range(0, len(cells), 2):
                g.add_row(*cells[i:i + 2], *([""] * (2 - len(cells[i:i + 2]))))
            parts.append(panel(g, "Permissions of this key", C2))
        st = Text()
        for x in core.STATUSES:
            st.append_text(badge(x))
            st.append(" ")
        parts.append(panel(st, "Statuses", C3))
        if core.CURRENCIES:
            parts.append(panel(Text("  ".join(core.CURRENCIES), style=f"bold {OK}"), "Currencies", OK))
        lim = info.get("limits")
        if isinstance(lim, dict) and lim:
            parts.append(panel(kv([(k, v if not isinstance(v, (dict, list)) else json.dumps(v)) for k, v in lim.items()]),
                               "Limits", C1))
        self.query_one("#info-body", Static).update(Group(*parts))
        self.loaded = True


class ConsoleView(Vertical):
    BINDINGS = [Binding("ctrl+r", "send", "Send")]

    def compose(self) -> ComposeResult:
        yield Static(Text("Any API call — the key is added for you.  Route e.g. orders/152 · params a=1&b=2 · ctrl+r sends",
                          style=MUTED))
        with Horizontal(id="c-row"):
            yield Select([(m, m) for m in ("GET", "POST", "PATCH", "PUT", "DELETE")], value="GET", allow_blank=False,
                         id="c-method")
            yield Input("info", placeholder="route", id="c-route")
            yield Input(placeholder="params  a=1&b=2", id="c-params")
            yield Button("Send ⏎", id="c-send", variant="primary")
        yield TextArea(id="c-body")
        with VerticalScroll(id="c-out"):
            yield Static(Text("response shows here", style=MUTED), id="c-res")

    def activate(self, force: bool = False) -> None:
        self.query_one("#c-route").focus()
        self.query_one("#c-body", TextArea).border_title = "JSON body (POST / PATCH / PUT)"

    @on(Input.Submitted)
    @on(Button.Pressed, "#c-send")
    def action_send(self) -> None:
        self.send()

    @work(exclusive=True)
    async def send(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        m = sel_value(self.query_one("#c-method", Select)) or "GET"
        route = self.query_one("#c-route", Input).value.strip().lstrip("/") or "info"
        params = {}
        for part in self.query_one("#c-params", Input).value.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip()] = v.strip()
        body = None
        txt = self.query_one("#c-body", TextArea).text.strip()
        out = self.query_one("#c-res", Static)
        if txt and m != "GET":
            try:
                body = json.loads(txt)
            except json.JSONDecodeError as e:
                out.update(Text(f"✖ bad JSON: {e}", style=BAD))
                return
        out.update(Text("sending…", style=MUTED))
        try:
            res = await asyncio.to_thread(app.api.call, m, route, params=params, json_body=body)
            out.update(Group(Text(f"✔ {m} {route}", style=f"bold {OK}"), RJSON(json.dumps(res, ensure_ascii=False))))
        except APIError as e:
            out.update(Group(Text(f"✖ HTTP {e.status}", style=f"bold {BAD}"), RJSON(json.dumps(e.payload, ensure_ascii=False))))


class SettingsView(VerticalScroll):
    def compose(self) -> ComposeResult:
        app: ACP4UApp = self.app  # type: ignore
        c = app.conf
        yield Static("⚙  Settings", classes="section")
        with Grid(classes="fgrid"):
            yield Label("Server")
            yield Input(app.api.base, id="s-base")
            yield Label("Rows per page")
            yield Input(str(app.per_page), id="s-pp", type="integer")
            yield Label("Watch every (s)")
            yield Input(str(c.get("watch_interval", 20)), id="s-watch", type="integer")
            yield Label("Download folder")
            yield Input(str(app.dl_dir), id="s-dl")
            yield Label("Theme")
            yield Select([(n, n) for n in sorted(app.available_themes)], value=app.theme, allow_blank=False, id="s-theme")
            yield Label("Photos")
            yield Select([(lab, k) for k, lab in IMG_MODES.items()],
                         value=c.get("images") if c.get("images") in IMG_MODES else "auto", allow_blank=False,
                         id="s-images")
            yield Label("")
            yield Static(Text.assemble(("now: ", MUTED), (IMG_MODES.get(IMG_MODE, IMG_MODE).split(" —")[0], f"bold {C1}"),
                                       ("  · changes apply on next start", MUTED)), id="s-imgnow")
        with Horizontal(classes="btnrow"):
            yield Button("✔ Save", id="s-save", variant="primary")
        yield Static("⛨  Proxy  —  SOCKS5 · SOCKS4 · HTTP, with user / password", classes="section")
        yield ProxyForm(app.conf.get("proxy"), lambda: self.app.api.base, on_save=self.save_proxy, id="s-proxy")  # type: ignore
        yield Static("🔑  Key", classes="section")
        yield Static(id="s-key")
        with Horizontal(classes="btnrow"):
            yield Button("Change key", id="s-change", variant="warning")
            yield Button("Log out (forget key)", id="s-logout", variant="error")

    def activate(self, force: bool = False) -> None:
        app: ACP4UApp = self.app  # type: ignore
        self.query_one("#s-key", Static).update(Text.assemble(
            ("  key ", MUTED), (core.mask(app.api.key), f"bold {C3}"), ("   saved in ", MUTED), (str(core.CONF_FILE), C1)))

    def save_proxy(self, p: dict) -> None:
        app: ACP4UApp = self.app  # type: ignore
        app.conf["proxy"] = p
        core.save_conf(app.conf)
        if core.PROXY_OVERRIDE is not None:
            app.notify("Saved — but this run still uses --proxy / ACP4U_PROXY.", severity="warning")
        app.api = API(app.api.base, app.api.key, core.effective_proxy(app.conf))
        app.notify(core.proxy_label(core.effective_proxy(app.conf)), title="⛨ Proxy in use")

    @on(Select.Changed, "#s-theme")
    def theme(self, e: Select.Changed) -> None:
        if e.value and e.value is not Select.NULL:
            self.app.theme = str(e.value)

    @on(Button.Pressed, "#s-save")
    def save(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        c = app.conf
        try:
            c["per_page"] = max(1, min(100, int(self.query_one("#s-pp", Input).value or 25)))
            c["watch_interval"] = max(5, int(self.query_one("#s-watch", Input).value or 20))
        except ValueError:
            self.app.notify("Numbers only, please", severity="error")
            return
        c["download_dir"] = self.query_one("#s-dl", Input).value.strip() or core.DEFAULT_DL
        c["theme"] = app.theme
        c["images"] = sel_value(self.query_one("#s-images", Select)) or "auto"
        base = self.query_one("#s-base", Input).value.strip()
        if base and base != app.api.base:
            c["base"] = base
            app.api = API(base, app.api.key, core.effective_proxy(c))
        core.save_conf(c)
        self.app.notify("Settings saved", title="✔")

    @on(Button.Pressed, "#s-change")
    def change(self) -> None:
        def done(ok):
            if ok:
                self.activate()
                self.app.notify("New key saved", title="✔")
        self.app.push_screen(LoginScreen(modal=True), done)

    @on(Button.Pressed, "#s-logout")
    def logout(self) -> None:
        def done(ok):
            if ok:
                c = self.app.conf  # type: ignore
                c.pop("key", None)
                core.save_conf(c)
                self.app.exit(message="Key forgotten. Run acp4u to connect again.")
        self.app.push_screen(ConfirmModal("Forget the saved key?", "You will need to paste it again next time.",
                                          yes="Log out"), done)


class ProxyForm(Vertical):
    """Proxy editor: mode, SOCKS5/4/HTTP type, host, port, user, password, presets, test, exit IP."""

    def __init__(self, proxy: dict | None, base_getter, on_save=None, **kw) -> None:
        super().__init__(classes="proxyform", **kw)
        self.p = dict(proxy or {"mode": "system"})
        self.base_getter, self.on_save = base_getter, on_save

    def compose(self) -> ComposeResult:
        p = self.p
        typ = p.get("type") if p.get("type") in core.DEFAULT_PORTS else "socks5h"
        with Horizontal(classes="px-row"):
            yield Select([("◎ System proxy (env vars)", "system"), ("○ Direct — no proxy", "none"),
                          ("⛨ Custom proxy", "custom")], value=p.get("mode") or "system", allow_blank=False, id="px-mode")
            yield Select([(f"★ {n}   {ty}://{h}:{po}", i) for i, (n, ty, h, po) in enumerate(core.PROXY_PRESETS)],
                         prompt="★ presets (Tor, v2ray, Clash…)", id="px-preset")
        yield Input(placeholder="paste a proxy URL and press enter:  socks5h://user:pass@127.0.0.1:1080", id="px-url")
        with Grid(classes="fgrid px-fields"):
            yield Label("Type")
            yield Select([(lab, k) for k, lab in core.PROXY_TYPES], value=typ, allow_blank=False, id="px-type")
            yield Label("Host")
            yield Input(s(p.get("host")), placeholder="127.0.0.1", id="px-host")
            yield Label("Port")
            yield Input(s(p.get("port")), placeholder=str(core.DEFAULT_PORTS[typ]), type="integer", id="px-port")
            yield Label("Username")
            yield Input(s(p.get("user")), placeholder="optional", id="px-user")
            yield Label("Password")
            yield Input(s(p.get("password")), placeholder="optional", password=True, id="px-pass")
            yield Label("")
            yield Button("👁 show password", id="px-show")
        with Horizontal(classes="btnrow"):
            yield Button("⚡ Test connection", id="px-test", variant="warning")
            yield Button("🌐 My exit IP", id="px-ip", tooltip="Asks api.ipify.org through this proxy")
            if self.on_save:
                yield Button("✔ Save & use", id="px-save", variant="primary")
        yield Static(id="px-status")

    def on_mount(self) -> None:
        self.sync()
        st = Text()
        if not core.socks_available():
            st.append("✖ SOCKS support missing — pip install pysocks  (or sudo apt install python3-socks)\n", style=BAD)
        if core.env_proxy():
            st.append("system proxy: ", style=MUTED)
            st.append(core.proxy_label({"mode": "system"})[8:], style=C1)
        if core.PROXY_OVERRIDE is not None:
            st.append(f"\nthis run uses --proxy / ACP4U_PROXY: {core.proxy_label(core.PROXY_OVERRIDE)}", style=C3)
        self.status(st)

    def status(self, t) -> None:
        self.query_one("#px-status", Static).update(t)

    def sync(self) -> None:
        custom = sel_value(self.query_one("#px-mode", Select)) == "custom"
        for w in self.query(".px-fields Input, .px-fields Select, #px-show"):
            w.disabled = not custom
        typ = sel_value(self.query_one("#px-type", Select))
        noauth = typ in ("socks4", "socks4a")
        self.query_one("#px-pass", Input).disabled = not custom or noauth
        self.query_one("#px-port", Input).placeholder = str(core.DEFAULT_PORTS.get(typ, 1080))

    @on(Select.Changed, "#px-mode")
    @on(Select.Changed, "#px-type")
    def changed(self) -> None:
        self.sync()

    def fill(self, p: dict) -> None:
        self.query_one("#px-mode", Select).value = p.get("mode", "custom")
        if p.get("mode") == "custom":
            self.query_one("#px-type", Select).value = p.get("type") or "socks5h"
            self.query_one("#px-host", Input).value = s(p.get("host"))
            self.query_one("#px-port", Input).value = s(p.get("port"))
            self.query_one("#px-user", Input).value = s(p.get("user"))
            self.query_one("#px-pass", Input).value = s(p.get("password"))
        self.call_after_refresh(self.sync)

    @on(Select.Changed, "#px-preset")
    def preset(self, e: Select.Changed) -> None:
        if e.value is Select.NULL or e.value is None:
            return
        n, typ, host, port = core.PROXY_PRESETS[int(e.value)]
        self.fill({"mode": "custom", "type": typ, "host": host, "port": port, "user": "", "password": ""})
        self.status(Text(f"★ {n} — make sure that app is running, then press Test.", style=C3))

    @on(Input.Submitted, "#px-url")
    def url(self, e: Input.Submitted) -> None:
        try:
            p = core.parse_proxy_url(e.value)
        except ValueError as err:
            self.status(Text(f"✖ {err}", style=BAD))
            return
        self.fill(p)
        e.input.value = ""
        self.status(Text(f"✔ {core.proxy_label(p)}", style=OK))

    @on(Button.Pressed, "#px-show")
    def show(self) -> None:
        i = self.query_one("#px-pass", Input)
        i.password = not i.password

    def get(self) -> dict:
        """The proxy as typed. Raises ValueError when custom fields are wrong."""
        mode = sel_value(self.query_one("#px-mode", Select)) or "system"
        if mode != "custom":
            return {**self.p, "mode": mode}
        typ = sel_value(self.query_one("#px-type", Select)) or "socks5h"
        host = self.query_one("#px-host", Input).value.strip().strip("[]")
        if not host:
            raise ValueError("Type the proxy host (e.g. 127.0.0.1).")
        if "://" in host:
            return core.parse_proxy_url(host)
        port_s = self.query_one("#px-port", Input).value.strip()
        try:
            port = int(port_s) if port_s else core.DEFAULT_PORTS[typ]
        except ValueError:
            raise ValueError("The port must be a number.")
        if not 1 <= port <= 65535:
            raise ValueError("The port must be 1-65535.")
        user = self.query_one("#px-user", Input).value.strip()
        pw = "" if typ in ("socks4", "socks4a") else self.query_one("#px-pass", Input).value
        return {"mode": "custom", "type": typ, "host": host, "port": port, "user": user, "password": pw}

    def checked(self) -> dict | None:
        try:
            p = self.get()
        except ValueError as e:
            self.status(Text(f"✖ {e}", style=BAD))
            return None
        if p.get("mode") == "custom" and p["type"].startswith("socks") and not core.socks_available():
            self.status(Text("✖ SOCKS support missing — pip install pysocks", style=BAD))
            return None
        return p

    @on(Button.Pressed, "#px-test")
    def test(self) -> None:
        p = self.checked()
        if p:
            self.run_test(p)

    @work(exclusive=True, group="px")
    async def run_test(self, p: dict) -> None:
        self.status(Text(f"⚡ testing {core.proxy_label(p)} …", style=C3))
        ok, ms, msg = await asyncio.to_thread(core.test_connection, self.base_getter(), p)
        self.status(Text(("✔ " if ok else "✖ ") + msg, style=OK if ok else BAD))

    @on(Button.Pressed, "#px-ip")
    def ip(self) -> None:
        p = self.checked()
        if p:
            self.run_ip(p)

    @work(exclusive=True, group="px")
    async def run_ip(self, p: dict) -> None:
        self.status(Text("🌐 asking api.ipify.org …", style=C3))
        try:
            ip = await asyncio.to_thread(core.exit_ip, p)
            self.status(Text.assemble(("🌐 the world sees you as  ", MUTED), (ip, f"bold {OK}"),
                                      (f"   via {core.proxy_label(p)}", MUTED)))
        except APIError as e:
            self.status(Text("✖ " + (e.payload.get("message") or str(e)) +
                             (f"  [{e.payload['detail']}]" if e.payload.get("detail") else ""), style=BAD))

    @on(Button.Pressed, "#px-save")
    def save(self) -> None:
        p = self.checked()
        if p and self.on_save:
            self.on_save(p)
            self.status(Text(f"✔ saved — {core.proxy_label(p)}.  Press Test to check it.", style=OK))


NAV = [("dash", "◈  Dashboard"), ("orders", "▤  Orders"), ("mine", "$  Need my price"), ("customers", "☺  Customers"),
       ("watch", "◉  Live watch"), ("import", "⇪  Import Excel"), ("info", "⚿  Key & permissions"),
       ("console", "⌘  API console"), ("settings", "⚙  Settings")]
NAV_TITLES = {k: v[3:] for k, v in NAV}


class MainScreen(Screen):
    BINDINGS = [Binding("n", "new_order", "New order"), Binding("g", "goto", "Go to #"),
                Binding("f5", "refresh", "Refresh"), Binding("f1", "help", "Keys")] + \
               [Binding(str(i + 1), f"nav('{k}')", NAV_TITLES[k], show=False) for i, (k, _) in enumerate(NAV)]

    def __init__(self, start: tuple | None = None) -> None:
        super().__init__()
        self.start = start
        self.current = "dash"

    def compose(self) -> ComposeResult:
        yield TopBar("Dashboard")
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(mini_logo(), id="logo")
                yield OptionList(*[Option(Text.assemble((lab[:3], f"bold {C1}"), (lab[3:], ""),
                                                        (f"  {i + 1}", MUTED)), id=k)
                                   for i, (k, lab) in enumerate(NAV)], id="nav")
                yield Static("STATUS", classes="side-title")
                yield OptionList(id="statuslist")
                yield Static(Text.assemble(("n ", f"bold {C1}"), ("new order   ", MUTED), ("g ", f"bold {C1}"), ("go to #\n", MUTED),
                                           ("^p", f"bold {C1}"), (" commands  ", MUTED), ("f1", f"bold {C1}"), (" keys\n", MUTED),
                                           ("esc", f"bold {C1}"), (" back       ", MUTED), ("^q", f"bold {C1}"), (" quit", MUTED)),
                             id="side-foot")
            with ContentSwitcher(id="views", initial="dash"):
                yield DashboardView(id="dash")
                yield OrdersView(id="orders")
                yield CustomersView(id="customers")
                yield WatchView(id="watch")
                yield InfoView(id="info-view")
                yield ConsoleView(id="console-view")
                yield SettingsView(id="settings-view")
        yield Footer()

    def on_mount(self) -> None:
        self.update_statuslist()
        self.query_one("#dash", DashboardView).activate()
        st = self.start
        if st:
            kind = st[0]
            if kind == "order":
                self.app.open_order(st[1])  # type: ignore
            elif kind == "new":
                self.app.new_order()  # type: ignore
            elif kind == "import":
                self.action_nav("import", st[1] if len(st) > 1 else None)
            elif kind == "orders":
                self.open_orders(st[1] if len(st) > 1 else {}, "Orders")
            else:
                self.action_nav(kind)

    def update_statuslist(self) -> None:
        counts = getattr(self.app, "counts", {}) or {}
        ol = self.query_one("#statuslist", OptionList)
        ol.clear_options()
        for st in core.STATUSES:
            n = counts.get(st)
            lab = Text.assemble((f"{core.STATUS_ICON.get(st, '•')} ", f"bold {core.STATUS_COLOR.get(st, '#aaa')}"),
                                (core.STATUS_SHORT.get(st, st), core.STATUS_COLOR.get(st, "#aaa")))
            if n is not None:
                lab.append(f"  {n}", style="bold" if n else MUTED)
            ol.add_option(Option(lab, id=st))

    @on(OptionList.OptionSelected, "#nav")
    def nav_sel(self, e: OptionList.OptionSelected) -> None:
        self.action_nav(e.option.id)

    @on(OptionList.OptionSelected, "#statuslist")
    def status_sel(self, e: OptionList.OptionSelected) -> None:
        self.open_orders({"status": e.option.id}, e.option.id)

    def show_view(self, view_id: str, crumb: str) -> None:
        self.query_one(ContentSwitcher).current = view_id
        self.query_one(TopBar).set_crumb(crumb)
        self.current = view_id

    def action_nav(self, key: str, path: str | None = None) -> None:
        if key == "import":
            self.app.import_excel(path)  # type: ignore
            return
        if key == "mine":
            self.open_orders({"mine": "waiting"}, "Need my price")
            return
        view_id = {"info": "info-view", "console": "console-view", "settings": "settings-view"}.get(key, key)
        self.show_view(view_id, NAV_TITLES.get(key, key))
        self.query_one(f"#{view_id}").activate()  # type: ignore
        try:
            self.query_one("#nav", OptionList).highlighted = [k for k, _ in NAV].index(key)
        except ValueError:
            pass

    def open_orders(self, filters: dict, title: str = "Orders") -> None:
        self.show_view("orders", "Orders" + (f" › {title}" if title and title != "Orders" else ""))
        ov = self.query_one(OrdersView)
        ov.set_filters(filters)
        tbl = ov.query_one(DataTable)
        tbl.focus()
        self.call_after_refresh(tbl.focus)

    def action_new_order(self) -> None:
        self.app.new_order()  # type: ignore

    def action_goto(self) -> None:
        self.app.action_goto()  # type: ignore

    def action_help(self) -> None:
        self.app.push_screen(HelpModal())

    def action_refresh(self) -> None:
        view = self.query_one(ContentSwitcher).current
        if view:
            self.query_one(f"#{view}").activate(force=True)  # type: ignore
        if view != "dash":
            self.query_one(DashboardView).loaded = False

    def on_screen_resume(self) -> None:
        # coming back from an order: refresh what is visible
        view = self.query_one(ContentSwitcher).current
        if view in ("orders", "dash", "customers"):
            self.query_one(f"#{view}").activate(force=True)  # type: ignore


# ═══════════════════════════════ order screen ════════════════════════════
class OrderScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("s", "status", "Status"), Binding("e", "edit", "Edit"),
                Binding("c", "customer", "Customer"), Binding("p", "add_photos", "Add photos"),
                Binding("m", "price", "My price"), Binding("w", "download", "Download"),
                Binding("r", "reload", "Refresh"), Binding("j", "json", "JSON"),
                Binding("delete", "delete", "Delete"),
                Binding("1", "tab('t-over')", "Overview", show=False), Binding("2", "tab('t-photos')", "Photos", show=False),
                Binding("3", "tab('t-prices')", "Prices", show=False), Binding("4", "tab('t-all')", "All data", show=False)]

    def __init__(self, oid) -> None:
        super().__init__()
        self.oid = str(oid)
        self.o: dict = {}

    def compose(self) -> ComposeResult:
        yield TopBar(f"Orders › #{self.oid}")
        yield Static(id="o-head")
        with TabbedContent(id="o-tabs", initial="t-over"):
            with TabPane("◈ Overview  1", id="t-over"):
                with Horizontal(id="o-body"):
                    with VerticalScroll(id="o-left"):
                        with Horizontal(id="o-panels"):
                            yield Static(id="p-car")
                            yield Static(id="p-part")
                        yield Static(id="p-cust")
                        yield Static(id="p-details")
                        yield Static(id="p-extra")
                        yield Static(id="p-quotes")
                        yield Static(id="p-meta")
                    with VerticalScroll(id="o-right"):
                        yield Static(id="ph-title", classes="box-title")
                        yield Grid(id="thumbs")
            with TabPane("▣ Photos  2", id="t-photos"):
                with VerticalScroll():
                    yield Static(id="gal-title", classes="box-title")
                    yield Grid(id="gallery")
            with TabPane("$ Prices  3", id="t-prices"):
                yield VerticalScroll(id="quotes-box")
            with TabPane("{ } All data  4", id="t-all"):
                with VerticalScroll():
                    yield Static(id="all-data")
        with Horizontal(classes="actions"):
            yield Button("◆ Status", id="a-status", variant="primary")
            yield Button("✎ Edit", id="a-edit")
            yield Button("☺ Customer", id="a-customer")
            yield Button("▣ Add photos", id="a-photos")
            yield Button("$ My price", id="a-price", variant="success")
            yield Button("⇩ Download", id="a-download")
            yield Button("✖ Delete", id="a-delete", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    def folder_name(self) -> str:
        return f"order-{self.oid}"

    def action_tab(self, tab: str) -> None:
        self.query_one("#o-tabs", TabbedContent).active = tab

    @work(exclusive=True)
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        self.query_one("#o-head", Static).update(Text("loading…", style=MUTED))
        res = await app.call("GET", f"orders/{self.oid}")
        if res is None:
            self.query_one("#o-head", Static).update(Text("Could not load this order.", style=BAD))
            return
        o = self.o = res.get("data") or {}
        head = Text()
        head.append(f" #{o.get('id')} ", style=f"bold #0a0a16 on {C1}")
        head.append("  ")
        head.append_text(badge(o.get("status")))
        head.append(f"   {car_line(o)}", style="bold")
        head.append("  —  ", style=MUTED)
        head.append(s(o.get("part")), style=f"bold {C2}")
        if o.get("ref"):
            head.append("    ref ", style=MUTED)
            head.append(s(o["ref"]), style=f"bold {C3}")
        if o.get("source"):
            head.append("   via ", style=MUTED)
            head.append(s(o["source"]), style="bold")
        self.query_one("#o-head", Static).update(head)

        # ── overview ──
        self.query_one("#p-car", Static).update(panel(kv([(lab, o.get(k)) for k, lab in core.CAR_FIELDS]), "Car", C1))
        self.query_one("#p-part", Static).update(panel(kv([(lab, o.get(k)) for k, lab in core.PART_FIELDS]), "Part", C2))
        c = o.get("customer")
        if isinstance(c, dict):
            cb = kv([("Customer #", c.get("id"))] + [(lab, c.get(k)) for k, lab in core.CUST_FIELDS] +
                    extra_rows(c, CUSTOMER_KEYS))
        else:
            cb = Text("not visible with this key" if "customer" in o else "—", style=MUTED)
        self.query_one("#p-cust", Static).update(panel(cb, "Customer  (c)", C3))
        det = details_rows(o.get("details"))
        self.query_one("#p-details", Static).update(panel(kv(det), "Details", C1) if det else "")
        extra = extra_rows(o, ORDER_KEYS)
        self.query_one("#p-extra", Static).update(panel(kv(extra), "More fields", C3) if extra else "")
        quotes = o.get("quotes") or []
        if quotes:
            self.query_one("#p-quotes", Static).update(panel(core.quotes_table(quotes), "Prices  (3 = with photos)", OK))
        else:
            self.query_one("#p-quotes", Static).update(
                panel(Text("No prices yet — press m to give yours.", style=MUTED), "Prices", OK) if "quotes" in o else "")
        foot = Text.assemble(("created ", MUTED), core.when(o.get("created_at")))
        if o.get("created_by"):
            foot.append("  by ", style=MUTED)
            foot.append(s(o["created_by"]), style="bold")
        foot.append("     updated ", style=MUTED)
        foot.append_text(core.when(o.get("updated_at")))
        self.query_one("#p-meta", Static).update(foot)
        photos = sorted(o.get("photos") or [], key=lambda x: x.get("slot") or 0)
        self.query_one("#ph-title", Static).update(f"▣  Photos {len(photos)}/8  ·  2 = big gallery")
        taken = {x.get("slot"): x for x in photos}
        first_free = next((j for j in range(1, 9) if j not in taken), 0)
        grid = self.query_one("#thumbs", Grid)
        await grid.remove_children()
        await grid.mount_all([Thumb(taken.get(i), i, full=True, group=photos) for i in range(1, 9)
                              if i in taken or i == first_free])

        # ── tab labels ──
        tabs = self.query_one("#o-tabs", TabbedContent)
        tabs.get_tab("t-photos").label = f"▣ Photos {len(photos)}/8  2"
        tabs.get_tab("t-prices").label = f"$ Prices {len(quotes)}  3"

        # ── big gallery: every photo, as large as fits ──
        self.query_one("#gal-title", Static).update(Text.assemble(
            (f"▣  {len(photos)} of 8 photos", f"bold {C2}"), ("   click one for full screen · ← → inside", MUTED),
            (f"   drawn as: {IMG_MODES.get(IMG_MODE, IMG_MODE).split(' —')[0]}", MUTED),
            ("" if SHARP_CLS else "    F2 = open in a sharp window (real pixels)", f"bold {C3}")))
        gal = self.query_one("#gallery", Grid)
        await gal.remove_children()
        await gal.mount_all([Thumb(x, x.get("slot") or i + 1, full=True, group=photos, classes="thumb big")
                             for i, x in enumerate(photos)] +
                            ([Thumb(None, first_free, classes="thumb big")] if first_free else []))

        # ── prices: one card per account, with its photos ──
        box_ = self.query_one("#quotes-box", VerticalScroll)
        await box_.remove_children()
        cards = []
        if not quotes:
            cards.append(Static(Text("No prices yet" if "quotes" in o else "This key cannot see prices.", style=MUTED),
                                classes="box-title"))
        for q in sorted(quotes, key=lambda q: not q.get("mine")):
            cur = s(q.get("currency"))
            total = q.get("total")
            if total in (None, "") and q.get("price") not in (None, ""):
                total = (to_num(q.get("price")) or 0) + (to_num(q.get("shipping")) or 0)
            head_q = Text.assemble((f" {s(q.get('by')) or '?'} ", f"bold #0a0a16 on {C2 if q.get('mine') else C1}"),
                                   ("  (me)" if q.get("mine") else "", f"bold {C2}"),
                                   ("    total ", MUTED), (money(total, cur), f"bold {OK}"))
            body = kv([("Price", money(q.get("price"), cur)), ("Shipping", money(q.get("shipping"), cur)),
                       ("Stock", core.stock_text(q.get("stock"))), ("Qty", q.get("qty")), ("Delivery", q.get("delivery")),
                       ("Note", q.get("note")), ("User id", q.get("user_id")),
                       ("Updated", core.when(q.get("updated_at")) if q.get("updated_at") else None)] +
                      extra_rows(q, {"by", "user_id", "mine", "price", "shipping", "total", "currency", "stock", "qty",
                                     "delivery", "note", "updated_at", "photos"}))
            qph = sorted(q.get("photos") or [], key=lambda x: x.get("slot") or 0)
            card = Vertical(Static(head_q), Static(body),
                            Grid(*[Thumb(x, x.get("slot") or i + 1, route="quote-photos", full=True, group=qph,
                                         classes="thumb qbig") for i, x in enumerate(qph)], classes="qgrid"),
                            classes="qcard")
            cards.append(card)
        await box_.mount_all(cards)

        # ── all data: every field the API sent ──
        t = Table(box=box.SIMPLE, header_style=f"bold {C2}", expand=True, show_lines=False)
        t.add_column("Field", style=C1, no_wrap=True, ratio=1)
        t.add_column("Value", overflow="fold", ratio=3)
        for k, v in flatten(o):
            t.add_row(k, v)
        self.query_one("#all-data", Static).update(Group(
            Text(f"Every field of order #{self.oid} as the API sent it ({len(flatten(o))} values)", style=MUTED),
            t, Text("raw JSON", style=f"bold {C2}"), RJSON(json.dumps(o, ensure_ascii=False))))

    def thumb_clicked(self, th: Thumb) -> None:
        if not th.photo:
            self.action_add_photos()
            return
        group = th.group or [th.photo]
        idx = group.index(th.photo) if th.photo in group else 0

        def done(changed):
            if changed:
                self.load()
        if th.route == "quote-photos":
            self.app.push_screen(PhotoViewer(group, idx, "quote-photos", None, f"{self.folder_name()}-price-photos",
                                             "photo with a price"), done)
        else:
            self.app.push_screen(PhotoViewer(group, idx, "photos", self, self.folder_name(),
                                             f"order #{self.oid}"), done)

    # ── photo ops used by the viewer ──
    async def remove_photo(self, p: dict) -> bool:
        res = await self.app.call("DELETE", f"orders/{self.oid}/photos/{p.get('slot')}")  # type: ignore
        if res:
            self.app.notify(f"Photo in slot {p.get('slot')} removed", title="✔")
        return bool(res)

    async def put_photo(self, slot, files: list, urls: list) -> bool:
        app: ACP4UApp = self.app  # type: ignore
        if files:
            res = await app.call("POST", f"orders/{self.oid}/photos/{slot}", files=[file_tuple("photo", files[0])])
        elif urls:
            res = await app.call("PATCH", f"orders/{self.oid}", json_body={"photos": [{"slot": slot, "url": urls[0]}]})
        else:
            return False
        if res:
            app.notify(f"Slot {slot} replaced", title="✔")
        return bool(res)

    # ── actions ──
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self.load()

    @on(Button.Pressed, "#a-status")
    def action_status(self) -> None:
        self.app.change_status(self.oid, self.o.get("status"), self.load)  # type: ignore

    @on(Button.Pressed, "#a-edit")
    def action_edit(self) -> None:
        def done(r):
            if r:
                self.load()
        self.app.push_screen(OrderForm(self.o), done)

    @on(Button.Pressed, "#a-customer")
    def action_customer(self) -> None:
        c = self.o.get("customer") if isinstance(self.o.get("customer"), dict) else {}

        def done(d):
            if d is None:
                return
            if d.get("__pick"):
                self.app.push_screen(CustomerPicker(), lambda cid: cid and self.move_to(cid))
                return
            diff = {k: v for k, v in d.items() if v != s(c.get(k))}
            if diff:
                self.save_customer(diff)
        self.app.push_screen(OrderCustomerModal(c), done)

    @work
    async def move_to(self, cid) -> None:
        if await self.app.call("PATCH", f"orders/{self.oid}", json_body={"customer_id": cid}):  # type: ignore
            self.app.notify(f"Order moved to customer #{cid}", title="✔")
            self.load()

    @work
    async def save_customer(self, diff) -> None:
        if await self.app.call("PATCH", f"orders/{self.oid}", json_body={"customer": diff}):  # type: ignore
            self.app.notify("Customer saved", title="✔")
            self.load()

    @on(Button.Pressed, "#a-photos")
    def action_add_photos(self) -> None:
        free = 8 - len(self.o.get("photos") or [])
        if free <= 0:
            self.app.notify("All 8 slots are full — click a photo to replace or remove it", severity="warning")
            return

        def done(res):
            if res:
                self.upload(*res)
        self.app.push_screen(FilePicker(f"Add photos to #{self.oid}", max_files=free), done)

    @work
    async def upload(self, files: list, urls: list) -> None:
        app: ACP4UApp = self.app  # type: ignore
        ok = False
        if urls:
            ok = bool(await app.call("PATCH", f"orders/{self.oid}", json_body={"photos": urls})) or ok
        if files:
            app.notify(f"Uploading {len(files)} photo(s)…", timeout=3)
            ok = bool(await app.call("POST", f"orders/{self.oid}/photos",
                                     files=[file_tuple("photos[]", f) for f in files])) or ok
        if ok:
            app.notify("Photos added", title="✔")
            self.load()

    @on(Button.Pressed, "#a-price")
    def action_price(self) -> None:
        def done(r):
            if r:
                self.load()
        self.app.push_screen(QuoteModal(self.oid), done)

    @on(Button.Pressed, "#a-download")
    def action_download(self) -> None:
        self.app.save_photos(self.o.get("photos") or [], "photos", self.folder_name())  # type: ignore

    @on(Button.Pressed, "#a-delete")
    def action_delete(self) -> None:
        self.app.delete_order(self.oid, lambda: self.app.pop_screen())  # type: ignore

    def action_json(self) -> None:
        self.app.push_screen(ResultModal(f"{{ }}  order #{self.oid}", RJSON(json.dumps(self.o, ensure_ascii=False))))


class OrderCustomerModal(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, c: dict) -> None:
        super().__init__()
        self.c = c

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("☺  Customer of this order", classes="title")
            yield OptionList(
                Option(Text.assemble(("✎  ", C1), ("Change the details", "bold"),
                                     ("\n    another ID / platform moves the order to that customer", MUTED)), id="edit"),
                Option(Text.assemble(("⌕  ", C3), ("Move to an existing customer", "bold")), id="pick"),
                Option(Text.assemble(("☺  ", OK), ("Open this customer", "bold")), id="open"),
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def sel(self, e: OptionList.OptionSelected) -> None:
        if e.option.id == "edit":
            self.app.push_screen(CustomerForm(self.c, "✎  Customer of this order"), lambda d: self.dismiss(d))
        elif e.option.id == "pick":
            self.dismiss({"__pick": True})
        else:
            if self.c.get("id"):
                self.dismiss(None)
                self.app.push_screen(CustomerScreen(self.c["id"]))
            else:
                self.app.notify("No visible customer", severity="warning")

    def action_cancel(self) -> None:
        self.dismiss(None)


# ═══════════════════════════════ quote modal ═════════════════════════════
class QuoteModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, oid) -> None:
        super().__init__()
        self.oid = oid
        self.q: dict = {}
        self.changed = False

    def compose(self) -> ComposeResult:
        curs = core.CURRENCIES or ["AED", "USD", "EUR", "IRR"]
        with Vertical(classes="dialog wide"):
            yield Static(f"$  My price for #{self.oid}", classes="title")
            yield Static(id="q-total")
            with Grid(classes="fgrid"):
                yield Label("Price")
                yield Input(id="q-price", type="number", placeholder="120")
                yield Label("Shipping")
                yield Input(id="q-shipping", type="number", placeholder="15")
                yield Label("Currency")
                yield Select([(c, c) for c in curs], prompt="currency", id="q-currency")
                yield Label("Stock")
                yield Select([(core.stock_text("in_stock"), "in_stock"), (core.stock_text("on_order"), "on_order"),
                              (core.stock_text("out"), "out")], prompt="not set", id="q-stock")
                yield Label("Qty")
                yield Input(id="q-qty", type="integer")
                yield Label("Delivery")
                yield Input(id="q-delivery", placeholder="3-5 days")
                yield Label("Note")
                yield Input(id="q-note", placeholder="original, new …")
            yield Static("▣  Photos of the offered part (up to 3) — click to view", classes="section")
            yield Grid(id="q-thumbs")
            with Horizontal(classes="btnrow"):
                yield Button("✔ Save", id="q-save", variant="primary")
                yield Button("＋ Photos", id="q-addph")
                yield Button("✖ Delete my price", id="q-del", variant="error")
                yield Button("Close", id="q-close")

    def on_mount(self) -> None:
        self.load()

    def folder_name(self) -> str:
        return f"order-{self.oid}-my-price"

    @work(exclusive=True)
    async def load(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        res = await app.call("GET", f"orders/{self.oid}/quote", quiet=True)
        q = (res or {}).get("data")
        if isinstance(q, dict) and isinstance(q.get("quote"), (dict, type(None))) and "quote" in q:
            q = q["quote"]
        self.q = q or {}
        for k in ("price", "shipping", "qty", "delivery", "note"):
            self.query_one(f"#q-{k}", Input).value = s(self.q.get(k))
        if self.q.get("currency"):
            sel = self.query_one("#q-currency", Select)
            try:
                sel.value = self.q["currency"]
            except Exception:
                pass
        elif core.CURRENCIES:
            self.query_one("#q-currency", Select).value = core.CURRENCIES[0]
        self.query_one("#q-stock", Select).value = self.q.get("stock") or Select.NULL
        self.update_total()
        self.query_one("#q-del", Button).disabled = not self.q
        photos = self.q.get("photos") or []
        grid = self.query_one("#q-thumbs", Grid)
        await grid.remove_children()
        await grid.mount_all([Thumb(p, p.get("slot") or i + 1, route="quote-photos") for i, p in enumerate(photos)])
        self.query_one("#q-price").focus()

    @on(Input.Changed)
    @on(Select.Changed)
    def update_total(self) -> None:
        p = to_num(self.query_one("#q-price", Input).value or "0") or 0
        sh = to_num(self.query_one("#q-shipping", Input).value or "0") or 0
        cur = sel_value(self.query_one("#q-currency", Select))
        t = Text.assemble(("Total  ", MUTED), (money(p + sh, cur), f"bold {OK}"))
        if self.q.get("updated_at"):
            t.append(f"     last saved {ago(self.q.get('updated_at'))}", style=MUTED)
        elif not self.q:
            t.append("     (no price yet)", style=MUTED)
        self.query_one("#q-total", Static).update(t)

    @on(Button.Pressed, "#q-save")
    def action_save(self) -> None:
        body = {}
        for k in ("price", "shipping"):
            v = self.query_one(f"#q-{k}", Input).value.strip()
            n = to_num(v) if v else None
            if v and n is None:
                self.notify(f"{k}: numbers only", severity="error")
                return
            if n != (to_num(s(self.q.get(k))) if self.q.get(k) not in (None, "") else None):
                body[k] = n
        v = self.query_one("#q-qty", Input).value.strip()
        if v != s(self.q.get("qty")):
            body["qty"] = int(to_num(v)) if v and to_num(v) is not None else None
        for k in ("delivery", "note"):
            v = self.query_one(f"#q-{k}", Input).value.strip()
            if v != s(self.q.get(k)):
                body[k] = v
        cur = sel_value(self.query_one("#q-currency", Select))
        if cur and cur != s(self.q.get("currency")):
            body["currency"] = cur
        st = sel_value(self.query_one("#q-stock", Select))
        if st != s(self.q.get("stock")):
            body["stock"] = st
        if not body:
            self.notify("Nothing changed")
            return
        self.save(body)

    @work
    async def save(self, body: dict) -> None:
        if await self.app.call("PUT", f"orders/{self.oid}/quote", json_body=body):  # type: ignore
            self.changed = True
            self.notify("Price saved", title="✔")
            self.load()

    @on(Button.Pressed, "#q-addph")
    def add_photos(self) -> None:
        free = 3 - len(self.q.get("photos") or [])
        if free <= 0:
            self.notify("3 photos already — click one to remove or replace it", severity="warning")
            return

        def done(res):
            if res:
                self.upload(*res)
        self.app.push_screen(FilePicker("Photos of the offered part", max_files=free), done)

    @work
    async def upload(self, files, urls) -> None:
        app: ACP4UApp = self.app  # type: ignore
        ok = False
        if urls:
            ok = bool(await app.call("PUT", f"orders/{self.oid}/quote", json_body={"photos": urls})) or ok
        if files:
            ok = bool(await app.call("POST", f"orders/{self.oid}/quote",
                                     files=[file_tuple("photos[]", f) for f in files])) or ok
        if ok:
            self.changed = True
            self.notify("Photos added", title="✔")
            self.load()

    def thumb_clicked(self, th: Thumb) -> None:
        photos = self.q.get("photos") or []

        def done(ch):
            if ch:
                self.changed = True
                self.load()
        self.app.push_screen(PhotoViewer(photos, photos.index(th.photo), "quote-photos", self, self.folder_name(),
                                         "my price photo"), done)

    async def remove_photo(self, p: dict) -> bool:
        return bool(await self.app.call("PUT", f"orders/{self.oid}/quote",  # type: ignore
                                        json_body={"remove_photos": [p.get("slot")]}))

    async def put_photo(self, slot, files, urls) -> bool:
        app: ACP4UApp = self.app  # type: ignore
        if files:
            return bool(await app.call("POST", f"orders/{self.oid}/quote", files=[file_tuple(f"photo{slot}", files[0])]))
        if urls:
            return bool(await app.call("PUT", f"orders/{self.oid}/quote", json_body={"photos": [{"slot": slot, "url": urls[0]}]}))
        return False

    @on(Button.Pressed, "#q-del")
    def delete(self) -> None:
        def done(ok):
            if ok:
                self.do_delete()
        self.app.push_screen(ConfirmModal("Delete your price for this order?", "Its photos go too.", yes="Delete"), done)

    @work
    async def do_delete(self) -> None:
        if await self.app.call("DELETE", f"orders/{self.oid}/quote"):  # type: ignore
            self.notify("Price deleted", title="✔")
            self.dismiss(True)

    @on(Button.Pressed, "#q-close")
    def action_close(self) -> None:
        self.dismiss(self.changed)


# ═══════════════════════════════ order form ══════════════════════════════
class OrderForm(Screen):
    """New order (o=None) or edit an order."""
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, o: dict | None = None, customer_id=None) -> None:
        super().__init__()
        self.o = o or {}
        self.new = o is None
        self.customer_id = customer_id
        self.files: list[Path] = []
        self.urls: list[str] = []

    def compose(self) -> ComposeResult:
        o = self.o
        yield TopBar("New order" if self.new else f"Orders › #{o.get('id')} › Edit")
        yield Static(Text.assemble(("✚  New order" if self.new else f"✎  Edit order #{o.get('id')}", f"bold {C1}"),
                                   ("     * required · tab next field · ctrl+s save · esc cancel", MUTED)), id="form-head")
        with VerticalScroll(id="form"):
            if self.new:
                yield Static("1 · Customer", classes="section")
                with Grid(classes="fgrid"):
                    yield Label("Customer ID")
                    yield Input(id="f-handle", placeholder="ali.cars")
                    yield Label("Platform")
                    yield Input(id="f-platform", placeholder="Instagram, WhatsApp…",
                                suggester=SuggestFromList(core.PLATFORMS, case_sensitive=False))
                    yield Label("Name")
                    yield Input(id="f-name")
                    yield Label("Phone / WhatsApp")
                    yield Input(id="f-phone")
                    yield Label("Location")
                    yield Input(id="f-location", placeholder="Iran / Tehran")
                    yield Label("…or customer #")
                    with Horizontal():
                        yield Input(s(self.customer_id), id="f-customer_id", type="integer", placeholder="existing id")
                        yield Button("⌕", id="f-findcust", tooltip="Find a customer")
            yield Static(("2 · " if self.new else "") + "Car", classes="section")
            with Grid(classes="fgrid"):
                yield Label("Brand *", classes="req")
                yield Input(s(o.get("brand")), id="f-brand", placeholder="Kia",
                            suggester=SuggestFromList(BRANDS, case_sensitive=False))
                yield Label("Model *", classes="req")
                yield Input(s(o.get("model")), id="f-model", placeholder="Cerato")
                yield Label("Year *", classes="req")
                yield Input(s(o.get("year")), id="f-year", placeholder="2019")
                yield Label("VIN")
                yield Input(s(o.get("vin")), id="f-vin", placeholder="KNAFX4A68E5123456")
                yield Label("Fuel")
                yield Input(s(o.get("fuel")), id="f-fuel", placeholder="Petrol / Diesel / Electric / Hybrid",
                            suggester=SuggestFromList(FUELS, case_sensitive=False))
                yield Label("Vehicle type")
                yield Input(s(o.get("vehicle_type")), id="f-vehicle_type", placeholder="SUV / 4x4",
                            suggester=SuggestFromList(VTYPES, case_sensitive=False))
                yield Label("Origin")
                yield Input(s(o.get("origin")), id="f-origin", placeholder="Japan")
                yield Label("Engine No.")
                yield Input(s(o.get("engine_no")), id="f-engine_no")
                yield Label("G.V.W.")
                yield Input(s(o.get("gvw")), id="f-gvw")
            yield Static(("3 · " if self.new else "") + "Part", classes="section")
            with Grid(classes="fgrid"):
                yield Label("Part *", classes="req")
                yield Input(s(o.get("part")), id="f-part", placeholder="Right headlight")
                yield Label("Part number")
                yield Input(s(o.get("part_number")), id="f-part_number")
                yield Label("Side / position")
                yield Input(s(o.get("side")), id="f-side", placeholder="Front left",
                            suggester=SuggestFromList(SIDES, case_sensitive=False))
                yield Label("Colour")
                yield Input(s(o.get("colour")), id="f-colour")
                yield Label("Additional info")
                yield Input(s(o.get("info")), id="f-info")
                yield Label("Ref")
                yield Input(s(o.get("ref")), id="f-ref", placeholder="your own id, e.g. shop-10025")
            yield Static("Details  ·  one per line —  Label: value", classes="section")
            yield TextArea("\n".join(f"{a}: {b}" for a, b in details_rows(o.get("details"))), id="f-details")
            if self.new:
                yield Static("4 · Photos", classes="section")
                yield Static(id="f-photos")
                with Horizontal(classes="btnrow"):
                    yield Button("＋ Add photos…", id="f-addph", variant="primary")
                    yield Button("⌫ Clear photos", id="f-clearph")
                yield Static("5 · Status", classes="section")
                yield Select([(core.status_label(st), st) for st in core.STATUSES], value="New",
                             allow_blank=False, id="f-status")
                with Collapsible(title="$  My price (optional)", collapsed=True):
                    with Grid(classes="fgrid"):
                        yield Label("Price")
                        yield Input(id="fq-price", type="number")
                        yield Label("Shipping")
                        yield Input(id="fq-shipping", type="number")
                        yield Label("Currency")
                        yield Select([(c, c) for c in (core.CURRENCIES or ["AED", "USD", "EUR", "IRR"])],
                                     prompt="currency", id="fq-currency")
                        yield Label("Stock")
                        yield Select([(core.stock_text("in_stock"), "in_stock"), (core.stock_text("on_order"), "on_order"),
                                      (core.stock_text("out"), "out")], prompt="not set", id="fq-stock")
                        yield Label("Qty")
                        yield Input(id="fq-qty", type="integer")
                        yield Label("Delivery")
                        yield Input(id="fq-delivery", placeholder="3-5 days")
            with Horizontal(classes="btnrow"):
                yield Button("✔ Send order" if self.new else "✔ Save changes", id="f-save", variant="success")
                yield Button("Cancel", id="f-cancel")
        yield Footer()

    def on_mount(self) -> None:
        if self.new:
            self.query_one("#f-handle").focus()
            self.show_photos()
        else:
            self.query_one("#f-brand").focus()

    def show_photos(self) -> None:
        t = Text()
        if not self.files and not self.urls:
            t.append("no photos yet — up to 8: files from this computer or links", style=MUTED)
        for f in self.files:
            t.append(f"✔ {f.name}  ", style=OK)
        for u in self.urls:
            t.append(f"🔗 {short(u, 40)}  ", style=C1)
        self.query_one("#f-photos", Static).update(t)

    @on(Button.Pressed, "#f-addph")
    def add_photos(self) -> None:
        free = 8 - len(self.files) - len(self.urls)
        if free <= 0:
            self.notify("8 photos already", severity="warning")
            return

        def done(res):
            if res:
                self.files += res[0]
                self.urls += res[1]
                self.show_photos()
        self.app.push_screen(FilePicker("Photos for the new order", max_files=free), done)

    @on(Button.Pressed, "#f-clearph")
    def clear_photos(self) -> None:
        self.files, self.urls = [], []
        self.show_photos()

    @on(Button.Pressed, "#f-findcust")
    def find_cust(self) -> None:
        def done(cid):
            if cid:
                self.query_one("#f-customer_id", Input).value = str(cid)
        self.app.push_screen(CustomerPicker(), done)

    def val(self, k: str) -> str:
        return self.query_one(f"#f-{k}", Input).value.strip()

    @on(Button.Pressed, "#f-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#f-save")
    def action_save(self) -> None:
        missing = []
        for k in ("brand", "model", "year", "part"):
            inp = self.query_one(f"#f-{k}", Input)
            inp.set_class(not inp.value.strip(), "-invalid")
            if not inp.value.strip():
                missing.append(k)
        if missing:
            self.notify("Fill in: " + ", ".join(missing), severity="error", title="Required")
            self.query_one(f"#f-{missing[0]}").focus()
            return
        fields = [k for k, _ in core.CAR_FIELDS + core.PART_FIELDS] + ["ref"]
        details = parse_details(self.query_one("#f-details", TextArea).text)
        if self.new:
            body: dict = {k: self.val(k) for k in fields if self.val(k)}
            if body.get("vin"):
                body["vin"] = body["vin"].upper()
            if details:
                body["details"] = details
            cid = self.val("customer_id")
            if cid:
                body["customer_id"] = int(cid)
            else:
                cust = {k: self.val(k) for k in ("handle", "platform", "name", "phone", "location") if self.val(k)}
                if cust:
                    body["customer"] = cust
            st = sel_value(self.query_one("#f-status", Select))
            if st and st != "New":
                body["status"] = st
            if self.urls:
                body["photos"] = self.urls
            q = {}
            for k in ("price", "shipping", "qty"):
                v = self.query_one(f"#fq-{k}", Input).value.strip()
                if v and to_num(v) is not None:
                    q[k] = to_num(v)
            for k in ("currency", "stock"):
                v = sel_value(self.query_one(f"#fq-{k}", Select))
                if v:
                    q[k] = v
            v = self.query_one("#fq-delivery", Input).value.strip()
            if v:
                q["delivery"] = v
            if q.get("price") is not None or q.get("shipping") is not None or q.get("stock"):
                body["quote"] = q
            self.create(body)
        else:
            o = self.o
            changes = {k: self.val(k) for k in fields if self.val(k) != s(o.get(k))}
            if details != dict(details_rows(o.get("details"))):
                changes["details"] = details
            if not changes:
                self.notify("Nothing changed")
                self.dismiss(None)
                return
            self.patch(changes)

    @work(exclusive=True)
    async def create(self, body: dict) -> None:
        app: ACP4UApp = self.app  # type: ignore
        btn = self.query_one("#f-save", Button)
        btn.disabled = True
        btn.label = "sending…"
        res = await app.call("POST", "orders", json_body=body)
        btn.disabled = False
        btn.label = "✔ Send order"
        if not res:
            return
        o = res.get("data") or {}
        oid = o.get("id")
        for w in (res.get("meta") or {}).get("warnings") or []:
            app.notify(w if isinstance(w, str) else json.dumps(w, ensure_ascii=False), severity="warning", timeout=10)
        if self.files and oid:
            app.notify(f"Uploading {len(self.files)} photo(s)…", timeout=3)
            r = await app.call("POST", f"orders/{oid}/photos", files=[file_tuple("photos[]", f) for f in self.files])
            for w in ((r or {}).get("meta") or {}).get("warnings") or []:
                app.notify(w if isinstance(w, str) else json.dumps(w, ensure_ascii=False), severity="warning", timeout=10)
        app.notify(f"{car_line(o)} — {s(o.get('part'))}", title=f"✔ Order #{oid} created", timeout=6)
        self.dismiss(oid)

    @work(exclusive=True)
    async def patch(self, changes: dict) -> None:
        if await self.app.call("PATCH", f"orders/{self.o.get('id')}", json_body=changes):  # type: ignore
            self.app.notify(f"{len(changes)} field(s) saved", title="✔ Saved")
            self.dismiss(True)


# ═════════════════════════════ customer screen ═══════════════════════════
class CustomerScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("n", "new", "New order"), Binding("e", "edit", "Edit"),
                Binding("delete", "delete", "Delete"), Binding("r", "reload", "Refresh")]

    def __init__(self, cid) -> None:
        super().__init__()
        self.cid = str(cid)
        self.c: dict = {}

    def compose(self) -> ComposeResult:
        yield TopBar(f"Customers › #{self.cid}")
        yield Static(id="o-head")
        yield Static(id="cs-info")
        yield Static("▤  Orders  ·  enter / click to open", classes="box-title")
        yield DataTable(id="cs-orders", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="actions"):
            yield Button("✚ New order", id="cs-new", variant="success")
            yield Button("✎ Edit", id="cs-edit")
            yield Button("✖ Delete", id="cs-del", variant="error")
            yield Button("↩ Back", id="cs-back")
        yield Footer()

    def on_mount(self) -> None:
        orders_columns(self.query_one(DataTable))
        self.load()

    def on_screen_resume(self) -> None:
        self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        res = await self.app.call("GET", f"customers/{self.cid}")  # type: ignore
        if not res:
            return
        c = self.c = res.get("data") or {}
        self.query_one("#o-head", Static).update(Text.assemble(
            (f" #{self.cid} ", f"bold #0a0a16 on {C3}"), "  ", (s(c.get("handle")) or s(c.get("name")) or "?", "bold"),
            (f"  {s(c.get('platform'))}", C3)))
        self.query_one("#cs-info", Static).update(panel(kv([(lab, c.get(k)) for k, lab in core.CUST_FIELDS] +
                                                           [("Created", core.when(c.get("created_at")) if c.get("created_at") else None)] +
                                                           extra_rows(c, CUSTOMER_KEYS | {"updated_at"})),
                                                        "Customer", C3))
        t = self.query_one(DataTable)
        t.clear()
        for o in c.get("orders") if isinstance(c.get("orders"), list) else []:
            t.add_row(*order_row(o), key=str(o.get("id")))

    @on(DataTable.RowSelected)
    def open_row(self, e: DataTable.RowSelected) -> None:
        self.app.open_order(e.row_key.value)  # type: ignore

    @on(Button.Pressed, "#cs-back")
    def action_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self.load()

    @on(Button.Pressed, "#cs-new")
    def action_new(self) -> None:
        self.app.new_order(customer_id=self.cid)  # type: ignore

    @on(Button.Pressed, "#cs-edit")
    def action_edit(self) -> None:
        def done(d):
            if d:
                diff = {k: v for k, v in d.items() if v != s(self.c.get(k))}
                if diff:
                    self.save(diff)
        self.app.push_screen(CustomerForm(self.c, f"✎  Customer #{self.cid}"), done)

    @work
    async def save(self, diff) -> None:
        if await self.app.call("PATCH", f"customers/{self.cid}", json_body=diff):  # type: ignore
            self.app.notify("Customer saved", title="✔")
            self.load()

    @on(Button.Pressed, "#cs-del")
    def action_delete(self) -> None:
        def done(ok):
            if ok:
                self.do_delete()
        self.app.push_screen(ConfirmModal(f"Delete customer #{self.cid}?", "Only customers without orders can be deleted.",
                                          yes="Delete"), done)

    @work
    async def do_delete(self) -> None:
        if await self.app.call("DELETE", f"customers/{self.cid}"):  # type: ignore
            self.app.notify("Customer deleted", title="✔")
            self.app.pop_screen()


# ═══════════════════════════════ login / splash ══════════════════════════
class SplashScreen(Screen):
    def compose(self) -> ComposeResult:
        with Vertical(id="splash"):
            with Vertical(id="splash-box"):
                yield Static(big_logo(), id="splash-logo")
                yield Static(Text("connecting…", style=MUTED), id="splash-msg")


class LoginScreen(Screen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, error: str = "", modal: bool = False) -> None:
        super().__init__()
        self.err, self.modal = error, modal

    def compose(self) -> ComposeResult:
        app: ACP4UApp = self.app  # type: ignore
        with Vertical(id="login"):
            with Vertical(id="login-card"):
                yield Static(big_logo(), id="login-logo")
                yield Static(Text.assemble(
                    ("Paste your API key once", f"bold {C1}"), (" — it is checked and saved to\n", "bold"),
                    (str(core.CONF_FILE), C3), (" (only your user can read it).\n", MUTED),
                    ("The owner makes keys on the site under ", MUTED), ("API", "bold"),
                    (". Keys start with ", MUTED), ("acp4u_", f"bold {C2}")), id="login-msg")
                yield Input(placeholder="acp4u_…", password=True, id="l-key")
                with Collapsible(title="Server", collapsed=True):
                    yield Input(app.conf.get("base") or core.DEFAULT_BASE, id="l-base")
                with Collapsible(title="⛨ Proxy  (SOCKS5 / SOCKS4 / HTTP)", collapsed=True, id="l-proxybox"):
                    yield ProxyForm(core.effective_proxy(app.conf),
                                    lambda: self.query_one("#l-base", Input).value.strip() or core.DEFAULT_BASE,
                                    id="l-proxy")
                yield Static(self.err, id="login-err")
                with Horizontal(classes="btnrow"):
                    yield Button("Connect ⏎", id="l-go", variant="primary")
                    yield Button("👁 Show", id="l-show")
                    if self.modal:
                        yield Button("Cancel", id="l-cancel")

    def on_mount(self) -> None:
        self.query_one("#l-key").focus()

    @on(Button.Pressed, "#l-show")
    def show(self) -> None:
        i = self.query_one("#l-key", Input)
        i.password = not i.password

    @on(Button.Pressed, "#l-cancel")
    def action_cancel(self) -> None:
        if self.modal:
            self.dismiss(False)

    @on(Input.Submitted, "#l-key")
    @on(Input.Submitted, "#l-base")
    @on(Button.Pressed, "#l-go")
    def go(self) -> None:
        self.connect()

    @work(exclusive=True)
    async def connect(self) -> None:
        app: ACP4UApp = self.app  # type: ignore
        key = self.query_one("#l-key", Input).value.strip().strip("'\"")
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        base = self.query_one("#l-base", Input).value.strip() or core.DEFAULT_BASE
        err = self.query_one("#login-err", Static)
        if not key:
            err.update("Paste the key first.")
            return
        proxy = self.query_one("#l-proxy", ProxyForm).checked()
        if proxy is None:
            self.query_one("#l-proxybox", Collapsible).collapsed = False
            err.update("Fix the proxy settings first.")
            return
        btn = self.query_one("#l-go", Button)
        btn.disabled, btn.label = True, "checking…"
        api = API(base, key, proxy)
        try:
            info = await asyncio.to_thread(api.get, "info")
        except APIError as e:
            if e.code in ("network", "proxy"):
                self.query_one("#l-proxybox", Collapsible).collapsed = False
            if e.code == "network" and base.startswith("https://") and proxy.get("mode") != "custom":
                base = "http://" + base[8:]
                api = API(base, key, proxy)
                try:
                    info = await asyncio.to_thread(api.get, "info")
                    app.notify("https did not answer — using http (the key travels unencrypted)", severity="warning")
                except APIError as e2:
                    e = e2
                    info = None
            else:
                info = None
            if info is None:
                btn.disabled, btn.label = False, "Connect ⏎"
                err.update(Text.assemble(("✖ ", f"bold {BAD}"), (e.payload.get("message") or str(e), BAD),
                                         (f"\n{e.payload['detail']}" if e.payload.get("detail") else "", C3),
                                         ("\n" + core.HINTS.get(e.code, ""), MUTED)))
                return
        app.conf.update({"key": key, "base": base, "saved_at": dt.datetime.now().isoformat(timespec="seconds")})
        if core.PROXY_OVERRIDE is None or proxy != core.PROXY_OVERRIDE:
            app.conf["proxy"] = proxy
        core.save_conf(app.conf)
        app.api = API(base, key, proxy)
        app.online = True
        app.set_info(info.get("data") or {})
        app.notify(f"Connected as {acct_name(app.info)} — you won't be asked again.", title="✔ Key saved")
        if self.modal:
            self.dismiss(True)
        else:
            app.switch_screen(MainScreen(app.start))


# ═════════════════════════════════ the app ═══════════════════════════════
class ACP4UApp(App):
    TITLE = "ACP4U Parts"
    CSS = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True), Binding("f1", "help", "Keys", show=False),
                Binding("f2", "hd", "Sharp photos", priority=True)]

    def __init__(self, conf: dict, start: tuple | None = None) -> None:
        super().__init__()
        self.conf = conf
        self.start = start
        self.api: API | None = API(conf["base"], conf["key"], core.effective_proxy(conf)) if conf.get("key") else None
        self.info: dict = {}
        self.counts: dict = {}
        self.online = False
        self.watching = False
        self.img_cache: dict = {}

    @property
    def per_page(self) -> int:
        return int(self.conf.get("per_page", 25))

    @property
    def dl_dir(self) -> Path:
        return Path(self.conf.get("download_dir") or core.DEFAULT_DL).expanduser()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = self.conf.get("theme") or "acp4u"
        if self.theme not in self.available_themes:
            self.theme = "acp4u"
        if self.api:
            self.push_screen(SplashScreen())
            self.boot()
        else:
            self.api = API(self.conf.get("base") or core.DEFAULT_BASE, "", core.effective_proxy(self.conf))
            self.push_screen(LoginScreen())

    @work(exclusive=True)
    async def boot(self) -> None:
        try:
            info = await asyncio.to_thread(self.api.get, "info")
            self.online = True
            self.set_info(info.get("data") or {})
            self.switch_screen(MainScreen(self.start))
            self.hd_tip()
        except APIError as e:
            if e.code in ("bad_key", "no_key"):
                self.switch_screen(LoginScreen(error=f"✖ {e.payload.get('message') or e}"))
            elif e.code in ("network", "proxy"):
                self.error(e)
                self.notify("Check the proxy here (Settings › Proxy), then press Test.", title="⛨ Can't reach the server",
                            severity="warning", timeout=12)
                self.switch_screen(MainScreen(("settings",)))
            else:
                self.error(e)
                self.switch_screen(MainScreen(self.start))

    def hd_tip(self) -> None:
        """Once: tell the user that photos can be sharp in another terminal."""
        if SHARP_CLS is not None or self.conf.get("hd_tip_shown") or not hd_command():
            return
        self.notify("This terminal can only draw photos with blocks.\nPress F2 (or run  acp4u hd) to open a window "
                    "with real-pixel photos.", title="🖼  Tip: sharp photos", timeout=15)
        self.conf["hd_tip_shown"] = True
        core.save_conf(self.conf)

    def set_info(self, info: dict) -> None:
        self.info = info
        st = names(info.get("statuses"))
        if st:
            core.STATUSES[:] = st
        core.CURRENCIES[:] = names(info.get("currencies"))

    # ── API helpers ──
    async def call(self, method: str, route: str, *, quiet: bool = False, **kw):
        try:
            res = await asyncio.to_thread(self.api.call, method, route, **kw)
            self.online = True
            return res
        except APIError as e:
            if e.code == "network":
                self.online = False
            if not quiet:
                self.error(e)
            return None

    async def image(self, route: str, pid, thumb: bool = False, quiet: bool = False) -> bytes | None:
        key = (route, pid, thumb)
        if key in self.img_cache:
            return self.img_cache[key]
        try:
            params = {"size": "thumb"} if thumb else {}
            data, _ = await asyncio.to_thread(self.api.download, f"{route}/{pid}", **params)
        except APIError as e:
            if not quiet:
                self.error(e)
            return None
        if len(self.img_cache) > 120:
            for k in list(self.img_cache)[:40]:
                self.img_cache.pop(k, None)
        self.img_cache[key] = data
        return data

    def error(self, e: APIError) -> None:
        p = e.payload
        lines = [p.get("message") or str(e)]
        if isinstance(p.get("fields"), dict):
            lines += [f"• {k}: {v}" for k, v in p["fields"].items()]
        if p.get("needs"):
            lines.append(f"Missing permission: “{p['needs']}”")
        if p.get("id"):
            lines.append(f"Existing record: #{p['id']}")
        if p.get("detail"):
            lines.append(f"Reason: {p['detail']}")
        if e.code in core.HINTS:
            lines.append(core.HINTS[e.code])
        self.notify("\n".join(lines), title=f"✖ {e.code}" + (f" · HTTP {e.status}" if e.status else ""),
                    severity="error", timeout=10, markup=False)

    # ── shared actions ──
    def open_order(self, oid) -> None:
        self.push_screen(OrderScreen(oid))

    def new_order(self, customer_id=None) -> None:
        def done(oid):
            if oid and oid is not True:
                self.open_order(oid)
        self.push_screen(OrderForm(None, customer_id=customer_id), done)

    def change_status(self, oid, current, after=None) -> None:
        def done(st):
            if st and st != current:
                self._set_status(oid, st, after)
        self.push_screen(StatusModal(current, f"◆  Status of #{oid}"), done)

    @work
    async def _set_status(self, oid, st, after) -> None:
        if await self.call("POST", f"orders/{oid}/status", json_body={"status": st}):
            self.notify(f"#{oid} → {st}", title="✔ Status")
            if after:
                after()

    def delete_order(self, oid, after=None) -> None:
        def done(ok):
            if ok:
                self._delete(oid, after)
        self.push_screen(ConfirmModal(f"✖  Delete order #{oid}?", "All its photos and prices go too. This cannot be undone.",
                                      yes=f"Delete #{oid}"), done)

    @work
    async def _delete(self, oid, after) -> None:
        if await self.call("DELETE", f"orders/{oid}"):
            self.notify(f"Order #{oid} deleted", title="✔")
            if after:
                after()

    def save_photos(self, photos: list, route: str, folder: str) -> None:
        if not photos:
            self.notify("No photos", severity="warning")
            return
        self._save_photos(photos, route, folder)

    @work
    async def _save_photos(self, photos, route, folder) -> None:
        out = self.dl_dir / folder
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in photos:
            try:
                data, ctype = await asyncio.to_thread(self.api.download, f"{route}/{p['id']}")
            except APIError as e:
                self.error(e)
                continue
            ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".jpg"
            ext = ".jpg" if ext in (".jpe", ".jfif") else ext
            (out / f"slot{p.get('slot', 0)}-{p['id']}{ext}").write_bytes(data)
            n += 1
        if n:
            self.notify(f"{n} photo(s) → {out}", title="⇩ Saved", timeout=8)
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def import_excel(self, path: str | None = None) -> None:
        if path:
            self._import(Path(path).expanduser())
            return

        def done(res):
            if res and res[0]:
                self._import(res[0][0])
        self.push_screen(FilePicker("⇪  Import an Excel file of the ACP4U Parts app", exts={".xlsx", ".xls"},
                                    max_files=1, allow_links=False), done)

    @work
    async def _import(self, f: Path) -> None:
        if not f.is_file():
            self.notify(f"File not found: {f}", severity="error")
            return
        self.notify(f"Importing {f.name}…", timeout=4)
        mime = mimetypes.guess_type(f.name)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        res = await self.call("POST", "import", files=[("file", (f.name, f.read_bytes(), mime))], timeout=600)
        if res:
            d = res.get("data")
            body = kv([(k.replace("_", " ").capitalize(), v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False))
                       for k, v in d.items()]) if isinstance(d, dict) else RJSON(json.dumps(d, ensure_ascii=False))
            self.push_screen(ResultModal(f"✔  Imported {f.name}", body))

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "hd":
            return SHARP_CLS is None      # already sharp → no need (hidden from the footer)
        return True

    def action_hd(self) -> None:
        """Open what is on screen in a terminal that draws real pixels."""
        args: list[str] = []
        for scr in reversed(self.screen_stack):
            if isinstance(scr, OrderScreen):
                args = ["order", scr.oid]
                break
            if isinstance(scr, CustomerScreen):
                args = []
                break
        name = launch_hd(args)
        if name:
            self.notify(f"Opening a {name} window with real-pixel photos" + (f" — order #{args[1]}" if args else "") +
                        ".\nYou can close this one.", title="🖼  Sharp photos", timeout=8)
        else:
            self.notify("No terminal with real-pixel images was found.\nInstall one:  sudo apt install kitty   "
                        "(or konsole / wezterm / foot)", title="🖼  Sharp photos", severity="warning", timeout=12)

    def action_goto(self) -> None:
        def done(v):
            if v:
                self._goto(v)
        self.push_screen(TextModal("⌕  Go to order", "#152, your ref, VIN, or any text",
                                   hint="A number opens the order · a ref opens its order · anything else searches"), done)

    @work
    async def _goto(self, v: str) -> None:
        m = re.fullmatch(r"#?\s*(\d+)", v)
        if m and await self.call("GET", f"orders/{m.group(1)}", quiet=True):
            self.open_order(m.group(1))
            return
        r = await self.call("GET", "orders", params={"ref": v, "per_page": 2}, quiet=True)
        rows = (r or {}).get("data") or []
        if len(rows) == 1:
            self.open_order(rows[0]["id"])
            return
        while not isinstance(self.screen, MainScreen) and len(self.screen_stack) > 1:
            self.pop_screen()
        if isinstance(self.screen, MainScreen):
            self.screen.open_orders({"q": v}, f"“{v}”")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        def nav(k):
            def f():
                while not isinstance(self.screen, MainScreen) and len(self.screen_stack) > 1:
                    self.pop_screen()
                if isinstance(self.screen, MainScreen):
                    self.screen.action_nav(k)
            return f

        def status(st):
            def f():
                while not isinstance(self.screen, MainScreen) and len(self.screen_stack) > 1:
                    self.pop_screen()
                if isinstance(self.screen, MainScreen):
                    self.screen.open_orders({"status": st}, st)
            return f
        yield SystemCommand("New order", "Add an order — customer, car, part, photos", self.new_order)
        yield SystemCommand("Go to order", "Open by #id, ref, or search", self.action_goto)
        for k, lab in NAV:
            yield SystemCommand(lab[3:], f"Open {lab[3:]}", nav(k))
        for st in core.STATUSES:
            yield SystemCommand(f"Orders: {st}", f"List orders with status {st}", status(st))
        yield SystemCommand("Proxy settings", "SOCKS5 / SOCKS4 / HTTP proxy, test, exit IP", nav("settings"))
        yield SystemCommand("Keys", "Keyboard shortcuts", self.action_help)
        yield from super().get_system_commands(screen)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="acp4u", description="ACP4U Parts — full-screen terminal app")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("login", help="set / replace the API key")
    sub.add_parser("logout", help="forget the saved key")
    p = sub.add_parser("order", help="open one order")
    p.add_argument("id")
    p = sub.add_parser("orders", help="orders list")
    p.add_argument("-s", "--status")
    p.add_argument("-q", "--query")
    p.add_argument("--mine", choices=["waiting", "priced"])
    sub.add_parser("new", help="add an order")
    sub.add_parser("customers")
    sub.add_parser("watch", help="live watch")
    sub.add_parser("info")
    p = sub.add_parser("import", help="import an Excel file")
    p.add_argument("file", nargs="?")
    sub.add_parser("classic", help="the old line-by-line menu app")
    sub.add_parser("version")
    sub.add_parser("proxy", help="open the proxy settings")
    sub.add_parser("update", help="update to the latest version from GitHub")
    sub.add_parser("uninstall", help="remove the app (your saved key stays in ~/.config/acp4u)")
    sub.add_parser("hd", help="open in a terminal with real-pixel photos (Konsole / Kitty / WezTerm / foot)")
    ap.add_argument("--proxy", metavar="URL", help="proxy for this run, e.g. socks5h://user:pass@127.0.0.1:1080")
    ap.add_argument("--no-proxy", action="store_true", help="connect directly for this run")
    purl, pnone, argv = core.pop_proxy_args(sys.argv[1:])
    if argv[:1] in (["update"], ["uninstall"]):
        url = f"https://raw.githubusercontent.com/{INSTALL_REPO}/main/install.sh"
        flag = " -s -- --uninstall" if argv[0] == "uninstall" else ""
        sys.exit(subprocess.call(["bash", "-c", f'curl -fsSL "{url}" | bash{flag}']))
    if argv[:1] == ["hd"]:
        if not core.apply_proxy_override(purl, pnone):
            return
        name = launch_hd(argv[1:])
        print(f"Opening ACP4U in {name} (real-pixel photos)…" if name else
              "No terminal with real-pixel images found — install one:  sudo apt install kitty")
        return
    args, rest = ap.parse_known_args(argv)

    if args.cmd == "version":
        print(f"acp4u {VERSION}")
        return
    if args.cmd == "classic":
        sys.argv = [sys.argv[0]] + rest + (["--proxy", purl] if purl else []) + (["--no-proxy"] if pnone else [])
        core.main()
        return
    if not core.apply_proxy_override(purl, pnone):
        return
    conf = core.load_conf()
    if os.environ.get("ACP4U_KEY"):
        conf["key"] = os.environ["ACP4U_KEY"]
    if os.environ.get("ACP4U_BASE"):
        conf["base"] = os.environ["ACP4U_BASE"]
    conf.setdefault("base", core.DEFAULT_BASE)
    if args.cmd == "logout":
        conf.pop("key", None)
        core.save_conf(conf)
        print("Key forgotten.")
        return
    if args.cmd == "login":
        conf.pop("key", None)
    start = None
    if args.cmd == "order":
        start = ("order", args.id.lstrip("#"))
    elif args.cmd == "orders":
        start = ("orders", {k: v for k, v in (("status", args.status), ("q", args.query), ("mine", args.mine)) if v})
    elif args.cmd in ("new", "customers", "watch", "info"):
        start = (args.cmd,)
    elif args.cmd == "proxy":
        start = ("settings",)
    elif args.cmd == "import":
        start = ("import", args.file) if args.file else ("import",)
    setup_images(conf.get("images"))
    app = ACP4UApp(conf, start)
    app.run()


if __name__ == "__main__":
    main()
