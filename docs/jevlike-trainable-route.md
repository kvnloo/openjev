# Route A: trainable jevlike scorers (ported)

OpenJev ships two complementary paths:

| Route | When | Tooling |
|-------|------|---------|
| **A — trainable head** | You have labelled JSONL and want a small/fast scorer | `openjev-train`, `openjev-eval`, `openjev-predict` |
| **B — direct logit readout** | Zero-shot on a frozen 4B+ model | `openjev-score --mode direct` |
| **C — vLLM diffusion read** | Jev-like reads via served DiffusionGemma (PR [#57250](https://github.com/vllm-project/vllm/pull/57250)) | `openjev-score --mode vllm` — see [vllm-diffusion-route.md](vllm-diffusion-route.md) |

Route A is ported from [vinnylarouge/jevlike](https://github.com/vinnylarouge/jevlike) (MIT) into
`openjev_phase1.jevlike`. Same JSONL format:

```json
{"context":"The customer needs a refund.","options":["refund","sales","technical support"],"label":0}
```

## Quickstart (CUDA / CPU)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'

openjev-data synthetic --output data/synthetic
openjev-train data/synthetic/train.jsonl \
  --validation data/synthetic/validation.jsonl \
  --output runs/synthetic.pt \
  --device cuda
openjev-eval runs/synthetic.pt data/synthetic/test.jsonl --device cuda
openjev-predict runs/synthetic.pt \
  --context "Choose the exact badge amber badger. Badge: amber badger." \
  --option "azure crane" --option "amber badger" --option "gold heron"
```

Use `--encoder hf --hf-model Qwen/Qwen2.5-0.5B` for a frozen transformer encoder + trainable head.

## Visual / game scorers

Screen-dependent scoring (`DoomScorerV2`) and the Doom/Chess examples live under
`examples/jevlike/`. Install game extras first:

```bash
pip install -e '.[games,test]'
pytest -q tests/test_jevlike_smoke.py
PYTHONPATH=examples/jevlike/doom pytest -q examples/jevlike/doom/test_smoke.py
```

Wikispeedia dataset builder: `scripts/get_wikispeedia.sh` then
`openjev-data wikispeedia --root data/wikispeedia --output data/wikispeedia/jsonl`.

## Bridge to OpenJev JSONL decisions

Score a labelled file with a checkpoint and emit probability rows:

```bash
openjev-bridge-score runs/synthetic.pt data/synthetic/test.jsonl --output bridge.jsonl
```

Compare zero-shot Route B on the same tasks with `openjev-score` when you convert rows via
`openjev_phase1.jevlike.bridge.choice_to_openjev_row`.
