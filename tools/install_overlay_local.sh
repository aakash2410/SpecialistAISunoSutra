#!/usr/bin/env bash
# One-command local deploy of the Specialist overlay on the device (no Ansible).
#
# Syncs the latest overlay code into BOTH interpreters (your system python3 that
# you test with, and the venv the systemd service boots from), deploys the corpus
# + ONNX model to /opt, rebuilds the index with the neural embedder if the model
# is present, and runs the health check.
#
#   ./tools/install_overlay_local.sh
#
# Overrides:
#   SPECIALIST_VENV_PY=/path/to/venv/bin/python   (service interpreter)
#   SPECIALIST_CORPUS_DIR=/opt/specialist/corpus

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/python/pocketinfer"
CORPUS_DST="${SPECIALIST_CORPUS_DIR:-/opt/specialist/corpus}"
MODEL_DST="/opt/specialist/models"
VENV_PY="${SPECIALIST_VENV_PY:-/home/ubuntu/pocket-infer-sw/python/venv/bin/python}"
MODEL_SRC="$REPO/local/models/multilingual-e5-small-onnx"

copy_overlay () {  # $1 = target pocketinfer package dir
  local pkg="$1"
  [ -d "$pkg" ] || { echo "  ! no pocketinfer at $pkg — skipped"; return; }
  cp "$SRC"/models/embed.py "$SRC"/models/ocr.py "$pkg"/models/ || return
  mkdir -p "$pkg"/specialist && cp "$SRC"/specialist/*.py "$pkg"/specialist/
  cp "$SRC"/applications/specialist_app.py "$pkg"/applications/
  echo "  ✓ overlay -> $pkg"
}

echo "== 1. overlay code -> interpreters =="
SYS_PKG=$(python3 -c "import pocketinfer,os;print(os.path.dirname(pocketinfer.__file__))" 2>/dev/null)
[ -n "$SYS_PKG" ] && copy_overlay "$SYS_PKG" || echo "  ! system python3 has no pocketinfer"
if [ -x "$VENV_PY" ]; then
  VENV_PKG=$("$VENV_PY" -c "import pocketinfer,os;print(os.path.dirname(pocketinfer.__file__))" 2>/dev/null)
  [ -n "$VENV_PKG" ] && [ "$VENV_PKG" != "$SYS_PKG" ] && copy_overlay "$VENV_PKG"
else
  echo "  ! venv python not found at $VENV_PY — service interpreter NOT updated"
  echo "    (set SPECIALIST_VENV_PY; the app boots from the venv, so it needs the overlay too)"
fi

echo "== 2. ONNX model -> $MODEL_DST =="
if [ -d "$MODEL_SRC" ]; then
  sudo mkdir -p "$MODEL_DST"
  sudo cp -r "$MODEL_SRC" "$MODEL_DST"/
  echo "  ✓ model -> $MODEL_DST/$(basename "$MODEL_SRC")"
  PREFER="onnx"
else
  echo "  ! no ONNX model at $MODEL_SRC — fetch with tools/fetch_onnx_embedder.py"
  echo "    (staying on TF-IDF for now)"
  PREFER="auto"
fi

echo "== 3. (re)build index with '$PREFER' embedder =="
python3 "$REPO"/ingest/build_index.py --prefer "$PREFER" || { echo "  ! build failed"; exit 1; }

echo "== 4. corpus/index -> $CORPUS_DST =="
sudo mkdir -p "$CORPUS_DST"
sudo rm -rf "$CORPUS_DST/diksha_g7_science"
sudo cp -r "$REPO/corpus/diksha_g7_science" "$CORPUS_DST/"
echo "  ✓ corpus -> $CORPUS_DST/diksha_g7_science"

echo "== 5. verify =="
python3 "$REPO"/tests/device_io_check.py
