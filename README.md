# ACP4U Parts — terminal app

A colourful full-screen terminal app (mouse + keyboard) for the **ACP4U Parts API**: orders, customers, photos, prices, live notifications — everything the API can do, from your terminal.

## Install — one line

```bash
curl -fsSL https://raw.githubusercontent.com/eris4444/acp4u-cli/main/install.sh | bash
```

Then run:

```bash
acp4u
```

The first time, paste your API key (it starts with `acp4u_`). It is checked and saved to `~/.config/acp4u/config.json` (readable only by your user) — you are never asked again.

| | |
|---|---|
| Update | `acp4u update` |
| Remove | `acp4u uninstall` (your saved key stays in `~/.config/acp4u`) |
| Needs | Linux / macOS, Python 3.10+ with `venv` (`sudo apt install python3 python3-venv`), `curl` |

Everything is installed into `~/.local/share/acp4u` in its own virtual environment — your system Python is not touched. If `acp4u` is not found, add `~/.local/bin` to your `PATH`.

## What it does

- **Dashboard** — a card per status with counts, a status chart, recently updated orders
- **Orders** — live search, filters (status, brand, source, dates, "waiting for my price" …), paging, CSV export, a preview pane with the first photo and all details
- **Order page** — tabs: *Overview* · *Photos* (big gallery) · *Prices* (every account's price **with its photos**) · *All data* (every field the API sends + raw JSON)
- **Photos** — full-screen viewer with zoom and pan (`+` `−` mouse wheel, `h j k l`), download, replace, remove, per-photo details
- **Create / edit orders** — one form, auto-suggestions, required-field check, photo picker with previews, own price, extra details
- **Customers**, **own price + shipping + stock + photos**, **Excel import**, **API console** for any raw call
- **Live watch** — new orders ring a bell and pop up a desktop notification
- **Command palette** (`Ctrl+P`), go-to-order (`g`), colour themes
- **Proxy** — SOCKS5 (remote DNS) / SOCKS5 / SOCKS4a / SOCKS4 / HTTP / HTTPS, with username and password, presets for Tor / v2ray / Clash, a *Test* button and *My exit IP*. One run only: `acp4u --proxy socks5h://user:pass@127.0.0.1:1080` · `acp4u --no-proxy`

## Sharp photos

Most terminals can only draw text, so photos are drawn with coloured blocks (the detailed mode picks the best block shape for every cell). For **real pixels** use a terminal that supports Sixel or the Kitty graphics protocol — **Kitty, Konsole, WezTerm, foot, Ghostty, mlterm**:

```bash
acp4u hd        # opens the app in such a terminal (or press F2 inside the app)
```

If you start the app directly inside one of these terminals it is detected automatically. Pick the mode by hand in *Settings › Photos*.

## Keys

| Key | |
|---|---|
| `1`–`9` | sidebar pages |
| `n` · `g` · `F1` · `F2` · `Ctrl+P` · `Ctrl+Q` | new order · go to order · keys · sharp window · commands · quit |
| `Enter` / click | open |
| `/` · `[` `]` · `s` · `v` · `x` · `r` | search · pages · status · preview · CSV · refresh |
| order page: `1`–`4` `s e c p m w j` | tabs · status · edit · customer · add photos · my price · download · JSON |
| viewer: `← →` `+ − 0` `h j k l` `d o i` | browse · zoom · move · download · open outside · info |
| forms: `Ctrl+S` | save |

## Command line

```bash
acp4u                      # the app
acp4u order 152            # open an order
acp4u orders -s New        # list orders with a status
acp4u new                  # new order form
acp4u watch                # live watch
acp4u import file.xlsx     # import an Excel file
acp4u login | logout       # change / forget the key
acp4u classic              # the older line-by-line menu version
```

Environment: `ACP4U_KEY`, `ACP4U_BASE` (server address), `ACP4U_PROXY`, `ACP4U_IMAGES` (`auto` · `sixel` · `kitty` · `detailed` · `blocks`).

## Security

- The key is stored with mode `600` in `~/.config/acp4u/`; it is shown masked everywhere in the app.
- Give a key only the permissions it needs on the site's **API** page. Errors name the missing permission.
- The app uses `https://`; it falls back to `http://` only after `https` could not connect and then tells you.

---

## فارسی

یک برنامه‌ی تمام‌صفحه برای ترمینال، برای کار با API سایت ACP4U Parts: سفارش‌ها، مشتری‌ها، عکس‌ها و قیمت‌ها.

**نصب با یک خط:**

```bash
curl -fsSL https://raw.githubusercontent.com/eris4444/acp4u-cli/main/install.sh | bash
```

بعد فقط بزن `acp4u` و **یک بار** کلید API را paste کن؛ دیگر نمی‌پرسد.

- به‌روزرسانی: `acp4u update` · حذف: `acp4u uninstall`
- عکس با کیفیت کامل (پیکسل واقعی): `acp4u hd` یا داخل برنامه `F2` — در Kitty / Konsole / WezTerm / foot
- پروکسی: تنظیمات › Proxy (SOCKS5 / SOCKS4 / HTTP با نام کاربری و رمز)
- کلیدها: `F1` همه‌ی کلیدها را نشان می‌دهد؛ `Ctrl+P` پنجره‌ی دستورها
