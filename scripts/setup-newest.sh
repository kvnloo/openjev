#!/usr/bin/env bash
# Install OpenJev into a separate venv with the newest pain-free stack:
#   Python 3.13 + PyTorch 2.14 cu128 (CUDA 12.8 wheels)
#
# Does NOT touch .venv (stable 3.11 path on feat/jevlike-trainable-port).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv-newest"
PYTORCH_INDEX="https://download.pytorch.org/whl/cu128"

pick_python() {
  for candidate in python3.13 /usr/bin/python3.13; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON="$(pick_python)"; then
  cat <<'EOF'
Python 3.13 not found.

On CachyOS/Arch:
  sudo pacman -S cachyos/python313

Then re-run:
  ./scripts/setup-newest.sh
EOF
  exit 1
fi

ver="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$ver" == "3.14" ]]; then
  echo "Refusing Python 3.14 on this branch (torch/numpy/triton still flaky). Use 3.13." >&2
  exit 1
fi
if [[ "$ver" != "3.13" ]]; then
  echo "Warning: expected Python 3.13, got $ver — continuing anyway." >&2
fi

echo "Using: $PYTHON ($("$PYTHON" --version))"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install -U pip wheel setuptools -q

# CUDA wheels first (avoid CPU-only torch from PyPI default index).
pip install "torch==2.14.0" --index-url "$PYTORCH_INDEX"

# Rest of deps from pyproject (torch already satisfied).
pip install -e "${ROOT}[test]" -q

python - <<'PY'
import sys
import torch
print("python", sys.version.split()[0])
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0))
PY

pytest "${ROOT}/tests/test_jevlike_smoke.py" -q
echo ""
echo "OK — activate with: source .venv-newest/bin/activate"
