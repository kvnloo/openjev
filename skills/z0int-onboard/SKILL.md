---
name: z0int-onboard
description: Onboard or repair a z0int checkout. Use when the user says onboard, setup flyforge, install z0int, or doctor the environment.
---

# z0int onboard

## Principle

Deterministic setup lives in the **`z0int` CLI**. This skill only routes the agent to that CLI and reports unresolved decisions.

## Procedure

1. Confirm cwd is the z0int repo root (`pyproject.toml` name `openjev-phase1` / README title z0int).
2. Run:

```bash
python -m z0int doctor --json
```

3. If the package is not importable:

```bash
pip install -e '.[test]'
# or
./scripts/bootstrap.sh
```

4. Run onboarding (safe, resumable):

```bash
python -m z0int onboard --auto
```

Use `--dry-run` first when the user asked only for a plan.  
Use `--sync-models` only when the user wants HF downloads.  
Use `--skip-evolution-lab` when offline or EL is managed elsewhere.

5. Run:

```bash
python -m z0int status --json
```

6. Report to the user:
   - overall ok / not
   - hardware + models plan one-liner
   - discovered data sources
   - **only** items in `unresolved` or onboard `next`

## Do not

- Re-type `scripts/setup-flyforge.sh` steps from memory.
- Load OpenJev 0.6B and 4B together on 12GB VRAM.
- Commit anything under `~/.z0int/` or personalized `champion.npz`.
- Bypass privacy failures from `z0int doctor` / onboard `privacy` step.
- Invent CLI subcommands that do not exist yet (`train recipe`, `evolve`, …) — say they are roadmap and point at `docs/ONBOARDING.md`.

## Optional follow-ups (only if data present)

```bash
z0int-compile
python -m evolution_lab capability-mine
python -m evolution_lab preflight "…"
```
