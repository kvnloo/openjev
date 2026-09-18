# AODL integration

z0int binds its learned/deterministic implementation to **AODL/HOTL 0.2** without extending AODL's ontology.

## Ownership boundary

```text
AODL / HOTL 0.2
  intentGraph + Pi policies + Gamma constraints/budgets
                 |
                 v
z0int compiler plan bindings
  routine registry / specialist artifacts / model selectors
                 |
                 v
z0int runtime
  routine -> MB/fly -> local semantic -> frontier
                 |
                 v
AODL eventLog
  route -> verified stateUpdate
```

AODL remains the portable intent/authority/budget contract. z0int remains the implementation. Kerdoios may resolve residual **model resource placement** after z0int decides a model stage is required.

## No new node kinds

The mapping uses only existing AODL kinds:

| z0int concept | AODL kind |
| --- | --- |
| capability request | `task` |
| z0int decision runtime | `executor` |
| compiled deterministic routine evaluator | `service` |
| routine registry / specialist checkpoint | `artifact` |
| MB/fly/local SLM/frontier stage | `model` |
| outcome checker | `verifier` |
| token/outcome receipts | `stateStore` |

There is deliberately no `fly`, `mushroomBody`, `routine`, `cascade`, or `Jev` kind.

## Pi and Gamma

The cascade is expressed as routing policy `Pi`:

```text
first eligible:
  routine-service
  -> stage-mb
  -> stage-local_slm
  -> stage-frontier
```

The **abstract fallback order** and abstain-to-next semantics live in `policies.route`. Learned confidence thresholds, concrete checkpoint/provider selectors, and routine ids live in `plan.bindings` / `plan.route` so Evolution Lab can change an implementation without silently redefining user intent.

The hard product constraints live in `Gamma` (`constraints`):

- token and premium-token budgets;
- latency / money / joule budgets when known;
- capability precision floor;
- maximum allowed success regression;
- termination on verifier success;
- local-only personalized artifacts and confidential receipts.

Declared budgets are not observed spend. Runtime spend is written into AODL `stateUpdate` events / z0int receipts.

## Intent vs compiled plan vs observed runtime

The integration preserves AODL's three-object separation:

1. `intentGraph` describes portable nodes and relations.
2. `plan.bindings` binds stable route slots to z0int implementation artifacts/resource selectors and stores learned thresholds.
3. `eventLog` records actual route/outcome events.

Private rule predicates and model weights are not embedded in the intent graph. The intent graph can therefore be shared without turning a personal checkpoint into public IR.

## CLI

Given a promoted cascade JSON and optional promoted-routine JSONL:

```bash
PYTHONPATH=src python -m z0int.aodl compile \
  --capability coding.needs_verification \
  --cascade ~/.z0int/cascades/needs-verification.json \
  --routines ~/.z0int/routines/needs-verification.jsonl \
  --harness omp \
  --premium-tokens 1000 \
  --tokens 4000 \
  --output ~/.z0int/aodl/needs-verification.json
```

The authoritative conformance check remains in the AODL repository:

```bash
python3 tests/validate.py /path/to/needs-verification.json
```

z0int also performs a small local invariant check so it cannot emit custom node kinds, invalid port directions, schema-mismatched edges, or a document without `Gamma` budgets/termination.

## Runtime event bridge

`z0int.aodl.route_event()` records the selected route (including a routine id when one compiled rule fires). `z0int.aodl.outcome_event()` records externally verified success and observed token/latency spend as a causally linked `stateUpdate`.

This makes the AODL document usable as both the desired orchestration contract and the anchor for observed `O_t`, without making AODL itself a scheduler or training runtime.
