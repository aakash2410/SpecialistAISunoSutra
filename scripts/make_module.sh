#!/usr/bin/env bash
# Export the installable Specialist module from the canonical source tree.
#
#   ./scripts/make_module.sh [--tar]
#
# Produces dist/specialist-module/: the pipeline code, the e5 embedding model, the
# answer verifier, the safety model, the language ID model, the baked DIKSHA indexes and the Ansible role. Bhashini and the CUDA
# build of CTranslate2 are too large to ship here, so the role fetches them on the device.
# Re-run this after any code or corpus change.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/specialist-module"
PKG="$ROOT/python/pocketinfer"
E5="$ROOT/local/models/multilingual-e5-small-onnx"
VERIFIER="$ROOT/local/models/nli-deberta-v3-base-onnx"
SAFETY="$ROOT/local/models/nli-safety-onnx"
LANGID="$ROOT/local/models/lang-id-ecapa-onnx"
VERSION="$(date +%Y.%m.%d)"

echo "==> Building Specialist module $VERSION"
if [ ! -f "$E5/tokenizer.json" ]; then
  echo "!! No embedding model at $E5. Run: python3 tools/fetch_onnx_embedder.py" >&2
  exit 1
fi
if [ ! -f "$VERIFIER/model.onnx" ]; then
  echo "!! No answer verifier at $VERIFIER. Run: python3 tools/fetch_onnx_verifier.py" >&2
  exit 1
fi
if [ ! -f "$SAFETY/model.onnx" ]; then
  echo "!! No safety model at $SAFETY. Export one with training/export_nli.py" >&2
  exit 1
fi
if [ ! -f "$LANGID/model.onnx" ]; then
  echo "!! No language ID model at $LANGID. Export it with tools/export_langid.py" >&2
  exit 1
fi
rm -rf "$OUT"
APP="$OUT/payload/app/python/pocketinfer"
mkdir -p "$APP/models" "$APP/specialist" "$OUT/payload/corpus" "$OUT/payload/models" "$OUT/payload/tools"

# --- code: only what the pipeline runs ---------------------------------------
cp "$PKG/__init__.py" "$APP/"
for model in asr nmt tts ocr embed verify whisper langid; do
  cp "$PKG/models/$model.py" "$APP/models/"
done
cp "$PKG/specialist/"*.py "$APP/specialist/"
mkdir -p "$OUT/payload/app/local"
cp "$ROOT/local/"*.py "$OUT/payload/app/local/"

# --- models and tools --------------------------------------------------------
cp -R "$E5" "$VERIFIER" "$SAFETY" "$LANGID" "$OUT/payload/models/"
cp "$ROOT/tools/fetch_assets.py" "$ROOT/module/assets.json" "$OUT/payload/tools/"

# --- content: the one baked index the device will use, not raw harvests ------
# Same choice as the voice loop (local/corpus_index.py), looking in this repo only.
INDEX="$(cd "$ROOT" && python3 -c 'import sys; from local.corpus_index import find_index; print(find_index(roots=(sys.argv[1],)) or "")' "$ROOT/corpus")"
if [ -z "$INDEX" ]; then
  echo "!! No baked index found. Run: python3 ingest/build_index.py --corpus corpus/diksha_g6_8_science" >&2
  exit 1
fi
name="$(basename "$(dirname "$INDEX")")"
echo "==> Shipping index $name"
mkdir -p "$OUT/payload/corpus/$name"
cp -R "$INDEX" "$OUT/payload/corpus/$name/"

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
