# Newest stack (experiment branch)

Branch **`experiment/newest-py313`** tests the latest **pain-free** CUDA stack without
disturbing the stable path on `feat/jevlike-trainable-port`.

| | Stable branch | This branch |
|---|---------------|-------------|
| Python | **3.11** (`.python-version`) | **3.13** |
| venv | `.venv` | **`.venv-newest`** |
| PyTorch | 2.10.0+cu128 (pinned) | **2.14.0+cu128** |
| Python cap | `<3.14` | `<3.14` (3.14 still excluded) |

## One-shot setup

```bash
git checkout experiment/newest-py313

# if needed (CachyOS):
# sudo pacman -S cachyos/python313

chmod +x scripts/setup-newest.sh
./scripts/setup-newest.sh
source .venv-newest/bin/activate
```

The script creates a **fresh** `.venv-newest`, installs **torch from the cu128 index**
(CUDA wheels, not CPU-only PyPI), then `pip install -e '.[test]'` and runs the jevlike
smoke test.

## Why not 3.14?

System `python3` may be **3.14**. Wheels for torch/numpy/triton exist in preview, but
installs still hit triton path bugs and pinned-dependency mismatches. This branch targets
**3.13** as the newest **boring** interpreter.

## Manual install (if you prefer)

```bash
python3.13 -m venv .venv-newest && source .venv-newest/bin/activate
pip install -U pip
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu128
pip install -e '.[test]'
pytest tests/test_jevlike_smoke.py -q
```

## Merge policy

Do **not** merge dependency bumps to stable until smoke tests + a Route B scoring run pass
on your GPU. This branch is for experimentation only.
