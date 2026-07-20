#!/usr/bin/env bash
# Export the flashable Specialist overlay module from the canonical source tree.
#
#   ./scripts/make_module.sh [--tar]
#
# Produces dist/specialist-module/ — a purely ADDITIVE overlay for a device that
# already has the Suno Sutra base platform. Nothing in it overwrites a base file.
#
# The canonical source stays in python/pocketinfer/... (so the laptop demo and
# voice loop keep working); this script copies the Specialist-owned files into a
# payload that mirrors the on-device layout. Re-run it after any code or corpus
# change — there is no second copy to keep in sync.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/specialist-module"
PKG="$ROOT/python/pocketinfer"
VERSION="$(date +%Y.%m.%d)"

echo "==> Building Specialist module $VERSION"
rm -rf "$OUT"
mkdir -p "$OUT/payload/python/pocketinfer/models" \
         "$OUT/payload/python/pocketinfer/specialist" \
         "$OUT/payload/python/pocketinfer/applications" \
         "$OUT/payload/corpus"

# --- code: only the files the Specialist owns (all NEW on the device) --------
cp "$PKG/models/embed.py" "$PKG/models/ocr.py" \
   "$OUT/payload/python/pocketinfer/models/"
cp "$PKG/specialist/"*.py "$OUT/payload/python/pocketinfer/specialist/"
cp "$PKG/applications/specialist_app.py" \
   "$OUT/payload/python/pocketinfer/applications/"

# --- content: the baked corpus/index (deployed to /opt/specialist/corpus) ----
if [ ! -f "$ROOT/corpus/diksha_g7_science/index/embeddings.npy" ]; then
  echo "!! No baked index found. Run: python ingest/build_index.py" >&2
  exit 1
fi
cp -R "$ROOT/corpus/"* "$OUT/payload/corpus/"

# --- provisioning: standalone ansible + module deps -------------------------
cp -R "$ROOT/module/ansible" "$OUT/ansible"
cp "$ROOT/module/requirements-specialist.txt" "$OUT/"

# Sample inventory so the module can be run without the base repo checked out.
cat > "$OUT/ansible/inventory.ini.sample" <<'EOF'
# Copy to inventory.ini and adjust for your device.
[usb]
192.168.55.1 ansible_user=ubuntu ansible_password=ubuntu ansible_sudo_pass=ubuntu
EOF

echo "$VERSION" > "$OUT/VERSION"
cp "$ROOT/module/README.md" "$OUT/README.md" 2>/dev/null || true

# --- manifest: exactly what lands where, for the hardware team ---------------
{
  echo "Specialist module $VERSION"
  echo "Generated $(date -u +%Y-%m-%dT%H:%M:%SZ) — purely additive overlay."
  echo
  echo "FILE -> ON-DEVICE DESTINATION"
  for f in "$OUT"/payload/python/pocketinfer/models/*.py; do
    echo "  payload/python/pocketinfer/models/$(basename "$f") -> {device_root}/python/pocketinfer/models/"
  done
  for f in "$OUT"/payload/python/pocketinfer/specialist/*.py; do
    echo "  payload/python/pocketinfer/specialist/$(basename "$f") -> {device_root}/python/pocketinfer/specialist/"
  done
  echo "  payload/python/pocketinfer/applications/specialist_app.py -> {device_root}/python/pocketinfer/applications/"
  echo "  payload/corpus/** -> /opt/specialist/corpus/"
  echo "  (systemd drop-in)  -> /etc/systemd/system/pocketinfer.service.d/10-specialist.conf"
  echo
  echo "device_root default: /home/ubuntu/pocket-infer-sw"
  echo "NO base platform file is modified or overwritten."
} > "$OUT/MANIFEST.txt"

echo "==> Wrote $OUT"
find "$OUT" -type f | sed "s|$OUT|    dist/specialist-module|" | sort

if [ "${1:-}" = "--tar" ]; then
  TARBALL="$ROOT/dist/specialist-module-$VERSION.tar.gz"
  tar -czf "$TARBALL" -C "$ROOT/dist" specialist-module
  echo "==> Tarball: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
fi
