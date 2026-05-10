#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== openknob installer ==="
echo ""

# ── 1. Python package ─────────────────────────────────────────────────────────
echo "[1/6] Installing Python package..."
python3 -m pip install -e "$DIR" --quiet --break-system-packages
echo "      openknob-daemon, openknob-overlay, openknob-config installed to ~/.local/bin"

# ── 2. udev rule ──────────────────────────────────────────────────────────────
echo "[2/6] Installing udev rule (requires sudo)..."
sudo tee /etc/udev/rules.d/99-openknob.rules > /dev/null <<'EOF'
KERNEL=="hidraw*", KERNELS=="0018:0B05:0220.*", SUBSYSTEMS=="hid", MODE="0664", GROUP="input"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "      /etc/udev/rules.d/99-openknob.rules written"

# ── 3. input group ────────────────────────────────────────────────────────────
echo "[3/6] Adding $USER to input group (requires sudo)..."
sudo usermod -aG input "$USER"
echo "      Done — takes effect on next login"

# ── 4. Systemd user service (daemon) ─────────────────────────────────────────
echo "[4/6] Enabling systemd user service..."
mkdir -p ~/.config/systemd/user
cp "$DIR/openknob-daemon.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable openknob-daemon.service
echo "      openknob-daemon.service enabled"

# ── 5. Autostart (overlay) ────────────────────────────────────────────────────
echo "[5/6] Installing overlay autostart..."
mkdir -p ~/.config/autostart
sed "s|Exec=openknob-overlay|Exec=env -u LD_LIBRARY_PATH $HOME/.local/bin/openknob-overlay|" \
    "$DIR/openknob-overlay.desktop" > ~/.config/autostart/openknob-overlay.desktop
echo "      ~/.config/autostart/openknob-overlay.desktop installed"

# ── 6. App launcher entry ─────────────────────────────────────────────────────
echo "[6/6] Installing app launcher entry..."
mkdir -p ~/.local/share/applications
cp "$DIR/openknob-config.desktop" ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
echo "      openknob appears in GNOME app launcher"

# ── Default profile ───────────────────────────────────────────────────────────
mkdir -p ~/.config/openknob/profiles
if [ ! -f ~/.config/openknob/profiles/default.json ]; then
    cp "$DIR/profiles/default.json" ~/.config/openknob/profiles/
    echo "      Default profile installed"
fi

echo ""
echo "✓ Installation complete!"
echo ""
echo "IMPORTANT: Log out and back in so the input group takes effect."
echo "           After that, openknob starts automatically at login."
echo ""
echo "To start now without logging out:"
echo "  newgrp input   # activate group in current shell"
echo "  systemctl --user start openknob-daemon"
