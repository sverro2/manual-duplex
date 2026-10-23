#!/bin/sh
# Adds Manual Duplex to the application menu and to "Open with" for PDFs.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

python3 -c "import pypdf" 2>/dev/null || pip install --user pypdf
[ -f "$DIR/assets/test-page.pdf" ] || typst compile "$DIR/assets/test-page.typ" "$DIR/assets/test-page.pdf"

mkdir -p "$APPS"
sed "s|@DIR@|$DIR|g" "$DIR/nl.audiospire.ManualDuplex.desktop.in" > "$APPS/nl.audiospire.ManualDuplex.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/manual_duplex.py" "$HOME/.local/bin/manual-duplex"

echo "Installed: look for \"Manual Duplex\" in your application menu,"
echo "or run \"manual-duplex --help\" in a terminal."
