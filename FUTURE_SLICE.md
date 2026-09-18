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

## Local implementation status

Implemented on `feat/routine-compiler`:

- `07bc4f8` — Routine Compiler V1
  - safe declarative rule AST (`eq`/`ge`/`le`, conjunction depth <=2)
  - train-only discovery, dev pruning, sealed credit, future drift demotion
  - independent-session support floors and prior-lift guard
  - fail-open runtime `RoutineRegistry`
  - JSONL round-trip + CLI
- `fc5ba5b` — Cascade Compiler V1
  - dev-only confidence-threshold search
  - objective: premium tokens per verified success
  - frontier baseline non-inferiority constraint
  - sealed-only credit + future drift demotion
  - optional routine stage represented as `available:false` on no match
  - provider-independent; Kerdoios remains residual compute allocator

Local verification: 9 unit tests passing.

Next future slice: counterexample-driven routine splitting / refinement.
## AODL binding (implemented)

Routine Compiler + Cascade Compiler now compile to HOTL/AODL 0.2 without extending the ontology. The stable intent graph uses only `task`, `executor`, `service`, `model`, `artifact`, `verifier`, and `stateStore`. Abstract fallback order and Gamma budgets/acceptance stay in AODL; learned thresholds, routine ids, checkpoint/provider selectors, and harness bindings stay in the compiled `plan` so Evolution Lab can change implementation without silently changing user intent. Runtime route/outcome receipts can be appended as AODL `route` + `stateUpdate` events.

See `docs/aodl-integration.md`, `src/z0int/aodl.py`, and `examples/aodl/routine-cascade.json`.


## ABAB meta-loop hardening

The future branch now codifies the Frontier KB research discipline in `src/z0int/abab.py`, `credit.py`, and
`battery.py`:

- 2-sigma sealed credit rather than point-estimate keeps;
- paired non-inferiority for cascade vs frontier baseline;
- explicit validity -> activation -> credit gates;
- evolver-blind sealed scoring interfaces;
- Phase-0 calibration on known-good/known-bad controls;
- Pareto/MAP-Elites niche archive + separate learned-only front;
- negative-result / killed-hypothesis retention;
- EIG-weighted experiment priority and C-stage discriminating tests;
- cost-per-credit and no-update stop rules;
- AODL hot-plane refusal of uncredited strategies (shadow remains explicit).

See `docs/abab-loop.md`.
