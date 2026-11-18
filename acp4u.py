#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACP4U Parts — a colourful terminal client for the ACP4U Parts API.

  acp4u                 interactive app (menus with arrow keys)
  acp4u login           set / replace the API key (asked once, then saved)
  acp4u logout          forget the saved key
  acp4u info            key, permissions, statuses, currencies, limits
  acp4u orders [-s STATUS] [-q TEXT] [--mine waiting|priced]
  acp4u order ID        open one order
  acp4u new             add an order (wizard)
  acp4u customers [-q TEXT]
  acp4u watch [-i SECONDS]
  acp4u import FILE.xlsx
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import mimetypes
import os
import re
import select as _select
import shlex
import shutil
import subprocess
import sys
import termios
import time
import tty
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import requests
    from rich import box
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.json import JSON as RJSON
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError as _e:  # pragma: no cover
    print(f"Missing library: {_e.name}.  Install with:  pip install rich requests pillow")
    sys.exit(1)

VERSION = "1.0.0"
CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "acp4u"
CONF_FILE = CONF_DIR / "config.json"
DEFAULT_BASE = "https://orders.acp4u.com/api.php"
DEFAULT_DL = str(Path.home() / "Downloads" / "acp4u")

console = Console(highlight=False)
INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()

# ─────────────────────────────── theme ────────────────────────────────
C1 = "#00d7ff"      # cyan
C2 = "#ff5fd7"      # pink
C3 = "#ffd75f"      # gold
OK = "#5fff87"
BAD = "#ff5f5f"
MUTED = "#6c6c80"
LINE = "#3a3a5a"

STATUSES = ["New", "In process", "Waiting for customer confirmation", "Waiting for payment",
            "Waiting for shipping", "Shipped", "Done", "Cancelled"]
STATUS_COLOR = {
    "New": "#00d7ff", "In process": "#ffd700", "Waiting for customer confirmation": "#d787ff",
    "Waiting for payment": "#ff8700", "Waiting for shipping": "#5f87ff", "Shipped": "#87d75f",
    "Done": "#00d787", "Cancelled": "#ff5f5f",
}
STATUS_ICON = {
    "New": "✦", "In process": "⚙", "Waiting for customer confirmation": "?", "Waiting for payment": "$",
    "Waiting for shipping": "⇢", "Shipped": "➜", "Done": "✔", "Cancelled": "✖",
}
STATUS_SHORT = {
    "New": "New", "In process": "In process", "Waiting for customer confirmation": "Confirm",
    "Waiting for payment": "Payment", "Waiting for shipping": "To ship", "Shipped": "Shipped",
    "Done": "Done", "Cancelled": "Cancelled",
}
CURRENCIES = []  # filled from `info`

CAR_FIELDS = [("brand", "Brand"), ("model", "Model"), ("year", "Year"), ("vin", "VIN"),
              ("vehicle_type", "Vehicle type"), ("fuel", "Fuel"), ("origin", "Origin"),
              ("engine_no", "Engine No."), ("gvw", "G.V.W.")]
PART_FIELDS = [("part", "Part"), ("part_number", "Part number"), ("side", "Side / position"),
               ("colour", "Colour"), ("info", "Additional info")]
CUST_FIELDS = [("handle", "Customer ID"), ("name", "Name"), ("platform", "Platform"),
               ("phone", "Phone"), ("location", "Location"), ("notes", "Notes")]
PLATFORMS = ["Instagram", "WhatsApp", "Telegram", "TikTok", "Facebook"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


# ─────────────────────────────── config ───────────────────────────────
def load_conf() -> dict:
    try:
        return json.loads(CONF_FILE.read_text())
    except Exception:
        return {}


def save_conf(conf: dict) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONF_DIR, 0o700)
    tmp = CONF_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONF_FILE)
    os.chmod(CONF_FILE, 0o600)


def mask(key: str) -> str:
    return f"{key[:10]}…{key[-4:]}" if len(key) > 16 else "•" * len(key)


# ─────────────────────────────── proxy ────────────────────────────────
# conf["proxy"] = {"mode": "system" | "none" | "custom", "type": "socks5h", "host": "", "port": 1080,
#                  "user": "", "password": ""}
PROXY_TYPES = [
    ("socks5h", "SOCKS5 ★ remote DNS"),
    ("socks5", "SOCKS5 · local DNS"),
    ("socks4a", "SOCKS4a"),
    ("socks4", "SOCKS4"),
    ("http", "HTTP"),
    ("https", "HTTPS"),
]
PROXY_PRESETS = [
    ("Tor", "socks5h", "127.0.0.1", 9050),
    ("Tor Browser", "socks5h", "127.0.0.1", 9150),
    ("v2rayN / Xray", "socks5h", "127.0.0.1", 10808),
    ("Clash / mixed", "socks5h", "127.0.0.1", 7890),
    ("SSH -D / Shadowsocks", "socks5h", "127.0.0.1", 1080),
]
DEFAULT_PORTS = {"socks5h": 1080, "socks5": 1080, "socks4a": 1080, "socks4": 1080, "http": 8080, "https": 443}
PROXY_OVERRIDE: dict | None = None   # set by --proxy / ACP4U_PROXY for this run only (never saved)


def socks_available() -> bool:
    try:
        import socks  # noqa: F401
        import urllib3.contrib.socks  # noqa: F401
        return True
    except ImportError:
        return False


def parse_proxy_url(url: str) -> dict:
    """'socks5h://user:pass@host:1080' / 'host:port' / 'none' / 'system' → proxy dict. Raises ValueError."""
    from urllib.parse import unquote, urlsplit
    url = url.strip()
    if url.lower() in ("none", "off", "direct", "no"):
        return {"mode": "none"}
    if url.lower() in ("system", "env", "auto"):
        return {"mode": "system"}
    if "://" not in url:
        url = "socks5h://" + url
    u = urlsplit(url)
    scheme = u.scheme.lower()
    if scheme == "socks":
        scheme = "socks5h"
    if scheme not in DEFAULT_PORTS:
        raise ValueError(f"Unknown proxy type “{u.scheme}” — use socks5h, socks5, socks4a, socks4, http or https.")
    if not u.hostname:
        raise ValueError("The proxy address has no host.")
    try:
        port = u.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise ValueError("The proxy port must be a number 1-65535.")
    return {"mode": "custom", "type": scheme, "host": u.hostname, "port": port,
            "user": unquote(u.username or ""), "password": unquote(u.password or "")}


