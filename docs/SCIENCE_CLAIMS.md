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

## L2 bar (next)

L0/L1 labels are **not** world success. L2 requires sealed outcomes joined on
`trace_id` (`z0int receipt join` / `outcome_gold`) for a chosen specialist
(`recovery_action` or `needs_verification` preferred over `delegate_gating`).
