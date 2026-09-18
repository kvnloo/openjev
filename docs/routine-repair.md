# Counterexample-driven routine repair

## Why this slice

The previous routine registry could demote a drifting rule but could not recover its useful region. This slice closes that gap without another model, another provider integration, or an unbounded research run.

```text
promoted routine develops verified counterexamples
                  |
                  v
demote the parent, preserving specialist fallback
                  |
                  v
open train data: propose positive guards on approved features
                  |
                  v
open dev data: choose ONE narrower rule, then freeze
                  |
                  v
fresh sealed sessions -> aggregate credit receipt
                  |
                  v
new future sessions -> independent confirmation
                  |
                  v
explicit local approval -> new registry artifact
                  |
                  v
shadow-only AODL composition -> separate harness/cascade credit
```

A repair retains every parent predicate and exactly the same output. It adds one guard. It does not guess a new action for the failing region. Unmatched cases continue to the specialist. Missing guard features and unseen categorical values fail to match. Numeric guards use the existing `ge`/`le` operators, not generated code.

This is a bounded offline component. It does not call an LLM, start a GPU job, schedule a daemon, modify an upstream repository, or connect to a live harness.

## Entry points

`src/z0int/refinement.py` provides `propose_repair`, `score_repair`, `activate_repair`, and `rollback_repair`. `export_shadow_plan` composes a candidate with the existing AODL bridge.

Run the synthetic integration fixture from this source overlay:

```bash
PYTHONPATH=src python examples/refinement/demo.py --output /tmp/z0int-repair-demo
```

The output directory must not exist. The fixture uses no network or model weights. Its report explicitly leaves real token savings and real task quality unknown.

## Input contract

The input is not an old next-action row with `tier=gold`. It needs an externally checked bounded target and a pre-decision feature snapshot:

```json
{
  "episode_id": "harness:session-7:decision-23",
  "session_id": "harness:session-7",
  "capability_id": "coding.needs_verification",
  "split": "train",
  "session_started_at": 1789680000,
  "features_at": 1789680010,
  "decision_at": 1789680011,
  "observed_at": 1789680014,
  "features": {"prev_family": "EDIT", "tool_ok": true, "phase": "batch"},
  "target": "VERIFY",
  "verified": true,
  "evidence_level": "L2_outcome",
  "verifier_id": "your-bounded-task-verifier-v1"
}
```

Timestamps are an illustrative schema example, not a measurement. Namespace IDs by harness and source. `tool_ok` above describes the *previous* action, available before this decision. A post-decision success bit must never become an input feature.

The scorer requires `session_started_at <= features_at <= decision_at <= observed_at`. Sealed sessions must start after proposal freeze. Future sessions must start after sealed credit was issued. Open train and dev sessions must be disjoint. Duplicate episode IDs are errors, not additional evidence.

The caller is responsible for the validity of the target and timestamps. `verified=true` is a declaration, not cryptographic proof. Tool exit status alone does not establish that a next action was correct. Ambiguous targets, multiple acceptable actions, and causal credit assignment require an upstream capability-specific verifier; this slice intentionally supports one bounded scalar target only.

## Bounded search

A `RepairConfig` explicitly names the available pre-decision features. Search does not inspect every receipt key. Guards are discovered only on train; dev selects among them. Candidate generation has no sealed/future parameter.

Default limits include a 64-guard search budget and a maximum total rule depth of four. The effective precision floor is the maximum of the parent's floor and the configured floor. There must be independent counterexamples, meaningful matched support, and a nontrivial retained region. Otherwise the result is `no_update`, with no candidate.

No runtime-generated Python, `eval`, or negative-only exclusion list is involved. For example, prefer a measured `phase == batch` region over an unbounded claim that every phase except one is safe.

## Fresh credit and reuse control

`RepairTrialStore` is a private SQLite credit-access journal. It is not another runtime telemetry system and does not replace Kerdoios receipts or the live Jev stream. Keep one stable store for the repair campaign:

```text
~/.z0int/routine-repairs/credit.sqlite3
```

A transaction binds each sealed/future attempt to the exact proposal, candidate, configuration, and cohort-content digests. Both accepted and rejected attempts consume their session and episode IDs. A process restart cannot retry a different candidate on an already consumed cohort. An identical retry returns the original receipt. Changing the data for a previously scored proposal fails. Concurrent attempts on one cohort cannot both succeed.

This is an operational access boundary, not a security sandbox. The filesystem owner can delete the store or forge inputs. Run the scoring command under a separately permissioned service/account when the proposing agent must be technically unable to read evaluation examples. A Python object or a hash does not create that isolation.

## What the gate measures

