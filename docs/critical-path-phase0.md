# Critical path Phase 0 inventory (2026-09-18)

Mission: `~/Downloads/z0int-critical-path-mission.zip` → close live evidence-to-action loop.
This file is **deployment evidence**, not architecture theater.

## Decision (locked)

- No new optimizer repo / MoA prerequisite.
- First primitive: **resident `resolve_context`** in z0int → feeds AODL provenance/evidence bags.
- Kerdoios = quota/plan eligibility, not a second scheduler.
- Do **not** flip z0int-bridge `execution: log_only` to live until host consumes runnable results.
- Preserve `execution_completed` ≠ `verified_success`.

## Repos (local, reconciled)

| Tree | Branch | HEAD | Dirty | Notes |
|------|--------|------|-------|-------|
| `/home/kvn/tmp/openjev` | master | `b2d43f45` | clean | remote-aligned z0int; **live bridge target** |
| `/home/kvn/tmp/z0int-future-integrate` | feat/nanojev… → feat/resolve-context | `c379325`+ | — | future stack + NanoJev + resolver |
| `/workspace/evolution-lab` | nightly | `fcab06f4` | clean | matches mission EL pin |
| Kerdoios chiefstaff plugin | (plugin git) | `e3100ae4` | — | matches mission main pin |
| Kerdoios intake plugin | | `b6bdfae6` | — | older |
| TencentDB-Agent-Memory | feat/server_team | `0aff21a2` | dirty ignore | v2.0.0 release tip |
| NanoJev | main | `71a513bb` | clean | parity baseline |
| AODL audit | detached | `9e8d6167` | — | intent-contract profile present |

Handoffs applied locally (not necessarily on origin/master): future routine/cascade/AODL/repair, NanoJev DecisionBackend, now `resolve_context`.

## Live processes (identity only)

- `omp` ×2 (bun) — coding harness
- Hermes chiefstaff gateway + dashboards + mesh-keel
- flyforge-recovery workers → `openjev` omp-extensions
- **No** resident qmd HTTP/MCP server observed
- **No** vLLM / NanoJev long-lived worker observed (NanoJev loads per eval)

## Bridge

- Symlink: `~/.omp/agent/extensions/z0int-bridge` → `/home/kvn/tmp/openjev/omp-extensions/z0int-bridge`
- HEAD of that tree: `b2d43f45` master
- `execution: "log_only"` still set (observe preflight + kerdoios plan; not avoided-model proof)
- Evidence split on close is present (`execution_completed` vs verified)

## Streams / receipts (counts)

| File | Lines |
|------|------:|
| stream/bridge.jsonl | 51 |
| stream/raw.jsonl | 100 |
| receipts/decisions.jsonl | 167 |
| receipts/outcomes.jsonl | 63 |
| outcome_gold.jsonl | 3 |

Recent decisions: `execution=log_only` on all sampled; route=`model`; providers include cerebras/xai-oauth/paid-api. **Planned model ≠ separate field** — receipt stores executed `provider`/`model` after the turn.

## QMD

- CLI: `~/.local/bin/qmd` installed
- Index: `~/.cache/qmd/index.sqlite` **0 documents, 0 collections**
- Status: installed, **not used**, not verified for scoped retrieval
- Transport for harness: not wired as resident HTTP/MCP in this session

## TencentDB memory

- Extension present: `~/.omp/agent/extensions/tencentdb-memory/index.ts` → gateway `MEMORY_TENCENTDB_GATEWAY_URL` default `:8420`
- Repo: `/mnt/zer0models/oss/TencentDB-Agent-Memory` @ `0aff21a` feat/server_team
- Proxy can inject memory — resolver must default **allow_memory=False** to avoid double-inject

## EL preflight

- `/workspace/evolution-lab` @ `fcab06f` still returns `context_refs=[]`, `evidence_level=L0_imitation` (mission pitfall confirmed)
- Bridge calls `python -m evolution_lab preflight` with EL_ROOT=/workspace/evolution-lab

## Kerdoios

- chiefstaff plugin @ `e3100ae`: single remaining/reset parser in `providers/http.py` (mission pitfall confirmed)
- heal.py classify/success-on-exit0: **not fully verified on this tip** (self-healing-run branch was mission pin `3e0b502`; local plugin is main economics tip)

## Goal / queue

- mesh-keel: `~/.local/state/hermes-mesh-keel/queue.db` exists (supervisor queue)
- z0int state: onboard.json only — **no durable authorized-task checkpoint object yet**

## First code slice shipped here

- `z0int context resolve` → `resolve_context` packet with EvidenceRef + ResolutionRecipe + AODL projection
- CPU tests; no GPU; no live-flag flip

## Blockers (exact)

1. QMD empty — lexical path cannot satisfy NL needs until scoped collections exist for the active project.
2. Bridge still log_only — host does not consume local runnable results to skip frontier.
3. No single authorized task family + frozen verifier wired to resolver output yet.
4. openjev master (live bridge) lacks NanoJev/future/resolver until promote/sync.
5. Multi-constraint quota types not in Kerdoios main tip.
6. Double-inject risk if memory proxy on and resolver memory on.

## Not done (by mission order)

- Evidence-packet invalidation on new overriding sources
- Single-flight coalescing across agents
- Quota-group atomic reservation
- Restart resume E2E
- ABAB pilot timings
