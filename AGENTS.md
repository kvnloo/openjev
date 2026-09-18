# Repository instructions

## Onboarding (do this first)

1. Read **`docs/ONBOARDING.md`** (canonical product setup).
2. Prefer **`z0int` CLI** over manually reproducing setup:
   - `z0int doctor --json`
   - `z0int onboard --auto`
   - `z0int status`
   - `z0int models plan` / `z0int models sync`
3. Do not bypass privacy/secret checks. Do not invent unimplemented steps.
4. Thin skill: `skills/z0int-onboard/SKILL.md` (calls CLI only).

## Verified OSS Loop

This repo follows the [Verified OSS Loop](https://github.com/kvnloo/verified-oss-loop). See **`docs/verified-oss-loop.md`**.

- Kit: `.verified-oss-loop/` (inventory, rollout, kit skills)
- Issues are not claims. Workers never merge `master` or `dev`.
- Day-pass PRs → `preview`; overnight unattended → `nightly` (see `python3 .verified-oss-loop/rollout.py show`).
- Evidence: revision-bound unit + product smoke; mutation is `n/a` unless adopted.

## Runtime / bench

- Run commands from the repository root in an isolated environment installed with `pip install -e '.[test]'` (or `./scripts/bootstrap.sh`).
- FlyForge stack bootstrap (legacy, still valid): `bash scripts/setup-flyforge.sh`. Prefer `z0int onboard` for new setups. See `docs/evolution-lab.md`.
- Route C (vLLM DiffusionGemma structured reads): `docs/vllm-diffusion-route.md`, `openjev-score --mode vllm`, `scripts/setup-vllm-diffusion.sh`. Requires vLLM PR #57250 or equivalent.
- OMP plugins live in `omp-extensions/` and must be symlinked into `~/.omp/agent/extensions/` (`flyforge-recovery`, `flyforge-jev` shadow-only, `vllm-jev`, `openjev`, `openjev-06b`, `z0int-bridge`). `z0int onboard` links them when OMP is present. Do not edit copies only under `~/.omp`. OpenJev 4B: `Qwen/Qwen3.5-4B` @ `851bf6e…`. 0.6B test lane: `Qwen/Qwen3-0.6B` @ `c1899de…`. Do not load both SLMs on 12GB. Shadow: `EVOLUTION_LAB_PYTHON` / `EVOLUTION_LAB_ROOT` optional (`~/.z0int/config/env_hints.json`).
- Validate changes with `pytest -q`, `(cd results/raw && sha256sum -c SHA256SUMS)`, and `python benchmarks/verify_published.py`.
- Benchmark outputs are create-only. Use a new output path and expose exactly one CUDA GPU per scorer process.
- Do not change headline claims or `results/phase1-summary.json` without committing the supporting row-level evidence, regenerating the relevant raw report, updating `results/raw/SHA256SUMS`, and updating the method/results text.
- Preserve exact model and source revisions. Fetch third-party evaluation records only through `benchmarks/fetch_sources.py`; do not commit model weights, caches, or third-party raw records.
- Personalized state lives under **`~/.z0int/`** only (episodes, specialists, stream, research). Never default champions into the git tree.
- `webgpu-demo/` is static and has no build step. Preserve `_headers`, runtime version pins, browser-only inference, and the explicit probability limitations.

## Decision backends

- Contract: `src/z0int/backends/` (`DecisionBackend`, not Provider).
- First local semantic engine: NanoJev (`nanojev` / model id `nanojev_06b`).
- Agent commands:
  - `z0int backends list --json`
  - `z0int backends doctor --json`
  - `z0int backends eval --backend nanojev --input tests/fixtures/nanojev_request.json --json`
- Do not load NanoJev from ordinary doctor/status list paths.
- Do not mark backend inference as `verified_success`; ambient turn close ≠ gold.

## Brand (background)

Product name **z0intelligence** (stochastic-parrot play; abundance under finite frontier budgets with Kerdoios). Details: `docs/brand.md`. Do not mass-rename the `z0int` package in drive-by PRs.
