# Route C: vLLM DiffusionGemma structured reads

OpenJev can score decision JSONL through a **vLLM server** running DiffusionGemma with
[structured read mode](https://github.com/vllm-project/vllm/pull/57250) instead of loading
Qwen weights locally. This is an alternate path to **Jev-like** typed decisions with
logprob-derived option masses — complementary to Route A (trainable jevlike heads) and
Route B (direct autoregressive logit readout).

| Route | Backend | When |
|-------|---------|------|
| **A** | `openjev-train` / local torch | Labelled JSONL, small/fast head |
| **B** | `openjev-score --mode direct` | Frozen causal LM on one GPU |
| **C** | `openjev-score --mode vllm` | vLLM + DiffusionGemma PR #57250 |

Route C does **not** train weights. It sends each OpenJev row as one bounded multiple-choice
read: a seeded diffusion canvas, one denoise step, read-only emit, and exact
`logprob_token_ids` for option letters **A–P** (same slot constraint as Route B).

## Prerequisites

1. vLLM built from PR **#57250** (or a release that includes structured diffusion reads).
2. DiffusionGemma served with a canvas wide enough for the answer template (64 is typical).
3. Network access from the OpenJev client to the vLLM OpenAI port.

Bootstrap helper (prints exact commands; does not install GPU wheels for you):

```bash
bash scripts/setup-vllm-diffusion.sh
```

## Serve DiffusionGemma

Example from the PR (adjust model id and GPU flags for your box):

```bash
vllm serve nvidia/diffusiongemma-26B-A4B-it-NVFP4 \
  --diffusion-config '{"canvas_length": 64}' \
  --max-logprobs 32 \
  --enable-prefix-caching \
  --async-scheduling
```

Optional: run vLLM's sample `structured_server.py` interposer on port 8011 for multi-question
schemas. OpenJev Route C talks to vLLM **directly** — one OpenJev row → one chat completion.

## Score OpenJev JSONL

```bash
pip install -e '.[test]'

openjev-score --mode vllm \
  --upstream http://127.0.0.1:8000 \
  --model dgemma \
  --tokenizer nvidia/diffusiongemma-26B-A4B-it-NVFP4 \
  --canvas 64 \
  --steps 1 \
  --input examples/decisions.jsonl \
  --output results-vllm.jsonl
```

Output rows match Route B shape (`probabilities`, `option_ids`, timing fields) with
`model.backend = "vllm-diffusion"` and diagnostics for canvas slot entropy.

## Option letters

Each option is mapped to a single-letter label **A, B, C, …** for the diffusion canvas.
If the tokenizer cannot swap letters in exactly one token at the answer slot, the row is
rejected at template compile time (same practical constraint as the PR's structured server).

## Hermes / FlyForge

Use Route C when a hosted or sidecar vLLM cluster already runs DiffusionGemma and you want
OpenJev-shaped JSONL for benchmarks or the generation-free Hermes plugin path, without
checking a `.pt` checkpoint into the tree. Route A remains the path for evolved / trained
specialists via Evolution Lab.
