#!/usr/bin/env bash
# Free up space on the Jetson by pruning the abandoned neural-embedder stack.
#
#   ./tools/prune_deps.sh            # REPORT ONLY — shows space + what it would remove
#   ./tools/prune_deps.sh --apply    # actually remove + clear caches + reinstall minimal
#
# SAFETY: only touches your USER site (~/.local) and caches. It never removes
# system (dist-packages) or venv packages, so the running services (which boot
# from the venv) are unaffected. The ONNX embedder keeps working — it needs only
# onnxruntime (already on the device), tokenizers, numpy<2, and the model files.

set -uo pipefail
APPLY="${1:-}"

PYUSER="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null)"

echo "==================== DISK ===================="
df -h / | awk 'NR==1 || /\/$/'
echo
echo "================ SPACE HOGS =================="
for d in "$HOME/.local" "$HOME/.cache" "$HOME/.cache/pip" "$HOME/.cache/huggingface" "$HOME/.cache/torch"; do
  [ -d "$d" ] && du -sh "$d" 2>/dev/null
done
echo
echo "========== BIGGEST USER PACKAGES ============="
[ -d "$PYUSER" ] && du -sh "$PYUSER"/* 2>/dev/null | sort -rh | head -15

# Pulled in by the sentence-transformers experiment; NOT needed by the ONNX path.
# (tokenizers, numpy, onnxruntime are deliberately KEPT.)
CANDIDATES=(
  sentence-transformers transformers accelerate datasets huggingface-hub
  safetensors tokenizers-cpp
  torch torchvision torchaudio triton sympy
  scikit-learn scipy joblib threadpoolctl
  nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12
  nvidia-cuda-nvrtc-cu12 nvidia-cufft-cu12 nvidia-curand-cu12
)

echo
echo "===== REMOVAL CANDIDATES (in ~/.local only) ====="
present=()
for p in "${CANDIDATES[@]}"; do
  loc="$(pip3 show "$p" 2>/dev/null | awk -F': ' '/^Location/{print $2}')"
  if [ -n "$loc" ] && printf '%s' "$loc" | grep -q "$HOME/.local"; then
    present+=("$p")
    echo "  will remove: $p  ($loc)"
  fi
done
[ ${#present[@]} -eq 0 ] && echo "  (none found in ~/.local)"

if [ "$APPLY" != "--apply" ]; then
  echo
  echo "REPORT ONLY. Re-run with --apply to remove the above, clear caches, and"
  echo "reinstall the minimal ONNX deps (numpy<2, tokenizers)."
  echo "Optional big win — drop an unused LLM:  ollama list  &&  ollama rm <model>"
  exit 0
fi

echo
echo "==================== APPLYING ===================="
if [ ${#present[@]} -gt 0 ]; then
  pip3 uninstall -y "${present[@]}"
fi

echo "-- clearing caches --"
rm -rf "$HOME/.cache/pip" "$HOME/.cache/huggingface" "$HOME/.cache/torch"
sudo apt-get clean 2>/dev/null || true
sudo journalctl --vacuum-size=100M 2>/dev/null || true

echo "-- reinstalling minimal ONNX deps --"
pip3 install --user "numpy<2" tokenizers

echo
echo "==================== DISK AFTER ===================="
df -h / | awk 'NR==1 || /\/$/'
echo "Verify the embedder still works:  python3 tests/device_io_check.py"
