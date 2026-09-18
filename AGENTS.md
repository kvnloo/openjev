# Repository instructions

- Run commands from the repository root in an isolated environment installed with `pip install -e '.[test]'`.
- FlyForge stack bootstrap: `bash scripts/setup-flyforge.sh` (OpenJev runtime + optional evolution-lab clone). See `docs/evolution-lab.md`.
- Route C (vLLM DiffusionGemma structured reads): `docs/vllm-diffusion-route.md`, `openjev-score --mode vllm`, `scripts/setup-vllm-diffusion.sh`. Requires vLLM PR #57250 or equivalent.
- OMP plugins live in `omp-extensions/` and must be symlinked into `~/.omp/agent/extensions/` (`flyforge-recovery`, `flyforge-jev` shadow-only, `vllm-jev`, `openjev`, `openjev-06b`). Do not edit copies only under `~/.omp`. OpenJev 4B: `Qwen/Qwen3.5-4B` @ `851bf6e…`. 0.6B test lane: `Qwen/Qwen3-0.6B` @ `c1899de…`. Do not load both SLMs on 12GB. Shadow: `EVOLUTION_LAB_PYTHON` / `EVOLUTION_LAB_ROOT` optional.
- Validate changes with `pytest -q`, `(cd results/raw && sha256sum -c SHA256SUMS)`, and `python benchmarks/verify_published.py`.
- Benchmark outputs are create-only. Use a new output path and expose exactly one CUDA GPU per scorer process.
- Do not change headline claims or `results/phase1-summary.json` without committing the supporting row-level evidence, regenerating the relevant raw report, updating `results/raw/SHA256SUMS`, and updating the method/results text.
- Preserve exact model and source revisions. Fetch third-party evaluation records only through `benchmarks/fetch_sources.py`; do not commit model weights, caches, or third-party raw records.
- `webgpu-demo/` is static and has no build step. Preserve `_headers`, runtime version pins, browser-only inference, and the explicit probability limitations.
