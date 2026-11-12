#!/bin/bash
# Render the README diagrams: docs/diagrams/<name>.html -> docs/<name>.png, at 2x, trimmed.
# Needs Google Chrome and Python with Pillow. Usage: docs/diagrams/render.sh [name ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(dirname "$HERE")"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  names=()
  for f in "$HERE"/*.html; do names+=("$(basename "$f" .html)"); done
fi
for name in "${names[@]}"; do
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1180,4000 --screenshot="$OUT/$name.png" "file://$HERE/$name.html" 2>/dev/null
  python3 - "$OUT/$name.png" <<'EOF'
import sys
from PIL import Image, ImageChops
path = sys.argv[1]
im = Image.open(path).convert("RGB")
box = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
if box:
    im.crop((0, 0, im.width, min(im.height, box[3] + 60))).save(path, optimize=True)
EOF
  echo "rendered docs/$name.png"
done
