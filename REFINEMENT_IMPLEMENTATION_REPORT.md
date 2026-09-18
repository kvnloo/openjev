# Implementation report: counterexample-driven routine repair

## Scope and baseline

Continued locally from the supplied `z0int-future-abab-optimized.zip`, not from a newly fetched upstream checkout. The supplied snapshot has 31 passing future-feature tests. This workspace restores that snapshot as a local Git baseline; its new commit history is not claimed to reproduce the original upstream object graph.

Grok-owned onboarding, Kerd integration, capability mining, token receipts, and live-source adapters were left alone.

## Highest-leverage choice

The old path stopped at `drift -> demote`. This patch implements `drift -> demote -> narrow -> freeze -> fresh credit -> confirm -> explicitly activate`, preserving fallback. This uses the earlier ABAB discipline for a concrete transformation rather than adding another generic optimizer.

The Frontier KB principles already supplied in the conversation guided the slice:

- `perm-20260912-distill-motifs-not-upload-the-graph`: deploy a small auditable computation when it suffices.
- `perm-20260914-harness-evolver`: separate open diagnosis from credit, preserve activation checks, and retain rollback.
- `perm-20260912-experiments-are-the-population`: preserve negative results and separate deterministic implementations from learned-only fronts.
- `perm-20260912-joules-per-verified-success`: do not translate a local accuracy improvement into invented economic savings.

These are design provenance, not a claim to have performed a new web literature audit.

## Implemented

`src/z0int/refinement.py` adds bounded positive-guard repair, strict outcome/provenance input validation, candidate content hashes, and separate scoring/activation APIs. A transactional private trial journal rejects cohort reuse across process restarts and concurrent attempts. Both successful and failed evaluation attempts consume their cohorts.

`RoutineRegistry.decide_shadow` makes candidate evaluation callable without marking a rule promoted. The AODL binding explicitly references this entrypoint. New compositions are shadow-only. A repaired child does not inherit a parent's L3 closed-loop evidence level; its own grade is capped at L2. AODL plan hashes now include deployment mode and routine artifact digests. Intent hashes remain stable under implementation-only changes.

Predicate hardening rejects non-finite numeric values and prevents a Boolean `true` guard from matching numeric `1`.

The CLI supports proposal, scoring, explicit activation, rollback, and AODL shadow export. The repo-owned skill documents this path without requiring a specific LLM harness or an LLM labeling call.

## Reproduced fixture result

`examples/refinement/demo.py` generates synthetic pre-decision events and a known toy verifier. It demotes an overbroad parent rule and learns `phase == batch` as an extra guard. The exception region continues to fallback.

On the separate 384-case synthetic routing replay:

| Variant | Correct bounded outputs | Fallback callback invocations |
| --- | ---: | ---: |
| After parent demotion | 384 / 384 | 384 |
| After repaired-child activation | 384 / 384 | 192 |
| After rollback | 384 / 384 | 384 |

The fresh sealed and future fixture cohorts each contain 96 session IDs. The reversed-label negative control is rejected. This is a reproducible integration fixture, not evidence about Blender, Codex, a real personal dataset, or subscription quota. No LLM was called. Real token savings and whole-task quality remain unknown.

## Verification

The future-feature suite now contains **73 passing tests**, including the original 31. Verification covers malformed inputs, temporal leakage, duplicated evidence, cohort shopping, restart idempotency, competing evaluations, stale parent replacement, shadow-only routing, AODL intent/plan separation, and rollback. A subprocess test exercises the complete CLI lifecycle.

The full upstream application suites, upstream AODL nightly validator, and live GPU/model benchmarks were not run. The confidence calculation is a nominal session-level Wilson interval, not a formal guarantee for arbitrary correlated or adversarial traffic. The trial journal assumes trusted input provenance and filesystem ownership; it is not cryptographic authorization or OS-level isolation.

## What is needed to use real data

The existing near-term collector/verifier work must supply outcome-backed targets, stable episode/session IDs, and feature/decision/outcome timing. Old `tool_ok`-as-gold rows are insufficient. Once those inputs exist, the new module can be run without an additional trainer or provider integration.

Before a repaired routine changes live traffic, validate the full composed cascade in the actual harness and respect the user's approval contract. The exported AODL plan intentionally stays shadow-only.