def proxy_url(p: dict | None, with_password: bool = True) -> str | None:
    if not p or p.get("mode") != "custom" or not p.get("host"):
        return None
    from urllib.parse import quote
    scheme = p.get("type") or "socks5h"
    host = p["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"   # IPv6
    auth = ""
    if p.get("user"):
        auth = quote(p["user"], safe="")
        if p.get("password"):
            auth += ":" + (quote(p["password"], safe="") if with_password else "••••")
        auth += "@"
    return f"{scheme}://{auth}{host}:{p.get('port') or DEFAULT_PORTS.get(scheme, 1080)}"


def env_proxy() -> str:
    for k in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(k):
            return os.environ[k]
    return ""


def effective_proxy(conf: dict) -> dict:
    return PROXY_OVERRIDE if PROXY_OVERRIDE is not None else (conf.get("proxy") or {"mode": "system"})


def proxy_label(p: dict | None) -> str:
    p = p or {"mode": "system"}
    if p.get("mode") == "custom":
        return proxy_url(p, with_password=False) or "custom (not set)"
    if p.get("mode") == "none":
        return "direct (no proxy)"
    e = env_proxy()
    if e:
        return "system: " + re.sub(r"//([^:@/]+):[^@/]*@", r"//\1:••••@", e)
    return "system (none set)"


def _why(e: Exception) -> str:
    """Short, human reason out of a requests / urllib3 / PySocks error."""
    txt = str(e)
    m = re.search(r"Caused by \w+\((.*)\)\)?$", txt)
    if m:
        txt = m.group(1)
    for pat in (r"Failed to establish a new connection: (.*)", r"(0x0\d: .*)", r"SOCKS\w*Error: (.*)",
                r"ProxyError\('(.*)'", r"Cannot connect to proxy\.?,? ?(.*)"):
        m = re.search(pat, txt)
        if m and m.group(1).strip(" '\")"):
            txt = m.group(1)
            break
    txt = txt.strip(" '\")(,")
    txt = re.sub(r"<[^>]+object at 0x[0-9a-f]+>:?\s*", "", txt)
    return (txt[:180] + "…") if len(txt) > 180 else txt


def net_error(e: Exception, proxy: dict | None, what: str = "reach the server") -> "APIError":
    txt = str(e)
    using = proxy and proxy.get("mode") == "custom"
    if isinstance(e, requests.exceptions.InvalidSchema) and "SOCKS" in txt:
        return APIError(0, {"error": "proxy", "message": "SOCKS support is missing.  Install it:  pip install pysocks  "
                                                         "(or  sudo apt install python3-socks)"})
    if isinstance(e, requests.exceptions.ProxyError) or (using and isinstance(e, requests.exceptions.ConnectionError)) \
            or "SOCKS" in txt or "proxy" in txt.lower():
        where = proxy_label(proxy) if proxy else "the proxy"
        reason = _why(e)
        low = reason.lower()
        if "auth" in low or "username" in low or "password" in low or "0x01: request rejected" in low:
            hint = "The proxy refused the username / password."
        elif "0x05" in low:
            hint = "The proxy is fine, but the server refused it (0x05)."
        elif "0x04" in low or "0x03" in low or "unreachable" in low:
            hint = "The proxy could not reach the server."
        elif "0x02" in low:
            hint = "The proxy's rules do not allow this connection."
        elif "0x01" in low:
            hint = "The proxy reported a general failure."
        elif "refused" in low or "errno 111" in low:
            hint = "Nothing answers on that host/port — is the proxy app running?"
        elif "timed out" in low or "timeout" in low:
            hint = "The proxy did not answer in time."
        elif "name or service" in low or "nodename" in low or "resolve" in low:
            hint = "The proxy host name could not be found."
        else:
            hint = ""
        return APIError(0, {"error": "proxy", "message": f"Could not {what} through the proxy {where}.  {hint}",
                            "detail": reason})
    if isinstance(e, requests.exceptions.SSLError):
        return APIError(0, {"error": "network", "message": f"TLS / certificate problem: {_why(e)}"})
    if isinstance(e, requests.exceptions.Timeout):
        return APIError(0, {"error": "network", "message": "The server did not answer in time."})
    return APIError(0, {"error": "network", "message": f"Could not {what} ({e.__class__.__name__}: {_why(e)})."})


def make_session(proxy: dict | None, headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    if headers:
        s.headers.update(headers)
    p = proxy or {"mode": "system"}
    if p.get("mode") == "system":
        s.trust_env = True          # ALL_PROXY / HTTPS_PROXY / NO_PROXY from the environment
    else:
        s.trust_env = False         # ignore environment proxies
        url = proxy_url(p)
        if url:
            s.proxies = {"http": url, "https": url}
    return s


def test_connection(base: str, proxy: dict | None, timeout: int = 20) -> tuple[bool, float, str]:
    """Reach the API through `proxy`. Any JSON answer (even 401) means the way is open."""
    s = make_session(proxy, {"User-Agent": f"acp4u-cli/{VERSION}"})
    t0 = time.time()
    try:
        r = s.get(f"{base.rstrip('/')}?r=info", timeout=timeout)
    except requests.exceptions.RequestException as e:
        err = net_error(e, proxy).payload
        return False, 0.0, err["message"] + (f"  [{err['detail']}]" if err.get("detail") else "")
    ms = (time.time() - t0) * 1000
    try:
        r.json()
        return True, ms, f"Server reached in {ms:.0f} ms (HTTP {r.status_code})."
    except ValueError:
        return False, ms, f"Something answered (HTTP {r.status_code}) but it is not the ACP4U API."


def exit_ip(proxy: dict | None, timeout: int = 20) -> str:
    """Public IP seen by the outside world (asks api.ipify.org through the proxy)."""
    s = make_session(proxy)
    try:
        return s.get("https://api.ipify.org?format=json", timeout=timeout).json().get("ip", "?")
    except requests.exceptions.RequestException as e:
        raise net_error(e, proxy, "reach api.ipify.org")
    except ValueError:
        return "?"


# ──────────────────────────────── API ─────────────────────────────────
class APIError(Exception):
    def __init__(self, status: int, payload: dict | None):
        self.status = status
        self.payload = payload or {}
        super().__init__(self.payload.get("message") or f"HTTP {status}")

    @property
    def code(self) -> str:
        return self.payload.get("error") or "error"


class API:
    def __init__(self, base: str, key: str, proxy: dict | None = None):
        self.base = base.rstrip("/")
        self.key = key
        self.proxy = proxy or {"mode": "system"}
        self.headers = {"X-API-Key": key, "Accept": "application/json",
                        "User-Agent": f"acp4u-cli/{VERSION}"}
        self.s = make_session(self.proxy, self.headers)

    def _url(self, route: str) -> str:
        return f"{self.base}?r={route}"

    def call(self, method: str, route: str, params: dict | None = None, json_body=None,
             data=None, files=None, timeout: int = 120, _override: bool = False) -> dict:
        m = method.upper()
        q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        headers = {}
        send = m
        if _override:
            headers["X-HTTP-Method-Override"] = m
            send = "POST"
        try:
            r = self.s.request(send, self._url(route), params=q, json=json_body, data=data,
                               files=files, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise net_error(e, self.proxy)
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            # A firewall that blocks PATCH/PUT/DELETE answers with HTML — retry as POST + override.
            if m in ("PATCH", "PUT", "DELETE") and not _override and files is None:
                return self.call(method, route, params, json_body, data, files, timeout, _override=True)
            raise APIError(r.status_code, {"error": "bad_response",
                                           "message": f"Unexpected answer (HTTP {r.status_code}): "
                                                      f"{r.text[:160].strip()}"})
        if not payload.get("ok"):
            raise APIError(r.status_code, payload)
        return payload

    def get(self, route: str, **params) -> dict:
        return self.call("GET", route, params=params)

    def total(self, **params) -> int | None:
        """Count of orders for a filter (thread-safe: own session, same proxy)."""
        params.update({"per_page": 1})
        try:
            with make_session(self.proxy, self.headers) as s:
                p = s.get(self._url("orders"), params=params, timeout=30).json()
            return int(p.get("meta", {}).get("total", 0)) if p.get("ok") else None
        except Exception:
            return None

    def download(self, route: str, **params) -> tuple[bytes, str]:
        try:
            r = self.s.get(self._url(route), params=params, timeout=180)
        except requests.exceptions.RequestException as e:
            raise net_error(e, self.proxy, "download")
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "json" in ctype:
            try:
                p = r.json()
            except ValueError:
                p = {"error": "bad_response", "message": f"HTTP {r.status_code}"}
            raise APIError(r.status_code, p)
        return r.content, ctype


class Ctx:
    def __init__(self, conf: dict):
        self.conf = conf
        self.api = API(conf["base"], conf["key"], effective_proxy(conf))
        self.info: dict = {}
        self.dash: dict | None = None
        self.dash_time = 0.0

    @property
    def per_page(self) -> int:
        return int(self.conf.get("per_page", 25))

    @property
    def dl_dir(self) -> Path:
        return Path(self.conf.get("download_dir") or DEFAULT_DL).expanduser()


# ───────────────────────────── terminal I/O ───────────────────────────
def getkey() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            r, _, _ = _select.select([fd], [], [], 0.06)
            if not r:
                return "esc"
            seq = os.read(fd, 16)
            for code, name in ((b"[A", "up"), (b"OA", "up"), (b"[B", "down"), (b"OB", "down"),
                               (b"[C", "right"), (b"OC", "right"), (b"[D", "left"), (b"OD", "left"),
                               (b"[H", "home"), (b"[1~", "home"), (b"[F", "end"), (b"[4~", "end"),
                               (b"[5~", "pgup"), (b"[6~", "pgdn")):
                if seq.startswith(code):
                    return name
            return "esc"
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x03":
            raise KeyboardInterrupt
        if ch == b"\x04":
            raise EOFError
        if ch in (b"\x7f", b"\x08"):
            return "back"
        return ch.decode("utf-8", "ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def choose(title: str | None, items: list, default: int = 0):
    """Arrow-key menu. items = [(value, label)]. Returns value, or None on Esc / q."""
    items = [(v, l if isinstance(l, Text) else Text(str(l))) for v, l in items]
    n = len(items)
    if not n:
        return None
    if not INTERACTIVE:
        if title:
            console.print(Text.assemble(("? ", f"bold {C2}"), (title, "bold")))
        for i, (_, l) in enumerate(items, 1):
            console.print(Text(f"  {i:>2}. ", style=C1) + l)
        while True:
            s = input("  number (empty = back): ").strip()
            if not s or s.lower() in ("q", "b"):
                return None
            if s.isdigit() and 1 <= int(s) <= n:
                return items[int(s) - 1][0]

    idx, top, drawn = max(0, min(default, n - 1)), 0, 0
    out = sys.stdout
    out.write("\x1b[?25l")
    out.flush()

    def erase():
        if drawn:
            out.write(f"\x1b[{drawn}F")
        out.write("\x1b[J")
        out.flush()

    try:
        while True:
            win = max(3, min(n, console.size.height - 4))
            if idx < top:
                top = idx
            elif idx >= top + win:
                top = idx - win + 1
            lines = []
            if title:
                lines.append(Text.assemble(("? ", f"bold {C2}"), (title, "bold"),
                                           ("   ↑↓ move · enter pick · 1-9 jump · esc back", MUTED)))
            for i in range(top, min(n, top + win)):
                lab = items[i][1].copy()
                if i == idx:
                    t = Text(" ❯ ", style=f"bold {C1}")
                    lab.stylize("bold")
                    t.append_text(lab)
                    t.stylize("on #1f2140")
                else:
                    t = Text("   ")
                    t.append_text(lab)
                lines.append(t)
            if n > win:
                lines.append(Text(f"   ── {idx + 1}/{n} ──", style=MUTED))
            erase()
            for t in lines:
                console.print(t, no_wrap=True, overflow="ellipsis", crop=True)
            drawn = len(lines)
            k = getkey()
            if k in ("up", "k"):
                idx = (idx - 1) % n
            elif k in ("down", "j", "\t"):
                idx = (idx + 1) % n
            elif k == "home":
                idx = 0
            elif k == "end":
                idx = n - 1
            elif k == "pgup":
                idx = max(0, idx - win)
            elif k == "pgdn":
                idx = min(n - 1, idx + win)
            elif k in ("enter", "right", " "):
                erase()
                if title:
                    console.print(Text.assemble(("✔ ", f"bold {OK}"), (title, "bold"), ("  ›  ", MUTED))
                                  + items[idx][1], no_wrap=True, overflow="ellipsis", crop=True)
                return items[idx][0]
            elif k in ("esc", "q", "left", "back"):
                erase()
                return None
            elif k.isdigit() and k != "0" and int(k) <= n:
                idx = int(k) - 1
    finally:
        out.write("\x1b[?25h")
        out.flush()


def ask(label: str, default: str | None = None, required: bool = False, password: bool = False) -> str:
    prompt = Text.assemble(("› ", f"bold {C1}"), (label, "bold"))
    while True:
        v = Prompt.ask(prompt, default=default if default else "", show_default=bool(default),
                       password=password, console=console)
        v = (v or "").strip()
        if v or not required:
            return v
        console.print(Text("  This one is required.", style=BAD))


def ask_num(label: str, default=None, integer: bool = False):
    """Returns (changed, value). '-' clears (value None). Empty keeps default."""
    d = "" if default in (None, "") else str(default)
    while True:
        v = ask(label + ("  (- clears)" if d else ""), default=d)
        if v == d:
            return False, default
        if v == "-":
            return True, None
        n = to_num(v)
        if n is None or (integer and not float(n).is_integer()):
            console.print(Text("  Please type a number.", style=BAD))
            continue
        return True, int(n) if integer else n


def confirm(label: str, default: bool = False) -> bool:
    return Confirm.ask(Text.assemble(("? ", f"bold {C2}"), (label, "bold")), default=default, console=console)


def pause(msg: str = "press any key") -> None:
    console.print(Text(f"\n  ↵ {msg}", style=MUTED))
    if INTERACTIVE:
        getkey()
    else:
        input()


def busy(msg: str, fn, *a, **kw):
    with console.status(Text(msg, style=f"bold {C1}"), spinner="dots12", spinner_style=C2):
        return fn(*a, **kw)


# ─────────────────────────────── helpers ──────────────────────────────
def s(v) -> str:
    return "" if v is None else str(v)


def to_num(v: str):
    try:
        f = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return int(f) if f.is_integer() else round(f, 2)


def money(v, cur: str = "") -> str:
    if v in (None, ""):
        return "—"
    try:
        f = float(v)
        txt = f"{f:,.0f}" if f.is_integer() else f"{f:,.2f}"
    except (TypeError, ValueError):
        txt = str(v)
    return f"{txt} {cur}".strip()


def parse_t(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def ago(v) -> str:
    t = parse_t(v)
    if not t:
        return ""
    sec = (dt.datetime.now().astimezone() - t).total_seconds()
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    if sec < 86400 * 30:
        return f"{int(sec // 86400)}d ago"
    return t.strftime("%Y-%m-%d")


def when(v) -> Text:
    t = parse_t(v)
    if not t:
        return Text("—", style=MUTED)
    return Text.assemble((t.strftime("%Y-%m-%d %H:%M"), "bold"), (f"  {ago(v)}", MUTED))


def badge(status) -> Text:
    st = s(status) or "?"
    col = STATUS_COLOR.get(st, "#8a8a8a")
    return Text(f" {STATUS_ICON.get(st, '•')} {st} ", style=f"bold #0a0a14 on {col}")


def status_label(st: str) -> Text:
    col = STATUS_COLOR.get(st, "#aaaaaa")
    return Text.assemble((f"{STATUS_ICON.get(st, '•')} ", f"bold {col}"), (st, col))


def names(lst) -> list[str]:
    out = []
    if isinstance(lst, dict):
        lst = list(lst.keys()) if not all(isinstance(v, str) for v in lst.values()) else list(lst.values())
    for x in lst or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            n = x.get("name") or x.get("code") or x.get("label") or x.get("value")
            if n:
                out.append(str(n))
    return out


def car_line(o: dict) -> str:
    return " ".join(x for x in (s(o.get("brand")), s(o.get("model")), s(o.get("year"))) if x)


def cust_short(c) -> Text:
    if not isinstance(c, dict):
        return Text("—", style=MUTED)
    who = c.get("handle") or c.get("name") or c.get("phone")
    if who:
        t = Text(str(who), style="bold")
        if c.get("platform"):
            t.append(f" · {c['platform']}", style=MUTED)
        return t
    return Text(s(c.get("location")) or "—", style=MUTED)


def kv(rows, label_width: int = 14):
    g = Table.grid(padding=(0, 2))
    g.add_column(style=f"{C1}", no_wrap=True, min_width=label_width)
    g.add_column(style="bold", overflow="fold")
    count = 0
    for k, v in rows:
        if v in (None, "", [], {}):
            continue
        g.add_row(str(k), v if isinstance(v, Text) else str(v))
        count += 1
    return g if count else Text("—", style=MUTED)


def details_rows(d) -> list[tuple[str, str]]:
    if isinstance(d, dict):
        return [(str(k), s(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False))
                for k, v in d.items()]
    if isinstance(d, list):
        return [(s(x.get("label")), s(x.get("value"))) for x in d if isinstance(x, dict)]
    if isinstance(d, str):
        rows = []
        for line in d.splitlines():
            if ":" in line:
                a, b = line.split(":", 1)
                rows.append((a.strip(), b.strip()))
            elif line.strip():
                rows.append(("", line.strip()))
        return rows
    return []


def grad_text(lines: list[str], c1=(0, 215, 255), c2=(255, 95, 215)) -> Text:
    w = max(len(l) for l in lines)
    span = w + len(lines) * 3
    t = Text()
    for i, line in enumerate(lines):
        for j, ch in enumerate(line):
            f = min(1.0, (j + i * 3) / span)
            r, g, b = (int(a + (bb - a) * f) for a, bb in zip(c1, c2))
            t.append(ch, style=f"bold rgb({r},{g},{b})")
        if i < len(lines) - 1:
            t.append("\n")
    return t


FONT = {
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "4": ["██╗  ██╗", "██║  ██║", "███████║", "╚════██║", "     ██║", "     ╚═╝"],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
}


def banner() -> None:
    lines = ["".join(FONT[ch][i] for ch in "ACP4U") for i in range(6)]
    if console.width >= 50:
        console.print(Align.center(grad_text(lines)))
        sub = Text.assemble(("━━━━━━━━  ", LINE), ("P A R T S", f"bold {C3}"), ("  ·  ", MUTED),
                            ("terminal", f"italic {C1}"), (f"  v{VERSION}  ", MUTED), ("━━━━━━━━", LINE))
        console.print(Align.center(sub))
    else:
        console.print(Text.assemble((" ACP4U ", f"bold #000 on {C1}"), (" PARTS ", f"bold #000 on {C2}")))
    console.print()


def acct_name(info: dict) -> str:
    for k in ("account", "user", "key", "owner"):
        v = info.get(k)
        if isinstance(v, dict):
            for kk in ("name", "username", "label", "title"):
                if v.get(kk):
                    return str(v[kk])
        elif isinstance(v, str) and v and not v.startswith("acp4u_"):
            return v
    for k in ("account_name", "user_name", "username", "name", "key_name"):
        if info.get(k):
            return str(info[k])
    return "connected"


def key_name(info: dict) -> str:
    k = info.get("key")
    if isinstance(k, dict):
        return str(k.get("name") or k.get("label") or "")
    return str(info.get("key_name") or "")


def crumb(ctx: Ctx | None, *parts: str) -> None:
    console.clear()
    left = Text.assemble((" ACP4U ", f"bold #0a0a14 on {C1}"), (" PARTS ", f"bold #0a0a14 on {C2}"), "  ")
    for i, p in enumerate(parts):
        if i:
            left.append("  ›  ", style=MUTED)
        left.append(p, style="bold" if i == len(parts) - 1 else MUTED)
    right = Text()
    if ctx:
        right.append("● ", style=OK)
        right.append(acct_name(ctx.info), style="bold")
        host = re.sub(r"^https?://", "", ctx.api.base).split("/")[0]
        right.append(f"  {host}", style=MUTED)
        if ctx.api.base.startswith("http://"):
            right.append(" ⚠ http", style=C3)
    g = Table.grid(expand=True)
    g.add_column()
    g.add_column(justify="right")
    g.add_row(left, right)
    console.print(g)
    console.print(Rule(style=LINE))


def section(title: str) -> None:
    console.print()
    console.print(Rule(Text(f" {title} ", style=f"bold {C2}"), style=LINE, align="left"))


def ok_msg(msg: str) -> None:
    console.print(Text.assemble(("  ✔ ", f"bold {OK}"), (msg, "bold")))


def warn_msg(msg: str) -> None:
    console.print(Text.assemble(("  ⚠ ", f"bold {C3}"), (msg, C3)))


HINTS = {
    "no_key": "The key was not sent. Run  acp4u login.",
    "bad_key": "The key is wrong, turned off or replaced. Run  acp4u login  (or Settings › Change key).",
    "forbidden": "Ask the owner to tick this permission for the key on the API page (Edit key).",
    "too_large": "The upload is bigger than the host accepts — see post_max_size in Key & permissions.",
    "network": "Check your internet connection, the server address, or set a proxy (Settings › Proxy).",
    "proxy": "Check Settings › Proxy (type, host, port, user / password) — or switch the proxy off.",
    "duplicate_ref": "An order with this ref already exists.",
    "has_orders": "Only customers without orders can be deleted.",
}


def show_error(e: APIError) -> None:
    p = e.payload
    body = [Text(p.get("message") or str(e), style="bold")]
    if isinstance(p.get("fields"), dict) and p["fields"]:
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        t.add_column(style=f"bold {C3}")
        t.add_column()
        for k, v in p["fields"].items():
            t.add_row(str(k), str(v))
        body.append(t)
    if p.get("needs"):
        body.append(Text.assemble(("Missing permission: ", MUTED), (f"“{p['needs']}”", f"bold {C3}")))
    if p.get("id"):
        body.append(Text.assemble(("Existing record: ", MUTED), (f"#{p['id']}", f"bold {C1}")))
    if p.get("detail"):
        body.append(Text.assemble(("Reason: ", MUTED), (str(p["detail"]), C3)))
    if e.code in HINTS:
        body.append(Text(HINTS[e.code], style=f"italic {MUTED}"))
    sub = f"HTTP {e.status}" if e.status else None
    console.print(Panel(Group(*body), title=Text(f" ✖ {e.code} ", style=f"bold #ffffff on {BAD}"),
                        title_align="left", subtitle=sub, border_style=BAD, box=box.ROUNDED, padding=(0, 2)))


def safe(fn, *a, msg: str = "Working…", **kw):
    """Run an API call with a spinner; print errors. Returns result or None."""
    try:
        return busy(msg, fn, *a, **kw)
    except APIError as e:
        show_error(e)
        return None


# ─────────────────────────── image preview ────────────────────────────
def image_text(data: bytes, max_w: int = 60, max_rows: int = 22) -> Text | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    nw = max(8, min(max_w, console.width - 4))
    nh = max(2, int(h * nw / w))
    if nh > max_rows * 2:
        nh = max_rows * 2
        nw = max(8, int(w * nh / h))
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


def preview_photos(ctx: Ctx, photos: list, route: str = "photos") -> None:
    if not photos:
        warn_msg("No photos.")
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        warn_msg("Install Pillow for previews:  pip install pillow")
        return
    cols = 2 if console.width >= 100 and len(photos) > 1 else 1
    width = (console.width - 6) // cols - 4
    cards = []
    for p in photos:
        try:
            data, _ = busy(f"Loading photo {p.get('slot', '')}…", ctx.api.download, f"{route}/{p['id']}", size="thumb")
        except APIError as e:
            show_error(e)
            continue
        img = image_text(data, max_w=min(width, 70), max_rows=18)
        cards.append(Panel(img or Text("(cannot show this image)", style=MUTED),
                           title=Text(f" slot {p.get('slot', '?')} · #{p['id']} ", style=f"bold {C1}"),
                           border_style=LINE, box=box.ROUNDED, expand=False))
    if cards:
        console.print(Columns(cards) if cols > 1 else Group(*cards))


def download_photos(ctx: Ctx, photos: list, folder: Path, route: str = "photos") -> None:
    if not photos:
        warn_msg("No photos.")
        return
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for p in photos:
        try:
            data, ctype = busy(f"Downloading slot {p.get('slot')}…", ctx.api.download, f"{route}/{p['id']}")
        except APIError as e:
            show_error(e)
            continue
        ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".jpg"
        ext = ".jpg" if ext in (".jpe", ".jfif") else ext
        f = folder / f"slot{p.get('slot', 0)}-{p['id']}{ext}"
        f.write_bytes(data)
        saved += 1
        console.print(Text.assemble(("  ⇩ ", C1), (str(f), "bold"), (f"  {len(data) / 1024:,.0f} KB", MUTED)))
    if saved:
        ok_msg(f"{saved} photo(s) saved to {folder}")
        if shutil.which("xdg-open") and confirm("Open the folder?", default=False):
            subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────────── photo input ──────────────────────────────
def ask_photos(maxn: int = 8, label: str = "Photo") -> tuple[list[Path], list[str]]:
    console.print(Text("  Paste file paths or links — you can drag files into the terminal, several at once.\n"
                       "  A folder adds all its images. Empty line = done.", style=MUTED))
    files: list[Path] = []
    urls: list[str] = []
    while len(files) + len(urls) < maxn:
        line = ask(f"{label} {len(files) + len(urls) + 1}/{maxn}")
        if not line:
            break
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = [line]
        for part in parts:
            if len(files) + len(urls) >= maxn:
                warn_msg(f"Only {maxn} photos fit — the rest is skipped.")
                break
            if re.match(r"^(https?://|data:image/)", part, re.I):
                urls.append(part)
                console.print(Text.assemble(("  ✔ ", OK), ("link ", MUTED), (part[:80], "bold")))
                continue
            path = Path(re.sub(r"^file://", "", part)).expanduser()
            cand = sorted(x for x in path.iterdir() if x.suffix.lower() in IMG_EXT) if path.is_dir() else [path]
            for f in cand:
                if len(files) + len(urls) >= maxn:
                    break
                if not f.is_file():
                    console.print(Text.assemble(("  ✖ ", BAD), ("not found: ", MUTED), (str(f), "bold")))
                    continue
                if f.suffix.lower() not in IMG_EXT:
                    console.print(Text.assemble(("  ✖ ", BAD), ("not JPEG/PNG/GIF/WebP: ", MUTED), (f.name, "bold")))
                    continue
                size = f.stat().st_size
                if size > 20 * 1024 * 1024:
                    console.print(Text.assemble(("  ✖ ", BAD), ("bigger than 20 MB: ", MUTED), (f.name, "bold")))
                    continue
                files.append(f)
                console.print(Text.assemble(("  ✔ ", OK), (f.name, "bold"), (f"  {size / 1024:,.0f} KB", MUTED)))
    return files, urls


def file_tuple(field: str, f: Path):
    mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    return (field, (f.name, f.read_bytes(), mime))


# ───────────────────────────── rendering ──────────────────────────────
def stock_text(st) -> Text:
    return {"in_stock": Text("● in stock", style=f"bold {OK}"),
            "on_order": Text("◐ on order", style=f"bold {C3}"),
            "out": Text("○ out", style=f"bold {BAD}")}.get(s(st), Text(s(st) or "—", style=MUTED))


def quotes_table(quotes: list) -> Table:
    t = Table(box=box.ROUNDED, border_style=LINE, header_style=f"bold {C2}", expand=True)
    for col, kw in (("By", {}), ("Price", {"justify": "right"}), ("Ship", {"justify": "right"}),
                    ("Total", {"justify": "right"}), ("Stock", {}), ("Qty", {"justify": "right"}),
                    ("Delivery", {}), ("Note", {"overflow": "fold"}), ("Pics", {"justify": "right"}),
                    ("Updated", {})):
        t.add_column(col, **kw)
    for q in quotes:
        cur = s(q.get("currency"))
        by = Text(s(q.get("by")) or "?", style=f"bold {C1}" if q.get("mine") else "bold")
        if q.get("mine"):
            by.append(" (me)", style=C2)
        total = q.get("total")
        if total in (None, "") and q.get("price") not in (None, ""):
            total = (to_num(q.get("price")) or 0) + (to_num(q.get("shipping")) or 0)
        t.add_row(by, money(q.get("price"), cur), money(q.get("shipping"), cur),
                  Text(money(total, cur), style=f"bold {OK}"), stock_text(q.get("stock")),
                  s(q.get("qty")), s(q.get("delivery")), s(q.get("note")),
                  str(len(q.get("photos") or [])) if q.get("photos") else "", ago(q.get("updated_at")))
    return t


def render_order(o: dict) -> None:
    head = Text()
    head.append(f" #{o.get('id', '—')} ", style=f"bold #0a0a14 on {C1}")
    head.append("  ")
    head.append_text(badge(o.get("status")))
    if o.get("ref"):
        head.append("   ref ", style=MUTED)
        head.append(s(o["ref"]), style=f"bold {C3}")
    if o.get("source"):
        head.append("   via ", style=MUTED)
        head.append(s(o["source"]), style="bold")
    console.print(head)
    title = Text(car_line(o) or "—", style="bold white")
    title.append("  —  ", style=MUTED)
    title.append(s(o.get("part")) or "—", style=f"bold {C2}")
    console.print(title)
    console.print()

    car = kv([(lab, o.get(k)) for k, lab in CAR_FIELDS])
    part = kv([(lab, o.get(k)) for k, lab in PART_FIELDS])
    c = o.get("customer")
    if isinstance(c, dict):
        cust = kv([("Customer #", c.get("id"))] + [(lab, c.get(k)) for k, lab in CUST_FIELDS])
    else:
        cust = Text("not visible with this key" if "customer" in o else "—", style=MUTED)
    panels = [Panel(car, title=f"[bold {C1}]▌Car", title_align="left", border_style=LINE, box=box.ROUNDED),
              Panel(part, title=f"[bold {C2}]▌Part", title_align="left", border_style=LINE, box=box.ROUNDED),
              Panel(cust, title=f"[bold {C3}]▌Customer", title_align="left", border_style=LINE, box=box.ROUNDED)]
    console.print(Columns(panels, equal=True, expand=True))

    det = details_rows(o.get("details"))
    if det:
        console.print(Panel(kv(det), title=f"[bold {C1}]▌Details", title_align="left",
                            border_style=LINE, box=box.ROUNDED))

    photos = o.get("photos") or []
    slots = Text()
    taken = {p.get("slot"): p for p in photos}
    for i in range(1, 9):
        if i in taken:
            slots.append(f" {i} ", style=f"bold #0a0a14 on {C1}")
        else:
            slots.append(f" {i} ", style=f"{MUTED} on #1c1c2a")
        slots.append(" ")
    slots.append(f"  {len(photos)}/8 photos", style=MUTED)
    console.print(Panel(slots, title=f"[bold {C2}]▌Photos", title_align="left", border_style=LINE, box=box.ROUNDED))

    quotes = o.get("quotes")
    if quotes:
        console.print(Panel(quotes_table(quotes), title=f"[bold {OK}]▌Prices", title_align="left",
                            border_style=LINE, box=box.ROUNDED, padding=0))
    foot = Text.assemble(("created ", MUTED), when(o.get("created_at")))
    if o.get("created_by"):
        foot.append("  by ", style=MUTED)
        foot.append(s(o["created_by"]), style="bold")
    foot.append("     updated ", style=MUTED)
    foot.append_text(when(o.get("updated_at")))
    console.print(foot)


def orders_table(rows: list, title: str | None = None) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, header_style=f"bold {C2}", border_style=LINE, expand=True,
              row_styles=["", "on #14141f"], title=title, title_style=f"bold {C1}")
    t.add_column("#", style=f"bold {C1}", justify="right", no_wrap=True)
    t.add_column("Status", no_wrap=True)
    t.add_column("Car", overflow="ellipsis", no_wrap=True, ratio=2)
    t.add_column("Part", overflow="ellipsis", no_wrap=True, ratio=3, style=f"{C2}")
    t.add_column("Customer", overflow="ellipsis", no_wrap=True, ratio=2)
    t.add_column("▣", justify="right", style=MUTED)
    t.add_column("Price", justify="right", no_wrap=True)
    t.add_column("Updated", style=MUTED, no_wrap=True)
    for o in rows:
        quotes = o.get("quotes") or []
        price = Text("")
        if quotes:
            mine = [q for q in quotes if q.get("mine")]
            q = (mine or quotes)[0]
            tot = q.get("total") if q.get("total") not in (None, "") else q.get("price")
            price = Text(money(tot, s(q.get("currency"))), style=f"bold {OK}" if mine else "bold")
            if len(quotes) > 1:
                price.append(f" +{len(quotes) - 1}", style=MUTED)
        car = Text(" ".join(x for x in (s(o.get("brand")), s(o.get("model"))) if x), style="bold")
        if o.get("year"):
            car.append(f" {o['year']}", style=MUTED)
        t.add_row(str(o.get("id")), badge(o.get("status")), car, s(o.get("part")), cust_short(o.get("customer")),
                  str(len(o.get("photos") or [])) if o.get("photos") else "", price, ago(o.get("updated_at")))
    return t


def order_label(o: dict) -> Text:
    t = Text(f"#{s(o.get('id')):<6}", style=f"bold {C1}")
    t.append_text(badge(o.get("status")))
    t.append(f"  {car_line(o)}", style="bold")
    t.append(f"  —  {s(o.get('part'))}", style=C2)
    t.append(f"   {ago(o.get('updated_at'))}", style=MUTED)
    return t


# ─────────────────────────────── setup ────────────────────────────────
def setup(conf: dict) -> Ctx | None:
    console.clear()
    banner()
    console.print(Panel(Text.assemble(
        ("Welcome! ", f"bold {C1}"),
        ("Paste your API key once — it is tested and saved to\n", "bold"),
        (str(CONF_FILE), C3), ("  (only your user can read it).\n\n", MUTED),
        ("The owner makes keys on the site under ", MUTED), ("API", "bold"),
        (". The key starts with ", MUTED), ("acp4u_", f"bold {C2}"), (".", MUTED)),
        border_style=C1, box=box.ROUNDED, padding=(1, 3), title=f"[bold {C2}]🔑 Connect", title_align="left"))
    base = conf.get("base") or DEFAULT_BASE
    while True:
        key = ask("API key (hidden while typing / pasting)", required=True, password=True)
        key = key.strip().strip("'\"")
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if not key.startswith("acp4u_"):
            warn_msg("This does not look like an ACP4U key (they start with acp4u_) — trying anyway.")
        console.print(Text.assemble(("  key ", MUTED), (mask(key), f"bold {C3}"), ("   via ", MUTED),
                                    (proxy_label(effective_proxy(conf)), C1)))
        api = API(base, key, effective_proxy(conf))
        try:
            info = busy("Checking the key…", api.get, "info")
        except APIError as e:
            if e.code in ("network", "proxy"):
                show_error(e)
                opts = [("proxy", Text.assemble(("⛨ ", C1), ("Set up a proxy (SOCKS5 / SOCKS4 / HTTP)", "bold"))),
                        ("retry", "⟳ Try again")]
                if base.startswith("https://"):
                    opts.append(("http", "⚠ Try plain http instead of https"))
                opts.append(("quit", Text("✖ Give up", style=MUTED)))
                a = choose("The server could not be reached. What now?", opts)
                if a == "proxy":
                    proxy_menu(conf, base)
                    continue
                if a == "retry":
                    continue
                if a != "http":
                    return None
                warn_msg("Trying http — the key then travels unencrypted.")
                base = "http://" + base[len("https://"):]
                api = API(base, key, effective_proxy(conf))
                try:
                    info = busy("Checking the key…", api.get, "info")
                except APIError as e2:
                    show_error(e2)
                    base = conf.get("base") or DEFAULT_BASE
                    if not confirm("Try again?", True):
                        return None
                    continue
            else:
                show_error(e)
                if not confirm("Try another key?", True):
                    return None
                continue
        conf.update({"key": key, "base": base, "saved_at": dt.datetime.now().isoformat(timespec="seconds")})
        save_conf(conf)
        ctx = Ctx(conf)
        ctx.info = info.get("data") or {}
        apply_info(ctx)
        console.print()
        ok_msg(f"Connected as {acct_name(ctx.info)} — key saved. You won't be asked again.")
        time.sleep(1.2)
        return ctx


def apply_info(ctx: Ctx) -> None:
    global STATUSES, CURRENCIES
    st = names(ctx.info.get("statuses"))
    if st:
        STATUSES = st
    CURRENCIES = names(ctx.info.get("currencies"))


def connect(conf: dict, quiet: bool = False) -> Ctx | None:
    env_key = os.environ.get("ACP4U_KEY")
    if env_key:
        conf = {**conf, "key": env_key}
    if os.environ.get("ACP4U_BASE"):
        conf = {**conf, "base": os.environ["ACP4U_BASE"]}
    if not conf.get("key"):
        return setup(conf)
    conf.setdefault("base", DEFAULT_BASE)
    ctx = Ctx(conf)
    try:
        info = busy("Connecting…", ctx.api.get, "info") if not quiet else ctx.api.get("info")
        ctx.info = info.get("data") or {}
        apply_info(ctx)
    except APIError as e:
        show_error(e)
        if e.code in ("bad_key", "no_key"):
            if confirm("Enter a new key now?", True):
                return setup(conf)
            return None
        if e.code in ("network", "proxy"):
            a = choose("The server could not be reached. What now?", [
                ("proxy", Text.assemble(("⛨ ", C1), ("Proxy settings", "bold"), (f"   now: {proxy_label(effective_proxy(conf))}", MUTED))),
                ("retry", "⟳ Try again"),
                ("offline", Text("→ Continue anyway (calls fail until the server answers)", style=MUTED))])
            if a == "proxy":
                proxy_menu(conf, conf.get("base"))
                return connect(conf, quiet)
            if a == "retry":
                return connect(conf, quiet)
            if a == "offline":
                return ctx
            return None
        return None
    return ctx


# ─────────────────────────────── proxy menu ───────────────────────────
def proxy_menu(conf: dict, base: str | None = None) -> bool:
    """Interactive proxy setup. Saves conf['proxy']. Returns True when something changed."""
    base = base or conf.get("base") or DEFAULT_BASE
    changed = False
    while True:
        p = conf.get("proxy") or {"mode": "system"}
        section("⛨ Proxy")
        rows = [("Now", Text(proxy_label(p), style=f"bold {C1}")),
                ("SOCKS support", Text("✔ installed (PySocks)", style=OK) if socks_available()
                 else Text("✖ missing — pip install pysocks  /  sudo apt install python3-socks", style=BAD)),
                ("System proxy", env_proxy() and re.sub(r"//([^:@/]+):[^@/]*@", r"//\1:••••@", env_proxy()) or "—")]
        if PROXY_OVERRIDE is not None:
            rows.append(("This run", Text(f"overridden by --proxy / ACP4U_PROXY: {proxy_label(PROXY_OVERRIDE)}", style=C3)))
        console.print(Panel(kv(rows), border_style=LINE, box=box.ROUNDED))
        items = [("custom", Text.assemble(("✚ ", OK), ("Set a proxy", "bold"), ("  type · host · port · user · password", MUTED))),
                 ("url", Text.assemble(("⎘ ", C1), ("Paste a proxy URL", "bold"), ("  socks5h://user:pass@host:1080", MUTED)))]
        for name, typ, host, port in PROXY_PRESETS:
            items.append((("preset", typ, host, port), Text.assemble(("★ ", C3), (f"{name:<22}", "bold"),
                                                                    (f"{typ}://{host}:{port}", MUTED))))
        items += [("system", Text.assemble(("◎ ", C1), ("Use the system proxy", ""), ("  (ALL_PROXY / HTTPS_PROXY)", MUTED))),
                  ("none", Text.assemble(("○ ", MUTED), ("Direct — no proxy", ""))),
                  ("test", Text.assemble(("⚡ ", C3), ("Test the connection", "bold"))),
                  ("ip", Text.assemble(("🌐 ", C1), ("Show my exit IP", ""), ("  (asks api.ipify.org)", MUTED))),
                  ("back", Text("↩ Back", style=MUTED))]
        a = choose("Proxy", items)
        if a in (None, "back"):
            return changed
        new = None
        if a == "custom":
            new = proxy_form(p)
        elif a == "url":
            u = ask("Proxy URL (socks5h://user:pass@host:port, socks4://host:port, http://host:port)")
            if u:
                try:
                    new = parse_proxy_url(u)
                except ValueError as e:
                    show_error(APIError(0, {"error": "bad_proxy", "message": str(e)}))
        elif isinstance(a, tuple):
            _, typ, host, port = a
            new = {"mode": "custom", "type": typ, "host": host, "port": port, "user": "", "password": ""}
        elif a in ("system", "none"):
            new = {**p, "mode": a}
        elif a == "test":
            ok, ms, msg = busy("Testing…", test_connection, base, effective_proxy(conf))
            (ok_msg if ok else warn_msg)(msg)
            pause()
            continue
        elif a == "ip":
            try:
                ip = busy("Asking api.ipify.org…", exit_ip, effective_proxy(conf))
                ok_msg(f"The world sees you as  {ip}")
            except APIError as e:
                show_error(e)
            pause()
            continue
        if new:
            if new.get("mode") == "custom" and new.get("type", "").startswith("socks") and not socks_available():
                warn_msg("SOCKS support is missing — install it first:  pip install pysocks")
            conf["proxy"] = new
            save_conf(conf)
            changed = True
            ok_msg(f"Proxy: {proxy_label(new)}")
            ok, ms, msg = busy("Testing the connection…", test_connection, base, new)
            (ok_msg if ok else warn_msg)(msg)
            pause()


def proxy_form(p: dict) -> dict | None:
    cur_t = p.get("type") or "socks5h"
    typ = choose("Proxy type", [(k, Text.assemble((f"{k:<8}", f"bold {C1}"), (lab, MUTED))) for k, lab in PROXY_TYPES],
                 default=[k for k, _ in PROXY_TYPES].index(cur_t) if cur_t in DEFAULT_PORTS else 0)
    if not typ:
        return None
    host = ask("Host (IP or name)", default=p.get("host") or "127.0.0.1", required=True)
    if "://" in host:
        try:
            return parse_proxy_url(host)
        except ValueError as e:
            show_error(APIError(0, {"error": "bad_proxy", "message": str(e)}))
            return None
    while True:
        port = to_num(ask("Port", default=str(p.get("port") or DEFAULT_PORTS[typ])))
        if port and 1 <= int(port) <= 65535:
            break
        console.print(Text("  1-65535, please.", style=BAD))
    user, pw = "", ""
    if typ != "socks4" and typ != "socks4a":
        user = ask("Username (empty = none)", default=p.get("user") or "")
        if user:
            pw = ask("Password (hidden · empty = keep the saved one)" if p.get("password") else "Password (hidden)",
                     password=True) or (p.get("password") or "")
    elif confirm("SOCKS4 user id?", False):
        user = ask("User id")
    return {"mode": "custom", "type": typ, "host": host.strip("[]"), "port": int(port), "user": user, "password": pw}


# ──────────────────────────── info screen ─────────────────────────────
def perms_list(info: dict) -> list[tuple[str, bool]]:
    p = info.get("permissions")
    if p is None and isinstance(info.get("key"), dict):
        p = info["key"].get("permissions")
    out = []
    if isinstance(p, dict):
        for k, v in p.items():
            lab = k.replace("_", " ").strip().capitalize() if re.fullmatch(r"[a-z0-9_]+", k) else k
            out.append((lab, bool(v)))
    elif isinstance(p, list):
        for x in p:
            if isinstance(x, str):
                out.append((x, True))
            elif isinstance(x, dict):
                out.append((s(x.get("label") or x.get("name") or x.get("key")),
                            bool(x.get("allowed", x.get("granted", x.get("value", True))))))
    return out


def info_screen(ctx: Ctx) -> None:
    res = safe(ctx.api.get, "info", msg="Loading key info…")
    if res:
        ctx.info = res.get("data") or {}
        apply_info(ctx)
    crumb(ctx, "Key & permissions")
    info = ctx.info
    top = kv([("Account", acct_name(info)), ("Key name", key_name(info)),
              ("Key", Text(mask(ctx.api.key), style=C3)), ("Server", ctx.api.base),
              ("Saved in", str(CONF_FILE))])
    console.print(Panel(top, title=f"[bold {C1}]▌Connection", title_align="left", border_style=LINE, box=box.ROUNDED))

    perms = perms_list(info)
    if perms:
        g = Table.grid(padding=(0, 3))
        cols = 2 if console.width > 90 else 1
        for _ in range(cols):
            g.add_column()
        cells = [Text.assemble(("✔ " if ok else "✖ ", f"bold {OK}" if ok else BAD),
                               (lab, "bold" if ok else MUTED)) for lab, ok in perms]
        for i in range(0, len(cells), cols):
            g.add_row(*cells[i:i + cols], *([""] * (cols - len(cells[i:i + cols]))))
        console.print(Panel(g, title=f"[bold {C2}]▌Permissions of this key", title_align="left",
                            border_style=LINE, box=box.ROUNDED))
    st = Text()
    for x in STATUSES:
        st.append_text(badge(x))
        st.append(" ")
    console.print(Panel(st, title=f"[bold {C3}]▌Statuses", title_align="left", border_style=LINE, box=box.ROUNDED))
    if CURRENCIES:
        cur = Text("  ".join(CURRENCIES), style=f"bold {OK}")
        console.print(Panel(cur, title=f"[bold {OK}]▌Currencies", title_align="left", border_style=LINE, box=box.ROUNDED))
    lim = info.get("limits")
    if isinstance(lim, dict) and lim:
        console.print(Panel(kv([(k, v if not isinstance(v, (dict, list)) else json.dumps(v)) for k, v in lim.items()]),
                            title=f"[bold {C1}]▌Limits", title_align="left", border_style=LINE, box=box.ROUNDED))
    if confirm("Show everything the server said (raw JSON)?", False):
        console.print(Panel(RJSON(json.dumps(info, ensure_ascii=False)), border_style=LINE, box=box.ROUNDED))
    pause()


# ──────────────────────────── dashboard ───────────────────────────────
def load_dash(ctx: Ctx, force: bool = False) -> None:
    if not force and ctx.dash is not None and time.time() - ctx.dash_time < 90:
        return
    jobs = {st: {"status": st} for st in STATUSES}
    jobs["__mine"] = {"mine": "waiting"}
    jobs["__all"] = {}
    with console.status(Text("Loading dashboard…", style=f"bold {C1}"), spinner="dots12", spinner_style=C2):
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {k: ex.submit(ctx.api.total, **v) for k, v in jobs.items()}
            ctx.dash = {k: f.result() for k, f in futs.items()}
    ctx.dash_time = time.time()


def render_dash(ctx: Ctx) -> None:
    d = ctx.dash or {}
    if d.get("__all") is None:
        console.print(Align.center(Text("dashboard needs “Read orders” for this key", style=MUTED)))
        return
    cards = []
    for st in STATUSES:
        n = d.get(st)
        col = STATUS_COLOR.get(st, "#aaaaaa")
        body = Text.assemble((f"{n if n is not None else '–'}", f"bold {col}"))
        cards.append(Panel(Align.center(body), title=Text(f"{STATUS_ICON.get(st, '•')} {STATUS_SHORT.get(st, st)}",
                                                          style=col),
                           border_style=col if n else LINE, box=box.ROUNDED, width=18, padding=(0, 0)))
    mine = d.get("__mine")
    cards.append(Panel(Align.center(Text(str(mine if mine is not None else "–"), style=f"bold {C2}")),
                       title=Text("$ Need my price", style=C2), border_style=C2 if mine else LINE,
                       box=box.ROUNDED, width=21, padding=(0, 0)))
    console.print(Align.center(Columns(cards, align="center")))
    console.print(Align.center(Text.assemble(("total orders  ", MUTED), (str(d.get("__all")), f"bold {C1}"))))


# ──────────────────────────── orders list ─────────────────────────────
FILTER_DEFS = [
    ("status", "Status"), ("q", "Search text"), ("brand", "Brand"), ("ref", "Ref"),
    ("customer_id", "Customer id"), ("source", "Source"), ("mine", "My price"),
    ("iranian", "Iranian cars"), ("created_since", "Created since"), ("updated_since", "Updated since"),
    ("sort", "Sort"), ("per_page", "Per page"),
]


def edit_filters(f: dict) -> dict:
    f = dict(f)
    while True:
        items = []
        for k, lab in FILTER_DEFS:
            v = f.get(k)
            t = Text(f"{lab:<15}", style="bold")
            t.append(str(v) if v not in (None, "") else "·", style=C3 if v not in (None, "") else MUTED)
            items.append((k, t))
        items += [("__apply", Text("✔ Apply", style=f"bold {OK}")), ("__clear", Text("⌫ Clear all", style=BAD))]
        k = choose("Filters", items, default=len(items) - 2)
        if k in (None, "__apply"):
            return f
        if k == "__clear":
            return {}
        cur = s(f.get(k))
        if k == "status":
            v = choose("Status", [("", Text("· any", style=MUTED))] + [(x, status_label(x)) for x in STATUSES])
        elif k == "source":
            v = choose("Source", [("", "· any"), ("form", "form  — added in the panel"),
                                  ("excel", "excel — imported"), ("api", "api   — added with a key")])
        elif k == "mine":
            v = choose("My price", [("", "· any"), ("waiting", "waiting — New / In process, no price from me"),
                                    ("priced", "priced  — I gave a price")])
        elif k == "iranian":
            v = choose("Iranian cars", [("", "· any"), ("exclude", "exclude Iranian cars"),
                                        ("only", "only Iranian cars")])
        elif k == "sort":
            v = choose("Sort", [("newest", "newest first"), ("oldest", "oldest first"), ("updated", "last updated")])
        elif k in ("created_since", "updated_since"):
            v = ask(f"{dict(FILTER_DEFS)[k]} (e.g. 2026-10-01 or 'today' / '7d')", default=cur)
            v = rel_date(v)
        else:
            v = ask(dict(FILTER_DEFS)[k] + " (empty = any)", default=cur)
        if v is None:
            continue
        if v == "":
            f.pop(k, None)
        else:
            f[k] = v


def rel_date(v: str) -> str:
    v = v.strip().lower()
    today = dt.date.today()
    if v == "today":
        return today.isoformat()
    if v == "yesterday":
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+)\s*([dhw])", v)
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = {"d": dt.timedelta(days=n), "h": dt.timedelta(hours=n), "w": dt.timedelta(weeks=n)}[u]
        return (dt.datetime.now(dt.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    return v


def chips(f: dict) -> Text:
    t = Text()
    for k, lab in FILTER_DEFS:
        if f.get(k) not in (None, ""):
            t.append(f" {lab}: ", style=f"#0a0a14 on {C2}")
            t.append(f"{f[k]} ", style=f"bold #0a0a14 on {C2}")
            t.append(" ")
    return t


def orders_screen(ctx: Ctx, filters: dict | None = None, title: str = "Orders") -> None:
    f = dict(filters or {})
    page = 1
    while True:
        params = {"per_page": ctx.per_page, **f, "page": page}
        res = safe(ctx.api.call, "GET", "orders", params=params, msg="Loading orders…")
        if res is None:
            pause()
            return
        rows = res.get("data") or []
        meta = res.get("meta") or {}
        pages = int(meta.get("pages") or 1)
        crumb(ctx, title)
        if f:
            console.print(chips(f))
        if rows:
            console.print(orders_table(rows))
        else:
            console.print(Panel(Align.center(Text("No orders here.", style=MUTED)), border_style=LINE, box=box.ROUNDED))
        console.print(Text.assemble(("  page ", MUTED), (f"{meta.get('page', page)}/{pages}", f"bold {C1}"),
                                    ("   ·   ", MUTED), (str(meta.get("total", len(rows))), f"bold {C1}"),
                                    (" orders", MUTED)))
        items = []
        if rows:
            items.append(("open", Text("❯ Open an order", style=f"bold {C1}")))
        if page < pages:
            items.append(("next", "→ Next page"))
        if page > 1:
            items.append(("prev", "← Previous page"))
        if pages > 2:
            items.append(("goto", "↷ Go to page…"))
        items += [("filter", "⚲ Filters & search"), ("refresh", "⟳ Refresh")]
        if f:
            items.append(("clear", "⌫ Clear filters"))
        if rows:
            items.append(("export", "⇩ Export all matching orders to CSV"))
        items.append(("back", Text("↩ Back", style=MUTED)))
        a = choose("What now?", items)
        if a in (None, "back"):
            return
        if a == "open":
            oid = choose("Which order?", [(o.get("id"), order_label(o)) for o in rows])
            if oid is not None:
                order_screen(ctx, oid)
        elif a == "next":
            page += 1
        elif a == "prev":
            page -= 1
        elif a == "goto":
            v = to_num(ask(f"Page (1-{pages})", default=str(page)))
            if v:
                page = max(1, min(pages, int(v)))
        elif a == "filter":
            f = edit_filters(f)
            page = 1
        elif a == "clear":
            f = {}
            page = 1
        elif a == "export":
            export_csv(ctx, f)
            pause()


def export_csv(ctx: Ctx, f: dict) -> None:
    allrows, page = [], 1
    with console.status(Text("Fetching orders…", style=f"bold {C1}"), spinner="dots12", spinner_style=C2) as st:
        while True:
            try:
                res = ctx.api.call("GET", "orders", params={**f, "per_page": 100, "page": page})
            except APIError as e:
                show_error(e)
                return
            allrows += res.get("data") or []
            pages = int((res.get("meta") or {}).get("pages") or 1)
            st.update(Text(f"Fetching orders… page {page}/{pages}  ({len(allrows)})", style=f"bold {C1}"))
            if page >= pages:
                break
            page += 1
    ctx.dl_dir.mkdir(parents=True, exist_ok=True)
    out = ctx.dl_dir / f"orders-{dt.datetime.now():%Y%m%d-%H%M%S}.csv"
    cols = ["id", "status", "brand", "model", "year", "vin", "part", "part_number", "side", "colour", "info",
            "vehicle_type", "fuel", "origin", "engine_no", "gvw", "ref", "source", "customer_id", "customer_handle",
            "customer_platform", "customer_phone", "customer_location", "photos", "quotes", "created_at",
            "updated_at", "created_by"]
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for o in allrows:
            c = o.get("customer") or {}
            row = {**o, "customer_id": c.get("id"), "customer_handle": c.get("handle"),
                   "customer_platform": c.get("platform"), "customer_phone": c.get("phone"),
                   "customer_location": c.get("location"), "photos": len(o.get("photos") or []),
                   "quotes": len(o.get("quotes") or [])}
            w.writerow([s(row.get(k)) for k in cols])
    ok_msg(f"{len(allrows)} orders → {out}")


# ──────────────────────────── order screen ────────────────────────────
def order_screen(ctx: Ctx, oid) -> None:
    while True:
        try:
            res = busy(f"Loading order #{oid}…", ctx.api.get, f"orders/{oid}")
        except APIError as e:
            show_error(e)
            pause()
            return
        o = res.get("data") or {}
        crumb(ctx, "Orders", f"#{oid}")
        render_order(o)
        console.print()
        items = [("status", Text.assemble(("◆ ", C3), ("Change status", "bold"))),
                 ("edit", Text.assemble(("✎ ", C1), ("Edit car / part / ref / details", ""))),
                 ("customer", Text.assemble(("☺ ", C3), ("Customer of this order", ""))),
                 ("photos", Text.assemble(("▣ ", C2), (f"Photos  ({len(o.get('photos') or [])}/8)", ""))),
                 ("quote", Text.assemble(("$ ", OK), ("My price, shipping & stock", ""))),
                 ("refresh", Text.assemble(("⟳ ", C1), ("Refresh", ""))),
                 ("json", Text.assemble(("{} ", MUTED), ("Raw JSON", MUTED))),
                 ("delete", Text.assemble(("✖ ", BAD), ("Delete this order", BAD))),
                 ("back", Text("↩ Back", style=MUTED))]
        a = choose(f"Order #{oid}", items)
        if a in (None, "back"):
            return
        if a == "status":
            cur = o.get("status")
            st = choose("New status", [(x, status_label(x)) for x in STATUSES],
                        default=STATUSES.index(cur) if cur in STATUSES else 0)
            if st and st != cur:
                if safe(ctx.api.call, "POST", f"orders/{oid}/status", json_body={"status": st}, msg="Saving…"):
                    ok_msg(f"Status → {st}")
                    ctx.dash = None
                    time.sleep(0.6)
                else:
                    pause()
        elif a == "edit":
            edit_order(ctx, o)
        elif a == "customer":
            order_customer(ctx, o)
        elif a == "photos":
            photos_menu(ctx, o)
        elif a == "quote":
            quote_screen(ctx, oid)
        elif a == "json":
            console.print(Panel(RJSON(json.dumps(o, ensure_ascii=False)), border_style=LINE, box=box.ROUNDED))
            pause()
        elif a == "delete":
            console.print(Panel(Text.assemble(("This deletes order ", "bold"), (f"#{oid}", f"bold {C1}"),
                                              (" with all its photos and prices. It cannot be undone.", "bold")),
                                border_style=BAD, box=box.HEAVY))
            if ask(f"Type the order number {oid} to delete it (empty = cancel)") == str(oid):
                if safe(ctx.api.call, "DELETE", f"orders/{oid}", msg="Deleting…"):
                    ok_msg(f"Order #{oid} deleted.")
                    ctx.dash = None
                    pause()
                    return
                pause()
            else:
                warn_msg("Not deleted.")
                time.sleep(0.6)


def edit_order(ctx: Ctx, o: dict) -> None:
    oid = o.get("id")
    changes: dict = {}
    details = dict(details_rows(o.get("details")))
    fields = CAR_FIELDS + PART_FIELDS + [("ref", "Ref")]
    while True:
        items = []
        for k, lab in fields:
            v = changes.get(k, o.get(k))
            t = Text(f"{lab:<16}", style="bold")
            t.append(s(v) or "·", style=(C3 if k in changes else "") if s(v) else MUTED)
            if k in changes:
                t.append("  ✎", style=C3)
            items.append((k, t))
        dtxt = ", ".join(f"{a}: {b}" for a, b in details.items()) or "·"
        items.append(("details", Text.assemble((f"{'Details':<16}", "bold"), (dtxt, C3 if "details" in changes else ""))))
        n = len(changes)
        items.append(("__save", Text(f"✔ Save {n} change(s)", style=f"bold {OK}") if n else Text("✔ Save (nothing changed)", style=MUTED)))
        items.append(("__cancel", Text("↩ Cancel", style=MUTED)))
        k = choose("Pick a field to change", items, default=0)
        if k in (None, "__cancel"):
            if changes and not confirm("Discard your changes?", False):
                continue
            return
        if k == "__save":
            if not changes:
                return
            if safe(ctx.api.call, "PATCH", f"orders/{oid}", json_body=changes, msg="Saving…"):
                ok_msg("Saved.")
                time.sleep(0.6)
                return
            pause()
            continue
        if k == "details":
            details = edit_details(details)
            changes["details"] = details
            continue
        lab = dict(fields)[k]
        if k == "fuel":
            v = choose("Fuel", [(x, x) for x in ("Petrol", "Diesel", "Electric", "Hybrid")] + [("__other", "Other…")])
            if v == "__other":
                v = ask("Fuel", default=s(o.get(k)))
            if v is None:
                continue
        else:
            v = ask(f"{lab}  (empty = keep, - = clear)", default=s(changes.get(k, o.get(k))))
            if v == "-":
                v = ""
        if v != s(o.get(k)):
            if k in ("brand", "model", "year", "part") and not v:
                warn_msg(f"{lab} is required — not cleared.")
                time.sleep(0.8)
                continue
            changes[k] = v
        else:
            changes.pop(k, None)


def edit_details(details: dict) -> dict:
    details = dict(details)
    while True:
        items = [(k, Text.assemble((f"{k:<18}", "bold"), (v, C3))) for k, v in details.items()]
        items += [("__add", Text("✚ Add a detail", style=f"bold {OK}")), ("__done", Text("✔ Done", style=f"bold {C1}"))]
        k = choose("Details (pick one to change / remove)", items, default=len(items) - 2)
        if k in (None, "__done"):
            return details
        if k == "__add":
            lab = ask("Label (e.g. Mileage)")
            if lab:
                details[lab] = ask(f"{lab}")
        else:
            v = ask(f"{k}  (- = remove)", default=details[k])
            if v == "-":
                details.pop(k)
            else:
                details[k] = v


def ask_customer_fields(cur: dict | None = None) -> dict:
    cur = cur or {}
    out = {}
    plat_items = [(p, p) for p in PLATFORMS] + [("__other", "Other…"), ("", Text("· none", style=MUTED))]
    out["handle"] = ask("Customer ID / username (e.g. ali.cars)", default=s(cur.get("handle")))
    if cur.get("platform") and cur["platform"] not in PLATFORMS:
        plat_items.insert(0, (cur["platform"], cur["platform"]))
    dflt = next((i for i, (v, _) in enumerate(plat_items) if v == cur.get("platform")), 0)
    p = choose("Platform", plat_items, default=dflt)
    if p == "__other":
        p = ask("Platform name")
    out["platform"] = p or ""
    out["name"] = ask("Name", default=s(cur.get("name")))
    out["phone"] = ask("Phone / WhatsApp", default=s(cur.get("phone")))
    out["location"] = ask("Location (Country / City)", default=s(cur.get("location")))
    return out


def order_customer(ctx: Ctx, o: dict) -> None:
    oid = o.get("id")
    c = o.get("customer") if isinstance(o.get("customer"), dict) else {}
    items = [("edit", "✎ Change customer details (another ID / platform moves the order to that customer)"),
             ("pick", "⌕ Move the order to an existing customer"),
             ("open", "☺ Open this customer"),
             ("back", Text("↩ Back", style=MUTED))]
    a = choose("Customer", items)
    if a == "edit":
        new = ask_customer_fields(c)
        diff = {k: v for k, v in new.items() if v != s(c.get(k))}
        notes = ask("Notes", default=s(c.get("notes")))
        if notes != s(c.get("notes")):
            diff["notes"] = notes
        if not diff:
            warn_msg("Nothing changed.")
            time.sleep(0.6)
            return
        if safe(ctx.api.call, "PATCH", f"orders/{oid}", json_body={"customer": diff}, msg="Saving…"):
            ok_msg("Customer saved.")
            time.sleep(0.6)
        else:
            pause()
    elif a == "pick":
        cid = pick_customer(ctx)
        if cid and safe(ctx.api.call, "PATCH", f"orders/{oid}", json_body={"customer_id": cid}, msg="Saving…"):
            ok_msg(f"Order moved to customer #{cid}.")
            time.sleep(0.6)
    elif a == "open":
        if c.get("id"):
            customer_screen(ctx, c["id"])
        else:
            warn_msg("This order has no visible customer.")
            time.sleep(0.8)


# ──────────────────────────────── photos ──────────────────────────────
def photos_menu(ctx: Ctx, o: dict) -> None:
    oid = o.get("id")
    photos = o.get("photos") or []
    while True:
        items = [("add", Text.assemble(("✚ ", OK), ("Add photos (files or links)", "bold"))),
                 ("replace", "⇄ Put a photo into a slot (replaces)"),
                 ("preview", "◉ Preview in the terminal"),
                 ("download", "⇩ Download all"),
                 ("remove", Text.assemble(("✖ ", BAD), ("Remove one photo", ""))),
                 ("clear", Text.assemble(("✖ ", BAD), ("Remove all photos", BAD))),
                 ("back", Text("↩ Back", style=MUTED))]
        a = choose(f"Photos of #{oid}  ({len(photos)}/8)", items)
        if a in (None, "back"):
            return
        if a == "preview":
            preview_photos(ctx, photos)
            pause()
            continue
        if a == "download":
            download_photos(ctx, photos, ctx.dl_dir / f"order-{oid}")
            pause()
            continue
        done = False
        if a == "add":
            free = 8 - len(photos)
            if free <= 0:
                warn_msg("All 8 slots are full — replace or remove one first.")
                time.sleep(1)
                continue
            files, urls = ask_photos(free)
            if urls:
                done = bool(safe(ctx.api.call, "PATCH", f"orders/{oid}", json_body={"photos": urls}, msg="Adding links…")) or done
            if files:
                r = safe(ctx.api.call, "POST", f"orders/{oid}/photos",
                         files=[file_tuple("photos[]", f) for f in files], msg=f"Uploading {len(files)} photo(s)…")
                done = bool(r) or done
        elif a == "replace":
            taken = {p.get("slot"): p for p in photos}
            slot = choose("Which slot?", [(i, Text.assemble((f"slot {i}  ", "bold"),
                                                            (f"photo #{taken[i]['id']}" if i in taken else "empty",
                                                             C1 if i in taken else MUTED))) for i in range(1, 9)])
            if slot is None:
                continue
            files, urls = ask_photos(1)
            if files:
                done = bool(safe(ctx.api.call, "POST", f"orders/{oid}/photos/{slot}",
                                 files=[file_tuple("photo", files[0])], msg="Uploading…"))
            elif urls:
                done = bool(safe(ctx.api.call, "PATCH", f"orders/{oid}",
                                 json_body={"photos": [{"slot": slot, "url": urls[0]}]}, msg="Saving…"))
        elif a == "remove":
            if not photos:
                warn_msg("No photos.")
                time.sleep(0.8)
                continue
            slot = choose("Remove which?", [(p.get("slot"), f"slot {p.get('slot')}  ·  photo #{p.get('id')}") for p in photos])
            if slot is not None:
                done = bool(safe(ctx.api.call, "DELETE", f"orders/{oid}/photos/{slot}", msg="Removing…"))
        elif a == "clear":
            if photos and confirm(f"Remove all {len(photos)} photos of #{oid}?", False):
                done = bool(safe(ctx.api.call, "DELETE", f"orders/{oid}/photos", msg="Removing…"))
        if done:
            ok_msg("Done.")
        try:
            photos = (ctx.api.get(f"orders/{oid}").get("data") or {}).get("photos") or []
        except APIError:
            pass
        pause()


# ──────────────────────────────── quote ───────────────────────────────
def quote_form(cur: dict | None = None) -> dict:
    cur = cur or {}
    q: dict = {}
    console.print(Text("  Enter keeps the shown value · - clears a number", style=MUTED))
    for k, lab in (("price", "Price"), ("shipping", "Shipping")):
        changed, v = ask_num(lab, cur.get(k))
        if changed:
            q[k] = v
    curs = CURRENCIES or []
    if curs:
        dflt = curs.index(cur["currency"]) if cur.get("currency") in curs else 0
        c = choose("Currency", [(x, Text(x, style=f"bold {OK}")) for x in curs], default=dflt)
        if c and c != cur.get("currency"):
            q["currency"] = c
    else:
        c = ask("Currency (e.g. AED)", default=s(cur.get("currency")))
        if c != s(cur.get("currency")):
            q["currency"] = c
    stocks = [("in_stock", stock_text("in_stock")), ("on_order", stock_text("on_order")),
              ("out", stock_text("out")), ("", Text("· not set", style=MUTED))]
    dflt = next((i for i, (v, _) in enumerate(stocks) if v == s(cur.get("stock"))), 0)
    st = choose("Stock", stocks, default=dflt)
    if st is not None and st != s(cur.get("stock")):
        q["stock"] = st
    changed, v = ask_num("Qty", cur.get("qty"), integer=True)
    if changed:
        q["qty"] = v
    for k, lab in (("delivery", "Delivery (e.g. 3-5 days)"), ("note", "Note")):
        v = ask(lab, default=s(cur.get(k)))
        if v != s(cur.get(k)):
            q[k] = v
    return q


def render_quote(q: dict | None) -> None:
    if not q:
        console.print(Panel(Align.center(Text("You have not given a price for this order yet.", style=MUTED)),
                            border_style=LINE, box=box.ROUNDED, title=f"[bold {OK}]▌My price", title_align="left"))
        return
    cur = s(q.get("currency"))
    total = q.get("total")
    if total in (None, "") and q.get("price") not in (None, ""):
        total = (to_num(q.get("price")) or 0) + (to_num(q.get("shipping")) or 0)
    big = Text(money(total, cur), style=f"bold {OK}")
    rows = kv([("Price", money(q.get("price"), cur)), ("Shipping", money(q.get("shipping"), cur)),
               ("Stock", stock_text(q.get("stock"))), ("Qty", q.get("qty")), ("Delivery", q.get("delivery")),
               ("Note", q.get("note")), ("Photos", f"{len(q.get('photos') or [])}/3"),
               ("Updated", when(q.get("updated_at")) if q.get("updated_at") else None)])
    console.print(Panel(Group(Text.assemble(("Total  ", MUTED), big), Text(), rows),
                        border_style=OK, box=box.ROUNDED, title=f"[bold {OK}]▌My price", title_align="left"))


def quote_screen(ctx: Ctx, oid) -> None:
    while True:
        try:
            res = busy("Loading my price…", ctx.api.get, f"orders/{oid}/quote")
            q = res.get("data")
            if isinstance(q, dict) and "quote" in q and isinstance(q.get("quote"), (dict, type(None))):
                q = q["quote"]
        except APIError as e:
            if e.code == "not_found" and "order" not in (e.payload.get("message") or "").lower():
                q = None
            else:
                show_error(e)
                pause()
                return
        crumb(ctx, "Orders", f"#{oid}", "My price")
        render_quote(q)
        photos = (q or {}).get("photos") or []
        items = [("set", Text.assemble(("$ ", OK), ("Set / change price, shipping & stock" if q else "Give my price", "bold")))]
        items += [("addph", "✚ Add photos of the offered part (up to 3)")]
        if photos:
            items += [("preview", "◉ Preview photos"), ("download", "⇩ Download photos"),
                      ("rmph", Text.assemble(("✖ ", BAD), ("Remove a photo", "")))]
        if q:
            items.append(("delete", Text.assemble(("✖ ", BAD), ("Delete my price (with photos)", BAD))))
        items.append(("back", Text("↩ Back", style=MUTED)))
        a = choose("My price", items)
        if a in (None, "back"):
            return
        if a == "set":
            body = quote_form(q or {})
            if not body:
                warn_msg("Nothing changed.")
                time.sleep(0.6)
                continue
            if safe(ctx.api.call, "PUT", f"orders/{oid}/quote", json_body=body, msg="Saving price…"):
                ok_msg("Price saved.")
                ctx.dash = None
                time.sleep(0.6)
            else:
                pause()
        elif a == "addph":
            free = 3 - len(photos)
            if free <= 0:
                warn_msg("3 photos already — remove one first.")
                time.sleep(1)
                continue
            files, urls = ask_photos(free)
            if urls:
                safe(ctx.api.call, "PUT", f"orders/{oid}/quote", json_body={"photos": urls}, msg="Adding links…")
            if files:
                safe(ctx.api.call, "POST", f"orders/{oid}/quote",
                     files=[file_tuple("photos[]", f) for f in files], msg=f"Uploading {len(files)} photo(s)…")
            pause()
        elif a == "preview":
            preview_photos(ctx, photos, route="quote-photos")
            pause()
        elif a == "download":
            download_photos(ctx, photos, ctx.dl_dir / f"order-{oid}-my-price", route="quote-photos")
            pause()
        elif a == "rmph":
            slot = choose("Remove which?", [(p.get("slot"), f"slot {p.get('slot')} · photo #{p.get('id')}") for p in photos]
                          + [("all", Text("all photos", style=BAD))])
            if slot is not None:
                body = {"remove_photos": "all" if slot == "all" else [slot]}
                if not safe(ctx.api.call, "PUT", f"orders/{oid}/quote", json_body=body, msg="Removing…"):
                    pause()
        elif a == "delete":
            if confirm("Delete your price for this order?", False):
                if safe(ctx.api.call, "DELETE", f"orders/{oid}/quote", msg="Deleting…"):
                    ok_msg("Deleted.")
                    ctx.dash = None
                    time.sleep(0.6)
                else:
                    pause()


# ────────────────────────────── new order ─────────────────────────────
def new_order(ctx: Ctx, customer_id=None) -> None:
    crumb(ctx, "New order")
    body: dict = {}
    section("1 · Customer")
    if customer_id:
        body["customer_id"] = customer_id
        ok_msg(f"Customer #{customer_id}")
    else:
        m = choose("Who is it for?", [
            ("new", Text.assemble(("✚ ", OK), ("Type the customer", "bold"), ("  (same ID + platform = same customer, never doubled)", MUTED))),
            ("search", "⌕ Pick an existing customer"),
            ("none", Text("· No customer", style=MUTED))])
        if m is None:
            return
        if m == "new":
            cust = {k: v for k, v in ask_customer_fields().items() if v}
            if cust:
                body["customer"] = cust
        elif m == "search":
            cid = pick_customer(ctx)
            if cid:
                body["customer_id"] = cid

    section("2 · Car")
    body["brand"] = ask("Brand (e.g. Kia)", required=True)
    body["model"] = ask("Model (e.g. Cerato)", required=True)
    body["year"] = ask("Year (e.g. 2019)", required=True)
    vin = ask("VIN / chassis No. (optional)")
    if vin:
        body["vin"] = vin.upper()
    if confirm("More car details? (fuel, type, origin, engine No., G.V.W.)", False):
        fuel = choose("Fuel", [(x, x) for x in ("Petrol", "Diesel", "Electric", "Hybrid")]
                      + [("__o", "Other…"), ("", Text("· skip", style=MUTED))])
        if fuel == "__o":
            fuel = ask("Fuel")
        if fuel:
            body["fuel"] = fuel
        for k, lab in (("vehicle_type", "Vehicle type (e.g. SUV / 4x4)"), ("origin", "Origin (e.g. Japan)"),
                       ("engine_no", "Engine No."), ("gvw", "G.V.W.")):
            v = ask(lab)
            if v:
                body[k] = v

    section("3 · Part")
    body["part"] = ask("Part needed (e.g. Right headlight)", required=True)
    if confirm("More part details? (part number, side, colour, info)", False):
        for k, lab in (("part_number", "Part number"), ("side", "Side / position (e.g. Front left)"),
                       ("colour", "Colour"), ("info", "Additional information")):
            v = ask(lab)
            if v:
                body[k] = v
    if confirm("Extra details? (Label: value — e.g. Mileage, Gearbox)", False):
        d = edit_details({})
        if d:
            body["details"] = d

    section("4 · Photos")
    files, urls = ask_photos(8)
    if urls:
        body["photos"] = urls

    section("5 · Extras")
    ref = ask("Your own ref (e.g. shop-10025 — stops doubles on retry; optional)")
    if ref:
        body["ref"] = ref
    if confirm("Status other than New?", False):
        st = choose("Status", [(x, status_label(x)) for x in STATUSES])
        if st:
            body["status"] = st
    if confirm("Add my own price now?", False):
        q = quote_form({})
        q = {k: v for k, v in q.items() if v not in (None, "")}
        if q:
            body["quote"] = q

    crumb(ctx, "New order", "Check & send")
    preview = {**body, "id": "new", "status": body.get("status", "New"),
               "photos": [{"slot": i + 1} for i in range(len(urls) + len(files))]}
    if "customer_id" in body:
        preview["customer"] = {"id": body["customer_id"]}
    render_order(preview)
    if files:
        console.print(Text.assemble(("  files to upload: ", MUTED), (", ".join(f.name for f in files), "bold")))
    if body.get("quote"):
        console.print(Text.assemble(("  my price: ", MUTED), (json.dumps(body["quote"], ensure_ascii=False), OK)))
    console.print()
    if not confirm("Send this order?", True):
        warn_msg("Not sent.")
        pause()
        return
    res = safe(ctx.api.call, "POST", "orders", json_body=body, msg="Sending order…")
    if not res:
        pause()
        return
    o = res.get("data") or {}
    oid = o.get("id")
    for w in (res.get("meta") or {}).get("warnings") or []:
        warn_msg(w if isinstance(w, str) else json.dumps(w, ensure_ascii=False))
    if files and oid:
        r = safe(ctx.api.call, "POST", f"orders/{oid}/photos",
                 files=[file_tuple("photos[]", f) for f in files], msg=f"Uploading {len(files)} photo(s)…")
        if r:
            for w in (r.get("meta") or {}).get("warnings") or []:
                warn_msg(w if isinstance(w, str) else json.dumps(w, ensure_ascii=False))
    ctx.dash = None
    console.print(Panel(Align.center(Text.assemble(("✔  Order ", f"bold {OK}"), (f"#{oid}", f"bold {C1}"),
                                                   ("  created", f"bold {OK}"))),
                        border_style=OK, box=box.DOUBLE, padding=(1, 2)))
    pause("press any key to open it")
    if oid:
        order_screen(ctx, oid)


# ────────────────────────────── customers ─────────────────────────────
def customers_table(rows: list) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, header_style=f"bold {C2}", expand=True, row_styles=["", "on #14141f"])
    t.add_column("#", style=f"bold {C1}", justify="right")
    t.add_column("Customer ID", style="bold", overflow="ellipsis", no_wrap=True)
    t.add_column("Platform", style=C3)
    t.add_column("Name", overflow="ellipsis", no_wrap=True)
    t.add_column("Phone", no_wrap=True)
    t.add_column("Location", overflow="ellipsis", no_wrap=True, style=MUTED)
    t.add_column("Orders", justify="right", style=OK)
    for c in rows:
        n = c.get("orders_count", c.get("order_count", c.get("orders")))
        if isinstance(n, list):
            n = len(n)
        t.add_row(s(c.get("id")), s(c.get("handle")), s(c.get("platform")), s(c.get("name")),
                  s(c.get("phone")), s(c.get("location")), s(n))
    return t


def cust_label(c: dict) -> Text:
    return Text.assemble((f"#{s(c.get('id')):<5} ", f"bold {C1}"), (s(c.get("handle")) or s(c.get("name")) or "?", "bold"),
                         (f"  {s(c.get('platform'))}", C3), (f"  {s(c.get('phone'))}", ""),
                         (f"  {s(c.get('location'))}", MUTED))


def pick_customer(ctx: Ctx):
    q = ask("Search customers (ID, name, phone…)")
    res = safe(ctx.api.call, "GET", "customers", params={"q": q, "per_page": 50}, msg="Searching…")
    if not res:
        pause()
        return None
    rows = res.get("data") or []
    if not rows:
        warn_msg("No customer found.")
        time.sleep(0.8)
        return None
    return choose("Pick the customer", [(c.get("id"), cust_label(c)) for c in rows])


def customers_screen(ctx: Ctx, q: str = "") -> None:
    page = 1
    while True:
        res = safe(ctx.api.call, "GET", "customers", params={"q": q, "page": page, "per_page": ctx.per_page},
                   msg="Loading customers…")
        if res is None:
            pause()
            return
        rows = res.get("data") or []
        meta = res.get("meta") or {}
        pages = int(meta.get("pages") or 1)
        crumb(ctx, "Customers")
        if q:
            console.print(Text.assemble((" search: ", f"#0a0a14 on {C2}"), (f"{q} ", f"bold #0a0a14 on {C2}")))
        console.print(customers_table(rows) if rows else
                      Panel(Align.center(Text("No customers.", style=MUTED)), border_style=LINE, box=box.ROUNDED))
        console.print(Text.assemble(("  page ", MUTED), (f"{page}/{pages}", f"bold {C1}"), ("   ·   ", MUTED),
                                    (s(meta.get("total", len(rows))), f"bold {C1}"), (" customers", MUTED)))
        items = []
        if rows:
            items.append(("open", Text("❯ Open a customer", style=f"bold {C1}")))
        items += [("search", "⌕ Search"), ("add", Text.assemble(("✚ ", OK), ("Add a customer", "")))]
        if page < pages:
            items.append(("next", "→ Next page"))
        if page > 1:
            items.append(("prev", "← Previous page"))
        if q:
            items.append(("clear", "⌫ Clear search"))
        items.append(("back", Text("↩ Back", style=MUTED)))
        a = choose("What now?", items)
        if a in (None, "back"):
            return
        if a == "open":
            cid = choose("Which customer?", [(c.get("id"), cust_label(c)) for c in rows])
            if cid is not None:
                customer_screen(ctx, cid)
        elif a == "search":
            q = ask("Search (ID, name, phone…)", default=q)
            page = 1
        elif a == "clear":
            q, page = "", 1
        elif a == "next":
            page += 1
        elif a == "prev":
            page -= 1
        elif a == "add":
            add_customer(ctx)


def add_customer(ctx: Ctx) -> None:
    section("New customer")
    body = {k: v for k, v in ask_customer_fields().items() if v}
    notes = ask("Notes")
    if notes:
        body["notes"] = notes
    if not body:
        return
    res = safe(ctx.api.call, "POST", "customers", json_body=body, msg="Saving…")
    if res:
        c = res.get("data") or {}
        created = (res.get("meta") or {}).get("created")
        if created is False:
            warn_msg(f"This customer already exists — #{c.get('id')}.")
        else:
            ok_msg(f"Customer #{c.get('id')} added.")
        pause()
        if c.get("id"):
            customer_screen(ctx, c["id"])
    else:
        pause()


def customer_screen(ctx: Ctx, cid) -> None:
    while True:
        try:
            res = busy(f"Loading customer #{cid}…", ctx.api.get, f"customers/{cid}")
        except APIError as e:
            show_error(e)
            pause()
            return
        c = res.get("data") or {}
        orders = c.get("orders") if isinstance(c.get("orders"), list) else []
        crumb(ctx, "Customers", f"#{cid}")
        head = Text.assemble((f" #{cid} ", f"bold #0a0a14 on {C3}"), "  ",
                             (s(c.get("handle")) or s(c.get("name")) or "?", "bold white"),
                             (f"  {s(c.get('platform'))}", C3))
        console.print(head)
        console.print(Panel(kv([(lab, c.get(k)) for k, lab in CUST_FIELDS] +
                               [("Created", when(c.get("created_at")) if c.get("created_at") else None)]),
                            border_style=LINE, box=box.ROUNDED, title=f"[bold {C3}]▌Customer", title_align="left"))
        if orders:
            console.print(orders_table(orders, title=f"{len(orders)} order(s)"))
        items = []
        if orders:
            items.append(("open", Text("❯ Open one of the orders", style=f"bold {C1}")))
        items += [("new", Text.assemble(("✚ ", OK), ("New order for this customer", "bold"))),
                  ("edit", "✎ Edit customer"),
                  ("delete", Text.assemble(("✖ ", BAD), ("Delete customer (only without orders)", BAD))),
                  ("json", Text("{} Raw JSON", style=MUTED)),
                  ("back", Text("↩ Back", style=MUTED))]
        a = choose(f"Customer #{cid}", items)
        if a in (None, "back"):
            return
        if a == "open":
            oid = choose("Which order?", [(o.get("id"), order_label(o)) for o in orders])
            if oid is not None:
                order_screen(ctx, oid)
        elif a == "new":
            new_order(ctx, customer_id=cid)
        elif a == "edit":
            new = ask_customer_fields(c)
            new["notes"] = ask("Notes", default=s(c.get("notes")))
            diff = {k: v for k, v in new.items() if v != s(c.get(k))}
            if diff:
                if safe(ctx.api.call, "PATCH", f"customers/{cid}", json_body=diff, msg="Saving…"):
                    ok_msg("Saved.")
                    time.sleep(0.6)
                else:
                    pause()
        elif a == "delete":
            if confirm(f"Delete customer #{cid}?", False):
                if safe(ctx.api.call, "DELETE", f"customers/{cid}", msg="Deleting…"):
                    ok_msg("Deleted.")
                    pause()
                    return
                pause()
        elif a == "json":
            console.print(Panel(RJSON(json.dumps(c, ensure_ascii=False)), border_style=LINE, box=box.ROUNDED))
            pause()


# ─────────────────────────────── find ─────────────────────────────────
def find(ctx: Ctx) -> None:
    v = ask("Order number (#152), your ref, or any text (car, VIN, part, customer)")
    if not v:
        return
    m = re.fullmatch(r"#?\s*(\d+)", v)
    if m:
        try:
            busy("Looking…", ctx.api.get, f"orders/{m.group(1)}")
            order_screen(ctx, int(m.group(1)))
            return
        except APIError as e:
            if e.code != "not_found":
                show_error(e)
                pause()
                return
    try:
        r = busy("Looking…", ctx.api.call, "GET", "orders", params={"ref": v, "per_page": 2})
        rows = r.get("data") or []
        if len(rows) == 1:
            order_screen(ctx, rows[0]["id"])
            return
    except APIError:
        pass
    orders_screen(ctx, {"q": v}, title=f"Search “{v}”")


# ─────────────────────────────── watch ────────────────────────────────
def watch(ctx: Ctx, interval: int | None = None) -> None:
    interval = int(interval or ctx.conf.get("watch_interval", 20))
    crumb(ctx, "Live watch")
    console.print(Panel(Text.assemble(("Watching for new and changed orders every ", MUTED), (f"{interval}s", f"bold {C1}"),
                                      (". New orders ring the bell", MUTED),
                                      (" and pop a desktop notification" if shutil.which("notify-send") else "", MUTED),
                                      (".   Ctrl+C to stop.", MUTED)),
                        border_style=C2, box=box.ROUNDED, title=f"[bold {C2}]◉ LIVE", title_align="left"))
    seen: dict = {}
    try:
        r = busy("Starting…", ctx.api.call, "GET", "orders", params={"sort": "updated", "per_page": 5})
    except APIError as e:
        show_error(e)
        pause()
        return
    rows = r.get("data") or []
    since = max((s(o.get("updated_at")) for o in rows), default="") or \
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for o in reversed(rows):
        seen[o.get("id")] = o.get("updated_at")
        event_line(o, "recent")
    console.print(Rule(Text(" live ", style=f"bold {C2}"), style=LINE))
    notify = shutil.which("notify-send")
    try:
        with console.status(Text("watching…", style=f"bold {C1}"), spinner="point", spinner_style=C2) as st:
            while True:
                for left in range(interval, 0, -1):
                    st.update(Text.assemble(("watching", f"bold {C1}"), (f" · next check in {left:>2}s · Ctrl+C to stop", MUTED)))
                    time.sleep(1)
                st.update(Text("checking…", style=f"bold {C2}"))
                try:
                    res = ctx.api.call("GET", "orders", params={"updated_since": since, "sort": "updated", "per_page": 100})
                except APIError as e:
                    warn_msg(f"{dt.datetime.now():%H:%M:%S}  {e.code}: {e}")
                    continue
                fresh = [o for o in (res.get("data") or []) if seen.get(o.get("id")) != o.get("updated_at")]
                for o in sorted(fresh, key=lambda x: s(x.get("updated_at"))):
                    is_new = o.get("id") not in seen and o.get("created_at") == o.get("updated_at")
                    seen[o.get("id")] = o.get("updated_at")
                    event_line(o, "new" if is_new else "changed")
                    if is_new:
                        sys.stdout.write("\a")
                        sys.stdout.flush()
                        if notify:
                            subprocess.Popen([notify, "-a", "ACP4U", f"New order #{o.get('id')}",
                                              f"{car_line(o)} — {s(o.get('part'))}"],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    since = max(since, s(o.get("updated_at")))
                ctx.dash = None
    except KeyboardInterrupt:
        console.print(Text("\n  ◼ stopped", style=MUTED))
        time.sleep(0.5)


def event_line(o: dict, kind: str) -> None:
    tag = {"new": Text(" NEW ", style=f"bold #0a0a14 on {C2}"),
           "changed": Text(" UPD ", style=f"bold #0a0a14 on {C1}"),
           "recent": Text(" ··· ", style=f"{MUTED} on #1c1c2a")}[kind]
    t = parse_t(o.get("updated_at"))
    line = Text.assemble((f"{t:%H:%M:%S}" if t else "--:--:--", MUTED), " ")
    line.append_text(tag)
    line.append(f" #{s(o.get('id')):<6}", style=f"bold {C1}")
    line.append_text(badge(o.get("status")))
    line.append(f"  {car_line(o)}", style="bold")
    line.append(f"  —  {s(o.get('part'))}", style=C2)
    c = cust_short(o.get("customer"))
    if c.plain != "—":
        line.append("   ")
        line.append_text(c)
    console.print(line, no_wrap=True, overflow="ellipsis")


# ─────────────────────────────── import ───────────────────────────────
def import_excel(ctx: Ctx, path: str | None = None) -> None:
    crumb(ctx, "Import Excel")
    console.print(Text("  An Excel file exported by the ACP4U Parts app. Rows imported before are skipped.", style=MUTED))
    p = path or ask("File (drag it here)")
    if not p:
        return
    try:
        p = shlex.split(p)[0]
    except (ValueError, IndexError):
        pass
    f = Path(re.sub(r"^file://", "", p)).expanduser()
    if not f.is_file():
        show_error(APIError(0, {"error": "no_file", "message": f"File not found: {f}"}))
        pause()
        return
    mime = mimetypes.guess_type(f.name)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    res = safe(ctx.api.call, "POST", "import", files=[("file", (f.name, f.read_bytes(), mime))], timeout=600,
               msg=f"Importing {f.name}…")
    if res:
        d = res.get("data")
        ok_msg("Import finished.")
        if isinstance(d, dict):
            console.print(Panel(kv([(k.replace("_", " ").capitalize(), v if not isinstance(v, (dict, list))
                                     else json.dumps(v, ensure_ascii=False)) for k, v in d.items()]),
                                border_style=OK, box=box.ROUNDED))
        elif d is not None:
            console.print(RJSON(json.dumps(d, ensure_ascii=False)))
        ctx.dash = None
    pause()


# ────────────────────────────── raw request ───────────────────────────
def raw_request(ctx: Ctx) -> None:
    crumb(ctx, "Raw API request")
    console.print(Text("  For anything special. The key is added for you.  e.g. route: orders/152  ·  params: status=New&per_page=5",
                       style=MUTED))
    m = choose("Method", [(x, Text(x, style=f"bold {c}")) for x, c in
                          (("GET", OK), ("POST", C1), ("PATCH", C3), ("PUT", C3), ("DELETE", BAD))])
    if not m:
        return
    route = ask("Route", default="info").lstrip("/")
    params = {}
    ps = ask("Query params (a=1&b=2, optional)")
    if ps:
        for part in ps.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip()] = v.strip()
    body = None
    if m != "GET":
        b = ask("JSON body (optional, one line)")
        if b:
            try:
                body = json.loads(b)
            except json.JSONDecodeError as e:
                show_error(APIError(0, {"error": "bad_json", "message": str(e)}))
                pause()
                return
    try:
        res = busy("Sending…", ctx.api.call, m, route, params=params, json_body=body)
        console.print(Panel(RJSON(json.dumps(res, ensure_ascii=False)), border_style=OK, box=box.ROUNDED,
                            title=f"[bold {OK}]✔ {m} {route}", title_align="left"))
    except APIError as e:
        console.print(Panel(RJSON(json.dumps(e.payload, ensure_ascii=False)), border_style=BAD, box=box.ROUNDED,
                            title=f"[bold {BAD}]✖ HTTP {e.status}", title_align="left"))
    pause()


# ─────────────────────────────── settings ─────────────────────────────
def settings(ctx: Ctx) -> Ctx | None:
    while True:
        crumb(ctx, "Settings")
        c = ctx.conf
        items = [("key", Text.assemble((f"{'API key':<18}", "bold"), (mask(ctx.api.key), C3), ("   change", MUTED))),
                 ("base", Text.assemble((f"{'Server':<18}", "bold"), (ctx.api.base, C1))),
                 ("per_page", Text.assemble((f"{'Rows per page':<18}", "bold"), (str(ctx.per_page), C1))),
                 ("watch", Text.assemble((f"{'Watch interval':<18}", "bold"), (f"{c.get('watch_interval', 20)}s", C1))),
                 ("dl", Text.assemble((f"{'Download folder':<18}", "bold"), (str(ctx.dl_dir), C1))),
                 ("proxy", Text.assemble((f"{'Proxy':<18}", "bold"), (proxy_label(effective_proxy(c)), C1))),
                 ("logout", Text.assemble(("✖ ", BAD), ("Forget the key (log out)", BAD))),
                 ("back", Text("↩ Back", style=MUTED))]
        a = choose("Settings", items)
        if a in (None, "back"):
            return ctx
        if a == "key":
            new = setup(dict(c))
            if new:
                ctx = new
        elif a == "base":
            b = ask("Server address (…/api.php)", default=ctx.api.base)
            if b and b != ctx.api.base:
                c["base"] = b
                save_conf(c)
                ctx = connect(c) or ctx
        elif a == "per_page":
            v = to_num(ask("Rows per page (1-100)", default=str(ctx.per_page)))
            if v:
                c["per_page"] = max(1, min(100, int(v)))
                save_conf(c)
        elif a == "watch":
            v = to_num(ask("Seconds between checks (min 5)", default=str(c.get("watch_interval", 20))))
            if v:
                c["watch_interval"] = max(5, int(v))
                save_conf(c)
        elif a == "dl":
            v = ask("Download folder", default=str(ctx.dl_dir))
            if v:
                c["download_dir"] = v
                save_conf(c)
        elif a == "proxy":
            if proxy_menu(c, ctx.api.base):
                ctx = Ctx(c) if not ctx.info else _reproxy(ctx)
        elif a == "logout":
            if confirm("Forget the saved key?", False):
                c.pop("key", None)
                save_conf(c)
                ok_msg("Key removed. Run acp4u again to connect.")
                return None


def pop_proxy_args(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    """Take --proxy URL / --proxy=URL / --no-proxy out of argv, wherever they are."""
    url, nop, out, i = None, False, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--proxy" and i + 1 < len(argv):
            url, i = argv[i + 1], i + 2
            continue
        if a.startswith("--proxy="):
            url = a.split("=", 1)[1]
        elif a == "--no-proxy":
            nop = True
        else:
            out.append(a)
        i += 1
    return url, nop, out


def apply_proxy_override(url: str | None, no_proxy: bool = False) -> bool:
    """--proxy / --no-proxy / ACP4U_PROXY → PROXY_OVERRIDE for this run. False on a bad URL."""
    global PROXY_OVERRIDE
    url = url or os.environ.get("ACP4U_PROXY")
    if no_proxy:
        PROXY_OVERRIDE = {"mode": "none"}
    elif url:
        try:
            PROXY_OVERRIDE = parse_proxy_url(url)
        except ValueError as e:
            show_error(APIError(0, {"error": "bad_proxy", "message": str(e)}))
            return False
    return True


def _reproxy(ctx: Ctx) -> Ctx:
    info = ctx.info
    ctx = Ctx(ctx.conf)
    ctx.info = info
    return ctx


# ─────────────────────────────── main menu ────────────────────────────
def main_menu(ctx: Ctx) -> None:
    while True:
        try:
            load_dash(ctx)
        except KeyboardInterrupt:
            pass
        console.clear()
        banner()
        status = Text.assemble(("● ", OK), (acct_name(ctx.info), "bold"))
        kn = key_name(ctx.info)
        if kn:
            status.append(f"  ·  key “{kn}”", style=MUTED)
        status.append(f"  ·  {re.sub(r'^https?://', '', ctx.api.base).split('/')[0]}", style=MUTED)
        if ctx.api.base.startswith("http://"):
            status.append("  ⚠ http", style=C3)
        if ctx.api.proxy.get("mode") == "custom" or (ctx.api.proxy.get("mode") == "system" and env_proxy()):
            status.append(f"  ·  ⛨ {proxy_label(ctx.api.proxy)}", style=C1)
        console.print(Align.center(status))
        console.print()
        render_dash(ctx)
        console.print()
        items = [
            ("orders", Text.assemble(("▤  ", C1), ("Orders", "bold"), ("      browse · filter · open", MUTED))),
            ("new", Text.assemble(("✚  ", OK), ("New order", "bold"), ("   customer · car · part · photos", MUTED))),
            ("find", Text.assemble(("⌕  ", C3), ("Find", "bold"), ("        #id · ref · VIN · text", MUTED))),
            ("waiting", Text.assemble(("$  ", C2), ("Waiting for my price", "bold"))),
            ("customers", Text.assemble(("☺  ", C3), ("Customers", "bold"))),
            ("watch", Text.assemble(("◉  ", C2), ("Live watch", "bold"), ("  new orders as they arrive", MUTED))),
            ("import", Text.assemble(("⇪  ", C1), ("Import Excel", "bold"))),
            ("info", Text.assemble(("⚿  ", C3), ("Key & permissions", "bold"))),
            ("raw", Text.assemble(("⌘  ", MUTED), ("Raw API request", ""))),
            ("settings", Text.assemble(("⚙  ", MUTED), ("Settings", ""))),
            ("refresh", Text.assemble(("⟳  ", MUTED), ("Refresh dashboard", ""))),
            ("quit", Text.assemble(("✖  ", BAD), ("Quit", ""))),
        ]
        try:
            a = choose("Main menu", items)
        except KeyboardInterrupt:
            a = "quit"
        if a in (None, "quit"):
            console.print(Align.center(grad_text(["  see you  ✦  "])))
            return
        try:
            if a == "orders":
                orders_screen(ctx)
            elif a == "new":
                new_order(ctx)
            elif a == "find":
                find(ctx)
            elif a == "waiting":
                orders_screen(ctx, {"mine": "waiting"}, title="Waiting for my price")
            elif a == "customers":
                customers_screen(ctx)
            elif a == "watch":
                watch(ctx)
            elif a == "import":
                import_excel(ctx)
            elif a == "info":
                info_screen(ctx)
            elif a == "raw":
                raw_request(ctx)
            elif a == "settings":
                new = settings(ctx)
                if new is None:
                    return
                ctx = new
            elif a == "refresh":
                load_dash(ctx, force=True)
        except KeyboardInterrupt:
            console.print(Text("\n  ↩ cancelled", style=MUTED))
            time.sleep(0.5)


# ─────────────────────────────────── cli ──────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(prog="acp4u", description="ACP4U Parts — colourful terminal client")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("login", help="set / replace the API key")
    sub.add_parser("logout", help="forget the saved key")
    sub.add_parser("info", help="key, permissions, statuses, currencies, limits")
    p = sub.add_parser("orders", help="list orders")
    p.add_argument("-s", "--status")
    p.add_argument("-q", "--query")
    p.add_argument("--mine", choices=["waiting", "priced"])
    p.add_argument("--brand")
    p = sub.add_parser("order", help="open one order")
    p.add_argument("id")
    sub.add_parser("new", help="add an order")
    p = sub.add_parser("customers", help="list customers")
    p.add_argument("-q", "--query", default="")
    p = sub.add_parser("watch", help="live: new and changed orders")
    p.add_argument("-i", "--interval", type=int)
    p = sub.add_parser("import", help="import an Excel file")
    p.add_argument("file")
    sub.add_parser("version")
    sub.add_parser("proxy", help="proxy settings (SOCKS5 / SOCKS4 / HTTP)")
    ap.add_argument("--proxy", metavar="URL", help="proxy for this run, e.g. socks5h://user:pass@127.0.0.1:1080")
    ap.add_argument("--no-proxy", action="store_true", help="connect directly for this run")
    purl, pnone, argv = pop_proxy_args(sys.argv[1:])
    args = ap.parse_args(argv)

    if args.cmd == "version":
        console.print(f"acp4u {VERSION}")
        return
    if not apply_proxy_override(purl, pnone):
        return
    conf = load_conf()
    if args.cmd == "proxy":
        proxy_menu(conf)
        return
    if args.cmd == "logout":
        conf.pop("key", None)
        save_conf(conf)
        ok_msg("Key forgotten.")
        return
    if args.cmd == "login":
        ctx = setup(conf)
        if ctx:
            main_menu(ctx)
        return
    ctx = connect(conf)
    if not ctx:
        return
    cmd = args.cmd
    if cmd is None:
        main_menu(ctx)
    elif cmd == "info":
        info_screen(ctx)
    elif cmd == "orders":
        f = {k: v for k, v in (("status", args.status), ("q", args.query), ("mine", args.mine),
                               ("brand", args.brand)) if v}
        orders_screen(ctx, f)
    elif cmd == "order":
        order_screen(ctx, args.id.lstrip("#"))
    elif cmd == "new":
        new_order(ctx)
    elif cmd == "customers":
        customers_screen(ctx, args.query)
    elif cmd == "watch":
        watch(ctx, args.interval)
    elif cmd == "import":
        import_excel(ctx, args.file)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.stdout.write("\x1b[?25h")
        console.print(Text("\n  bye ✦", style=MUTED))
