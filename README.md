# openknob

Linux daemon and overlay UI for the **ASUS ProArt Studiobook hardware dial**.

> ⚠️ **Heads up** — this project is heavily vibecoded. It works great for me on
> **Zorin OS**, but there are no guarantees it will work out of the box on your setup. 

---

## Hardware compatibility

This project targets the physical rotary dial built into ASUS ProArt Studiobook laptops.
It communicates directly with the dial over the Linux `hidraw` interface.

**Confirmed working**

| Model | HID ID |
|---|---|
| ProArt Studiobook H7600Z | `0B05:0220` |

**Likely compatible** — any ProArt Studiobook whose dial exposes HID ID `0B05:0220`
in `/sys/class/hidraw/` (Pro 16, H7604, W7600, W5600, …).
Run `openknob-hid-probe` to verify your device sends the same 4-byte packet format.

**Not supported** — USB/Bluetooth external dials, ASUS Dial on non-Studiobook hardware,
or any device that does not match the HID ID above.

**OS / desktop requirements**

- Linux kernel with `hidraw` support (standard on all major distros)
- GNOME on Wayland or X11
- User must belong to the `input` group (handled by `install.sh`)

---

## How the stack works

```
┌─────────────────────────────────────────────────────┐
│              ASUS ProArt Dial (I2C HID)              │
│         HID ID 0B05:0220 — 4-byte reports            │
│  01 00 [Δlo][Δhi]  rotate   │  01 01 00 00  press   │
└──────────────────┬──────────────────────────────────┘
                   │ /dev/hidrawN  (Linux hidraw driver)
                   ▼
┌──────────────────────────────────────────────────────┐
│                 openknob-daemon                       │
│  • Reads raw HID packets via os.read()               │
│  • Decodes: rotate_cw / rotate_ccw / press           │
│  • Looks up the active profile (per-app matching)    │
│  • Executes the bound action (volume, brightness…)   │
│  • Broadcasts a text event line over a Unix socket   │
└──────────────────┬───────────────────────────────────┘
                   │ /run/user/$UID/openknob.sock
          ┌────────┴────────┐
          ▼                 ▼
┌──────────────────┐  ┌──────────────────────────────┐
│ openknob-overlay │  │       openknob-config         │
│                  │  │                               │
│ PyQt6 frameless  │  │ GTK4 / Adwaita settings UI    │
│ window drawn     │  │ Edit profiles, functions,     │
│ with QPainter.   │  │ actions, colors, position.    │
│ Fades in on      │  │ Writes ~/.config/openknob/    │
│ dial events,     │  │ profiles/*.json               │
│ auto-hides       │  │                               │
│ after 2 s.       │  └──────────────────────────────┘
└──────────────────┘
```

### Daemon

The daemon is the only process that needs access to `/dev/hidrawN`.
It opens the device read-only and uses `select()` to wait for reports.
Each 4-byte report is decoded into one of three events:

| Raw bytes | Event |
|---|---|
| `01 00 [Δlo][Δhi]` (Δ ≠ 0) | `rotate_cw` or `rotate_ccw` |
| `01 01 00 00` | `press` |
| `01 00 00 00` | `release` (ignored by overlay) |

After decoding, the daemon:
1. Resolves the active profile via window-title matching (`WindowWatcher`)
2. Executes the bound action (`volume:up`, `brightness:down`, custom shell command…)
3. Broadcasts a tab-separated event line to all connected clients over the Unix socket

### Overlay

The overlay is a transparent, always-on-top `QWidget` with `WA_TransparentForMouseEvents`.
It connects to the daemon socket and polls it every 50 ms.
On each event it updates its internal state and fades in; a 2-second timer triggers the fade-out.
All rendering is done in `paintEvent()` with `QPainter` — no CSS, no QSS.

### Profiles

Profiles live in `~/.config/openknob/profiles/*.json`.
Each profile declares:
- an app-name regex (`match`) for automatic switching when the focused window changes
- a list of *functions*, each with `rotate_cw`, `rotate_ccw`, and `press` actions
- per-function display flags (`show_ring`, `show_percentage`)

The daemon hot-reloads profiles from disk every 2 seconds.

---

## Installation

```bash
bash install.sh
```

Then log out and back in so the `input` group takes effect.

---

## Commands

### Daemon

```bash
systemctl --user start openknob-daemon
systemctl --user stop openknob-daemon
systemctl --user restart openknob-daemon
systemctl --user status openknob-daemon
journalctl --user -u openknob-daemon -f

# Verbose mode — prints every decoded HID event and broadcast line
openknob-daemon --verbose
```

### Overlay

The overlay starts automatically at login via `~/.config/autostart/openknob-overlay.desktop`.

```bash
# Start manually (from a native terminal, not the VS Code snap terminal)
env -u LD_LIBRARY_PATH openknob-overlay

# Kill
pkill -f openknob-overlay

# Verify it is connected to the daemon
ss -xp | grep openknob.sock
```

> **VS Code snap terminal** — the snap injects `GTK_PATH` and `GDK_PIXBUF_MODULEDIR`
> pointing at snap-bundled libraries that are ABI-incompatible with the system glibc.
> Launching the overlay from that terminal will crash with a symbol lookup error.
> Use a native GNOME terminal or rely on the autostart entry.

### Config UI

```bash
openknob-config
# or launch "openknob" from the GNOME application grid
```

### HID diagnostics

```bash
# Print every raw packet the dial sends
openknob-hid-probe
```

---

## File layout

```
~/.config/openknob/
├── profiles/
│   └── default.json      # user profiles (hot-reloaded by daemon)
└── settings.json         # overlay position, size, colors (hot-reloaded)
```
