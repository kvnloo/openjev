# Routine Compiler

The routine compiler is z0int's final downward-compilation stage:

```text
frontier model -> local semantic model -> learned specialist -> deterministic routine
```

A routine is only eligible after a capability already exists with a bounded output contract.
It is not a general program synthesizer and it never executes generated Python.

## Data contract

Input JSONL rows contain:

```json
{
  "capability_id": "coding.needs_verification",
  "split": "train",
  "session_id": "session-123",
  "features": {"prev_family": "EDIT", "tool_ok": true},
  "target": "VERIFY",
  "evidence_level": "L2_outcome"
}
```

For L2/L3, `target` must come from an external outcome/verifier. Teacher or historical-action
labels are L0/L1 evidence and must stay labeled as such.

## Leakage boundary

- **train** discovers predicates and outputs;
- **dev** filters/prunes candidates;
- **sealed** only credits unchanged candidates;
- **future** observes canary traffic and may demote a credited routine.

Sealed rows are never passed to `mine_routines()`.

## Rule language

V1 supports conjunctions of one or two scalar predicates:

- `eq(feature, value)`
- `ge(feature, number)`
- `le(feature, number)`

No `eval`, code generation, calls, regex execution, filesystem access, or arbitrary expressions.
A no-match always fails open to the learned specialist.

## Promotion

`compile_and_credit()` promotes only candidates that:

1. exceed the configured precision floor on train, dev, and sealed;
2. have enough matched rows and independent sessions;
3. improve precision over the global prior for their output;
4. preserve the exact rule discovered before sealed evaluation.

Wilson lower bounds are recorded and can be required by configuration when sample sizes warrant it.

## Future drift

`RoutineRegistry.observe_future()` waits for a configurable minimum number of matched rows and
independent sessions. A rule that then falls below its precision floor is marked `demoted` and
stops executing; the specialist becomes the fallback again.

## CLI

With the package installed:

```bash
python -m z0int.routines compile \
  --input ~/.z0int/routines/needs-verification.jsonl \
  --output ~/.z0int/routines/registry.jsonl \
  --capability coding.needs_verification \
  --specialist mb-gen3 \
  --evidence L2_outcome

python -m z0int.routines apply \
  --registry ~/.z0int/routines/registry.jsonl \
  --capability coding.needs_verification \
  --features '{"prev_family":"EDIT","tool_ok":true}'
```

The runtime should call this registry **before** the learned specialist only after the routine has
been promoted. Routine execution is expected to be effectively zero-token and sub-millisecond.
