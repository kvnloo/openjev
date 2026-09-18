# ABAB meta-loop iteration report

Local branch: `feat/routine-compiler`

This pass used Frontier KB as the source of invariants and treated the ABAB loop itself as the object under optimization.

## Wave 1 — sealed credit must be statistical

Frontier KB input:
- `perm-20260914-harness-evolver`: validity -> activation -> paired 2-sigma sealed credit.
- `perm-20260911-measure-the-learning-loop`: publish outcome metrics, not effort.

Change:
- Added `z0int.credit`.
- Routine regions now record 2-sigma lift against the trivial output prior.
- Cascade success is paired against the frontier baseline on the exact same tasks.
- Zero-margin non-inferiority is no longer granted from a nice point estimate alone.

Commit: `bac0957`.

## Wave 2 — experiments are the population

Frontier KB input:
- `perm-20260912-experiments-are-the-population`: Δ frontier / MAP-Elites niches; retain failed runs; separate learned-only front.
- `perm-20260914-harness-evolver`: pathology-niche champion archive and cost per credit.

Change:
- Added `z0int.abab.ExperimentArchive`.
- Pareto fronts per niche.
- Separate learned-only fronts.
- Negative / hypothesis-killing runs are retained.
- Priority is `EIG × impact × decision-change × transferability / cost`.
- Added no-update and cost-per-credit stop signals.

Commit: `e3b236d`.

## Wave 3 — sealed must be a scoring API, and gates must be calibrated

Frontier KB input:
- Harness Evolver: the evolver must never observe sealed tasks; score via an API.
- Phase 0: calibrate gates on known-good / known-bad human controls before self-evolution.
- Transfer: a credited result must retain its lift off the development family.

Change:
- Added `RoutineSealedGate` / `CascadeSealedGate` and private battery manifests.
- Candidate search can run on open rows only.
- Added known-good / known-bad gate calibration.
- Added transfer-retention credit helper.

Commit: `3bdafa0`.

## Wave 4 — C must distinguish causal hypotheses

Frontier KB input:
- ABAB skill: C is the smallest discriminating experiment when experiment value exceeds more research.
- Useful-twin note: usefulness means predicting the next experiment, not producing a plausible demo.

Change:
- Added explicit hypothesis beliefs.
- C-stage expected information gain is computed from rival hypotheses' predicted outcomes.
- `choose_discriminating_test()` ignores hand-written EIG claims and chooses the test that actually separates explanations per unit cost.

Commit: `37322e5`.

## Wave 5 — offline evolution must not silently enter the hot path

Frontier KB input:
- Harness Evolver: offline control plane; only credited bundles reach rollout; shadow/canary before traffic.
- AODL intent contract: intent is stable; strategy is a compiled plan.

Change:
- Production AODL compilation now requires a `promoted` cascade.
- Uncredited candidates require explicit `allow_uncredited_shadow=True`.
- Shadow plans are marked `trafficEligible=false`.
- Added `docs/abab-loop.md` and the rollout boundary to the future slice.

Commit: `acec17f`.

## Current ABAB shape

```text
A: diagnose / propose
   highest priority = EIG × impact × decision-change × transferability / cost

B: execute frozen experiment
   validity -> activation -> blind sealed 2-sigma credit

archive:
   retain every result
   Pareto front per niche
   separate learned-only front
   record cost / failure class / killed hypothesis

C: rival explanations remain
   compute expected information gain
   run cheapest discriminating experiment

rollout:
   credited -> promoted -> AODL production plan
   uncredited -> shadow only
   future drift -> demote / new pathology

stop:
   repeated NO_UPDATE
   collapsed EIG
   rising cost / credit
   decision-invariant remainder
```

## What is deliberately still future work

- Counterexample-driven rule splitting and new-battery rotation.
- Clade/descendant scoring for experiment lineages.
- Signed rollout bundles / SBOM / cryptographic evolvability manifest.
- Real token/joule receipts from the near-term integration work.
- Cross-harness scoring API backed by truly inaccessible sealed storage rather than an in-process private object.

Those are now downstream improvements; they do not change the current core ABAB contract.
