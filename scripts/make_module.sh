#!/usr/bin/env bash
# Export the installable Specialist module from the canonical source tree.
#
#   ./scripts/make_module.sh [--tar]
#
# Produces dist/specialist-module/: the pipeline code, the e5 embedding model, the
# baked DIKSHA indexes and the Ansible role. Bhashini and the CUDA build of
# CTranslate2 are too large to ship here, so the role fetches them on the device.
# Re-run this after any code or corpus change.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/specialist-module"
PKG="$ROOT/python/pocketinfer"
E5="$ROOT/local/models/multilingual-e5-small-onnx"
VERSION="$(date +%Y.%m.%d)"

echo "==> Building Specialist module $VERSION"
if [ ! -f "$E5/tokenizer.json" ]; then
  echo "!! No embedding model at $E5. Run: python3 tools/fetch_onnx_embedder.py" >&2
  exit 1
fi
rm -rf "$OUT"
APP="$OUT/payload/app/python/pocketinfer"
mkdir -p "$APP/models" "$APP/specialist" "$OUT/payload/corpus" "$OUT/payload/models" "$OUT/payload/tools"

# --- code: only what the pipeline runs ---------------------------------------
cp "$ROOT/run_specialist.py" "$OUT/payload/app/"
cp "$PKG/__init__.py" "$APP/"
for model in asr nmt tts ocr embed whisper; do
  cp "$PKG/models/$model.py" "$APP/models/"
done
cp "$PKG/specialist/"*.py "$APP/specialist/"
mkdir -p "$OUT/payload/app/local"
cp "$ROOT/local/"*.py "$OUT/payload/app/local/"

# --- models and tools --------------------------------------------------------
cp -R "$E5" "$OUT/payload/models/"
cp "$ROOT/tools/fetch_assets.py" "$ROOT/module/assets.json" "$OUT/payload/tools/"

# --- content: baked indexes only, not raw harvests or backups ----------------
count=0
for index in "$ROOT"/corpus/*/index; do
  [ -f "$index/embeddings.npy" ] || continue
  name="$(basename "$(dirname "$index")")"
  mkdir -p "$OUT/payload/corpus/$name"
  cp -R "$index" "$OUT/payload/corpus/$name/"
  count=$((count + 1))
done
if [ "$count" -eq 0 ]; then
  echo "!! No baked index found. Run: python3 ingest/build_index.py" >&2
  exit 1
fi

# --- provisioning ------------------------------------------------------------
cp -R "$ROOT/module/ansible" "$OUT/ansible"
cp "$ROOT/module/requirements-specialist.txt" "$OUT/"
cp "$ROOT/module/README.md" "$OUT/README.md"
echo "$VERSION" > "$OUT/VERSION"

echo "==> Wrote $OUT ($(du -sh "$OUT" | cut -f1))"
find "$OUT" -type f | sed "s|$OUT|    dist/specialist-module|" | sort

if [ "${1:-}" = "--tar" ]; then
  TARBALL="$ROOT/dist/specialist-module-$VERSION.tar.gz"
  tar -czf "$TARBALL" -C "$ROOT/dist" specialist-module
  echo "==> Tarball: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
fi
