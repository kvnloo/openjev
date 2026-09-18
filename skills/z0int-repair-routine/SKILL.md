---
name: z0int-repair-routine
description: Repair drifted routines with fresh outcome evidence.
version: 0.1.0
license: MIT
---

# Repair a drifting routine

Use this workflow after a bounded, outcome-backed routine has been demoted. Do not use it to invent a new capability or to turn teacher agreement into a production claim.

Read `docs/routine-repair.md`. Use the current harness's terminal and file tools; the canonical implementation is `python -m z0int.refinement`. Source-only checkouts can run it with `PYTHONPATH=src`. This path does not need GPU or LLM dependencies.

## Procedure

1. Inspect the parent and the input provenance. It must be demoted, have L2/L3 evidence, and have verified counterexamples in multiple sessions. Ask the owning verifier for missing evidence; never synthesize targets.
2. Select an explicit allowlist of available **pre-decision** features. Keep credentials and post-decision information out. Prepare disjoint train/dev inputs and run `propose`. Stop on `no_update`.
3. Freeze the proposal artifact. Use a separate trusted scoring process for new sealed sessions. Keep a stable `~/.z0int/routine-repairs/credit.sqlite3` journal across attempts. Do not give sealed rows to the proposer or choose a runner-up after reading credit results.
4. After accepted sealed credit, score unchanged behavior on new future sessions. A rejected result stays rejected. A new proposal needs genuinely fresh evaluation cohorts.
5. Export `shadow-plan` and inspect `trafficEligible=false`, the candidate hash, and `routine_registry.decide_shadow`. Only an explicit user-approved `activate --approve` may write a new local registry after both receipts pass. That operation does not authorize a live harness rollout.
6. Keep the old registry and the `rollback` command. Rollback must disable the child without restoring the drifting parent. Report counts, support, and the bounded evidence claim. Leave token savings and whole-task quality unknown until the separate harness benchmark measures them.

## Verification

Run `PYTHONPATH=src python -m pytest -q tests/test_z0int_refinement.py tests/test_z0int_refinement_cli.py`.
The deterministic fixture is `PYTHONPATH=src python examples/refinement/demo.py --output /tmp/z0int-repair-demo`; choose a new output path if it exists.

A passing synthetic fixture verifies the implementation path, not usefulness on the user's real workload. Do not change the judge, bypass the user's participation contract, or enable a new cascade because its parent was previously credited.
