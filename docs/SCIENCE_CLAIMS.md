# Frozen science claims (z0int / Evolution Lab)

**Do not loosen this language in README, status, or agent prose without a new gen lock.**

Source of truth for numbers: `evolution_lab.coverage_metric.frozen_claims()` and
`data/next_action/champion.json` / `coverage_policy.json` in Evolution Lab.

## Gen-0 (wave-5 hash PN)

| Field | Value | Meaning |
| --- | --- | --- |
| confirm_acc | ≈0.572 | Multi-class next-action on chronological confirm |
| coverage@95 | ≈0.199 | Fraction of confirm absorbed under ≥0.95 precision prefix |
| precision@95 | ≈0.980 | **Prediction-conditional** on DELEGATE-pred mass (n=3063) |

**Allowed:** “DELEGATE *predictions* on confirm cover ~19.9% at ~98.0% precision.”

**Forbidden:**

- “the model is 98.2% / 98% accurate”
- “DELEGATE base-rate is 98%”
- citing raw precision without coverage, base-rate, lift, or FPR

## Gen-1 (old labels + rich PN)

| Field | Value |
| --- | --- |
| coverage@95 (local mask) | ≈0.287 (> gen-0) |
| precision@95 | ≈0.950 |
| cascade coverage | ≈0.369 |
| cascade local_precision | ≈0.90 |
| confirm_acc | ≈0.630 |

Promote metric remains **coverage at ≥95% precision**, not raw accuracy.

## DELEGATE gating reports

Always pair precision with:

1. `base_rate` — P(true DELEGATE)
2. `lift_over_base_rate` — precision / base_rate
3. `fpr` — false positive rate among non-DELEGATE
4. `coverage_at_95` when discussing safe offload mass

API: `evolution_lab.coverage_metric.binary_gate_metrics(y_true, y_pred)`.

## L2 bar

L0/L1 labels are **not** world success. First L2 card is **`recovery_action`**
(gym closed-loop + locked P0). Live harness joins still use `trace_id` via
`z0int receipt join` / `outcome_gold` for world events outside the gym.

## Evidence semantics (receipt outcomes)

Do **not** train routers on ambient turn completion.

| Signal | Meaning | `outcome_tier` | Counts as `verified_tasks`? |
| --- | --- | --- | --- |
| `execution_completed` | Agent turn finished | `execution` | no |
| `tool_ok` / bare `success` | Transport/soft legacy | `soft` | no |
| `test_pass` / `verifier_ok` / `pr_merged` / `task_done` / `verified_success` | Real quality | `gold` | yes |
| `user_correction` / `reverted` / `ci_failed` | Failure | `negative` | no |

Ambient OMP `turn_end` must emit `execution_completed=true` with `verified_success=null`.
Async `z0int receipt join` attaches gold later. Kerdoios `--completed` only on verified arms.

Provider/model on every arm must be the **actual** model version when known (not `kerdoios_plan`/`session` placeholders once usage is on the message).


## First L2 specialist (locked pick)

**`recovery_action`** — not `needs_verification`, not `delegate_gating`.

| Why recovery_action | Why not the others |
| --- | --- |
| `label_quality=high`; gym + locked P0 splits (`data/p0`, seed 20260912) | `needs_verification`: sealed L0 n≈14, needs test-join |
| Closed-loop reward is a real subsequent-success signal (L2) | `delegate_gating`: L0 fingerprint only until wall-clock join |
| Student bundle already hits confirm/val/ood/CL gates | |

Run:

```bash
python -m evolution_lab l2-recovery
# → ~/.z0int/benchmarks/recovery_action_l2.json
# → ~/.z0int/specialists/recovery_action.canary.json  (only if gates pass)
```

Gates (must all hold): confirm≥0.95, val≥0.95, ood≥0.85, closed_loop≥1.0.
OMP hooks stay **log_only** until that canary marker exists; other capabilities remain shadow.

