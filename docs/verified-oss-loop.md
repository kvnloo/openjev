# Verified OSS Loop (z0int)

This repository already has its own `AGENTS.md` and `docs/ONBOARDING.md`. The Verified OSS Loop kit lives under `.verified-oss-loop/` so onboard does not replace project instructions or dump kit skills over existing `source: local` skills (e.g. `skills/z0int-onboard`).

**Default branch is `master`.** Workers never merge `master` or `dev`. `github_writes=0` on origin until a human authorizes origin writes. Open day-pass PRs on the **fork** / `preview` target; overnight unattended work targets `nightly`.

## Kit paths

- Inventory / rollout: `.verified-oss-loop/`
- Kit skills (not copied into `skills/`): `.verified-oss-loop/skills/`
- Similar-issue clustering (local fixture only, cap 64): `.verified-oss-loop/scripts/cluster-similar-issues.py`

## Commands

| Layer | Command |
|---|---|
| Unit | `python -m pytest -q` |
| Product smoke | `python -m z0int doctor --json` |
| Mutation | `n/a` (not adopted; do not invent a score) |
| Runtime | `python -m z0int status` / project-specific |

```bash
python3 .verified-oss-loop/rollout.py show
python3 .verified-oss-loop/kit-inventory.py show --root .
# optional fixture only if present:
# python3 .verified-oss-loop/scripts/cluster-similar-issues.py tests/fixtures/issues-tiny.json
```

Protocol: https://github.com/kvnloo/verified-oss-loop  
Repo: https://github.com/kvnloo/z0intelligence

