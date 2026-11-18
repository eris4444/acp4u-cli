#!/usr/bin/env bash
# ACP4U Parts — installer / updater / uninstaller.
#   install:    curl -fsSL https://raw.githubusercontent.com/eris4444/acp4u-cli/main/install.sh | bash
#   uninstall:  curl -fsSL https://raw.githubusercontent.com/eris4444/acp4u-cli/main/install.sh | bash -s -- --uninstall
set -euo pipefail

REPO="${ACP4U_REPO:-eris4444/acp4u-cli}"
BRANCH="${ACP4U_BRANCH:-main}"
APP_DIR="${ACP4U_HOME:-$HOME/.local/share/acp4u}"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
LAUNCHER="$BIN_DIR/acp4u"
DESKTOP="$APPS_DIR/acp4u.desktop"

if [ -t 1 ]; then C1=$'\e[38;2;0;215;255m'; C2=$'\e[38;2;255;95;215m'; OK=$'\e[38;2;95;255;135m'; BAD=$'\e[38;2;255;95;95m'; DIM=$'\e[2m'; B=$'\e[1m'; R=$'\e[0m'
else C1= C2= OK= BAD= DIM= B= R=; fi
say()  { printf '%s\n' "${C1}▸${R} $*"; }
good() { printf '%s\n' "${OK}✔${R} $*"; }
warn() { printf '%s\n' "${C2}⚠${R} $*" >&2; }
die()  { printf '%s\n' "${BAD}✖ $*${R}" >&2; exit 1; }

banner() {
  printf '%s\n' "${B}${C1}  ACP4U ${C2}PARTS${R}${DIM}  — terminal app for the ACP4U Parts API${R}" ""
}

if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "uninstall" ]; then
  banner
  rm -rf "$APP_DIR" "$LAUNCHER" "$DESKTOP"
  rm -f "$HOME/.local/share/konsole/ACP4U.profile" "$HOME/.local/share/konsole/ACP4U.colorscheme"
  good "ACP4U removed."
  say "Your saved key and settings were kept in ${B}~/.config/acp4u${R} — delete that folder too if you want them gone."
  exit 0
fi

banner
[ "$(id -u)" -ne 0 ] || warn "Running as root — it will install for root only. Run it as your normal user instead."

# ── python ────────────────────────────────────────────────────────────
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.10 or newer is needed.  Debian / Ubuntu / Kali:  sudo apt install python3 python3-venv"
say "Python $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "The Python venv module is missing.  Debian / Ubuntu / Kali:  sudo apt install python3-venv"

# ── get the code ──────────────────────────────────────────────────────
mkdir -p "$APP_DIR" "$BIN_DIR"
if [ -n "${ACP4U_SOURCE:-}" ]; then
  say "Copying from ${ACP4U_SOURCE} …"
  ( cd "$ACP4U_SOURCE" && tar --exclude=.venv --exclude=__pycache__ --exclude=.git -cf - . ) | tar -xf - -C "$APP_DIR"
else
  command -v curl >/dev/null 2>&1 || die "curl is needed.  sudo apt install curl"
  say "Downloading ${B}${REPO}${R} (${BRANCH}) …"
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" | tar -xz --strip-components=1 -C "$APP_DIR" \
    || die "Could not download https://github.com/${REPO} — check your internet connection (or proxy)."
fi
[ -f "$APP_DIR/acp4u_tui.py" ] || die "The download looks incomplete (acp4u_tui.py is missing)."

# ── python packages (kept in the app's own folder, system Python is not touched) ──
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  say "Creating a private Python environment …"
  "$PY" -m venv "$APP_DIR/.venv"
fi
PIP="$APP_DIR/.venv/bin/python -m pip --disable-pip-version-check -q"
say "Installing packages (textual, rich, requests, pillow, numpy, pysocks) …"
if ! $PIP install -r "$APP_DIR/requirements.txt"; then
  warn "pip failed — retrying without a SOCKS/HTTP proxy from the environment …"
  env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
    $PIP install -r "$APP_DIR/requirements.txt" \
    || die "Could not install the Python packages. If you use a proxy, make sure pip can reach pypi.org."
fi
# optional: real-pixel photos in Kitty / Konsole / WezTerm / foot (needs Python 3.12+)
$PIP install textual-image >/dev/null 2>&1 \
  && good "Real-pixel photo support installed" \
  || say "${DIM}(real-pixel photo support skipped — needs Python 3.12+; photos will use detailed blocks)${R}"

# ── command + menu entry ──────────────────────────────────────────────
cat > "$LAUNCHER" <<LAUNCH
#!/bin/sh
# ACP4U Parts — installed by install.sh
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/acp4u_tui.py" "\$@"
LAUNCH
chmod +x "$LAUNCHER"

mkdir -p "$APPS_DIR"
cat > "$DESKTOP" <<ENTRY
[Desktop Entry]
Type=Application
Name=ACP4U Parts
GenericName=Parts orders
Comment=Orders, customers, prices and sharp photos
Exec=$LAUNCHER hd
Icon=$APP_DIR/icon.png
Terminal=false
Categories=Office;Network;
Keywords=acp4u;orders;parts;
ENTRY
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true

# ── check it starts ───────────────────────────────────────────────────
"$LAUNCHER" version >/dev/null 2>&1 || die "Installed, but the app does not start — run  $LAUNCHER version  to see the error."
good "Installed  ${DIM}($("$LAUNCHER" version))${R}"

printf '\n'
case ":$PATH:" in
  *":$BIN_DIR:"*) say "Start it:  ${B}${C1}acp4u${R}   ${DIM}(you paste your API key once)${R}" ;;
  *) warn "$BIN_DIR is not in your PATH. Add it, then open a new terminal:"
     printf '    %s\n' "echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc   # or ~/.zshrc"
     say "Start it now:  ${B}${C1}$LAUNCHER${R}" ;;
esac
if command -v kitty >/dev/null 2>&1 || command -v konsole >/dev/null 2>&1 || command -v wezterm >/dev/null 2>&1 || command -v foot >/dev/null 2>&1; then
  say "Photos in full quality:  ${B}acp4u hd${R}   ${DIM}(or press F2 inside the app)${R}"
else
  say "${DIM}Tip: install Kitty (sudo apt install kitty) for real-pixel photos — then run  acp4u hd${R}"
fi
say "Update later:  ${B}acp4u update${R}     Remove:  ${B}acp4u uninstall${R}"
