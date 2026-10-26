#!/usr/bin/env bash
# Island for Linux, from source: system packages, a virtualenv, the `island` command and a menu entry.
#   bash linux/install.sh            install
#   bash linux/install.sh --no-sys   skip system packages (no sudo)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/island/venv"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

if [[ "${1:-}" != "--no-sys" ]]; then
  # Qt's xcb plugin, zbar for QR, and the optional helpers the features use
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-venv libxcb-cursor0 libzbar0 xdotool playerctl pulseaudio-utils \
      tesseract-ocr tesseract-ocr-rus brightnessctl x11-utils || true
  elif command -v dnf >/dev/null; then
    sudo dnf install -y python3 xcb-util-cursor zbar xdotool playerctl pulseaudio-utils \
      tesseract tesseract-langpack-rus brightnessctl xprop || true
  elif command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm python xcb-util-cursor zbar xdotool playerctl libpulse \
      tesseract tesseract-data-rus brightnessctl xorg-xprop || true
  else
    echo "Install by hand: libxcb-cursor, zbar, xdotool, playerctl, pactl, tesseract (optional)."
  fi
fi

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"

mkdir -p "$BIN" "$APPS"
cat > "$BIN/island" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$ROOT/main.py" "\$@"
EOF
chmod +x "$BIN/island"

cat > "$APPS/island.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Island
Comment=Dynamic Island for the desktop
Exec=$BIN/island
Icon=$ROOT/linux/island.svg
Terminal=false
Categories=Utility;
EOF

echo
echo "Done. Run: island          (make sure ~/.local/bin is in PATH)"
echo "Shortcuts: bind e.g. Alt+Space to 'island --action command' in your desktop's keyboard settings."
