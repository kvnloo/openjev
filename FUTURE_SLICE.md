# Future slice: Routine Compiler

Upstream snapshot: `kvnloo/z0int@696c99e9157883918f55c860c97b74ddbf2ba4a2`.

## Thesis

After capability discovery, outcome-backed evaluation, token receipts, Kerdoios integration,
and onboarding are working, the next downward-compilation step is to replace stable regions of
learned specialist behavior with deterministic routines.

```text
frontier LLM -> local SLM/Jev -> MB/fly specialist -> deterministic routine
```

The routine compiler does **not** invent new task semantics. It consumes an already-defined
`capability_id`, specialist traces, features, predictions, and verified outcomes.

## Minimal first slice

1. Define a portable `RoutineCandidate` schema:
   - `capability_id`
   - typed input feature names
   - predicate/rule AST
   - bounded action/output
   - support, coverage, precision, confidence interval
   - evidence level (L0-L3)
   - source specialist + generation
   - provenance / data split
2. Mine small deterministic regions from specialist traces with deliberately simple controls:
   - exact categorical lookup
   - shallow decision tree / conjunction rules
   - majority-on-region baseline
3. Validate candidates in strict stages:
   - train: discover rule
   - dev: tune only thresholds / prune
   - sealed: credit only; never discover
   - future: canary confirmation
4. Promote only when the rule:
   - meets the capability's precision floor,
   - does not regress verified outcome success,
   - has enough independent session/task support,
   - has lower execution cost than the specialist,
   - has a fail-open fallback to the specialist.
5. Runtime order after promotion:

```text
routine registry -> learned specialist -> local semantic model -> frontier model
```

6. Every misfire becomes a counterexample and can demote or split the rule.

## Why this is future work

This begins only after the near-term work now delegated elsewhere:
- capability miner / benchmark compiler,
- L2/L3 outcome joins,
- token receipts + Kerdoios integration,
- context offload,
- provider-neutral labeling,
- self-onboarding / installation.

It therefore avoids racing the current implementation and advances the longer-term z0int goal:
**repeated cognition should continuously migrate to the cheapest representation that still passes
its verifier.**

## Non-goals

- no new AODL kinds;
- no LLM-generated rules promoted without deterministic evaluation;
- no editing the sealed judge;
- no replacing a specialist solely because a rule matches its predictions on training data;
- no free-form code generation in the hot path.