Both sealed and future credit require sufficient matched events and sessions. They also require:

- observed bounded-target precision at the capability's floor;
- a nominal 95% Wilson lower bound, applied to the fraction of **matched sessions with no errors**, at that floor;
- a cap on the share of evidence coming from one session;
- meaningful retention of the parent's region.

The all-correct-session statistic is deliberately stricter than event accuracy. A long repeated session cannot produce narrow uncertainty merely by contributing many rows. It is not a distribution-free guarantee, and real sessions may still be correlated across a project or user. Transfer across independent projects remains a separate experiment. Failed trials consume their cohorts, but this alone is not a global multiple-testing correction for an unlimited research campaign.

A passed receipt claims **bounded-target agreement on prospective sessions**. It does not claim improved whole-task success, optimal decisions, saved subscription quota, or lower energy. Those cells remain `null` until measured in the actual harness. The existing routine metrics are carried for registry compatibility; the repair gate does not use the older global-prior lift test as its authority.

## CLI workflow

All paths below refer to private artifacts. No stage overwrites an output file.

```bash
# Freeze one proposal from a demoted parent's open evidence.
PYTHONPATH=src python -m z0int.refinement propose \
  --parent parent.json --train train.jsonl --dev dev.jsonl \
  --config repair-config.json --output proposal.json

# The scorer receives a new cohort after proposal freeze.
PYTHONPATH=src python -m z0int.refinement score \
  --proposal proposal.json --input fresh-sealed.jsonl --phase sealed \
  --store "$HOME/.z0int/routine-repairs/credit.sqlite3" --output sealed-credit.json

# Confirm unchanged behavior on sessions starting after the sealed receipt.
PYTHONPATH=src python -m z0int.refinement score \
  --proposal proposal.json --input fresh-future.jsonl --phase future \
  --store "$HOME/.z0int/routine-repairs/credit.sqlite3" --output future-credit.json

# Explicit approval writes a NEW local registry; it does not enable a harness.
PYTHONPATH=src python -m z0int.refinement activate \
  --proposal proposal.json --registry old-registry.jsonl \
  --store "$HOME/.z0int/routine-repairs/credit.sqlite3" \
  --approve --output repaired-registry.jsonl

# The modified cascade gets SHADOW status even when the old cascade was promoted.
PYTHONPATH=src python -m z0int.refinement shadow-plan \
  --proposal proposal.json --cascade cascade.json --output aodl-shadow.json

# Rollback disables the child, never resurrecting the unsafe parent.
PYTHONPATH=src python -m z0int.refinement rollback \
  --registry repaired-registry.jsonl --proposal-id REPLACE_WITH_PROPOSAL_ID \
  --output rolled-back-registry.jsonl
```

A minimal config file is `{"allowed_features":["phase"]}`. Defaults are intentionally conservative; small corpora can yield `no_update` or rejected credit. Do not lower the gate after seeing sealed results to manufacture a pass.

## Activation and AODL

Activation reads the accepted receipts from the trial store rather than trusting a caller-provided `passed=true`. It checks the unchanged parent artifact before replacing anything. The old registry is not mutated, and the demoted parent remains disabled. Repeating the same activation is idempotent; stale or conflicting artifacts fail closed.

A new routine does **not** inherit the old cascade's production credit. `export_shadow_plan` forces `trafficEligible=false`, binds the candidate artifact by hash, and uses the real `RoutineRegistry.decide_shadow` entrypoint. Ordinary `decide` continues to ignore candidate rules. The host must honor the shadow flag; this package does not install a live dispatcher.

The intent graph, acceptance contract, and `sourceHash` stay unchanged for an implementation-only repair. `planHash` changes with the repair artifact and with shadow/production mode. Provider placement remains Kerdoios work. No AODL kinds were added.

## ABAB integration

A diagnoses a demoted region from open counterexamples. B searches a bounded set of guards and freezes one dev-selected candidate. The scorer returns aggregate evidence and preserves rejections. The supplied demo writes compatible `ExperimentRecord` entries into the existing `ExperimentArchive`, marking the implementation deterministic (`learned=false`).

No numerical EIG is invented for a deterministic repair task. An outer agent can use the existing discriminating-test interface when it has genuinely supported rival predictions. Empty or failed repair attempts are a reason to change the agenda, not permission to repeatedly inspect the same sealed cohort.

## Verification

```bash
PYTHONPATH=src python -m pytest -q
```

This command tests the supplied future-feature overlay. It is not a claim that the complete upstream z0int, Hermes, OMP, or AODL repository suites were run. The generated AODL plan is checked against the existing local bridge invariants; the separate upstream nightly validator remains an integration check for a full checkout.
