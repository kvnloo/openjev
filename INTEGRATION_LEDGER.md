# Integration ledger — z0int-future-routine-repair → current z0int

**Base SHA:** `b2d43f45a7fa13d6ab34aa2bcab00d8e9cf1e16f`  
**Branch:** `integrate/z0int-future-stack`  
**Archive:** `~/Downloads/z0int-future-routine-repair.zip` (SHA256SUMS verified)

## Semantic inventory (pre-apply)

| Feature | Path | Class |
|---|---|---|
| routines | `src/z0int/routines.py` | ABSENT |
| cascade | `src/z0int/cascade.py` | ABSENT |
| aodl bridge | `src/z0int/aodl.py` | ABSENT |
| credit | `src/z0int/credit.py` | ABSENT |
| battery | `src/z0int/battery.py` | ABSENT |
| abab | `src/z0int/abab.py` | ABSENT |
| refinement/repair | `src/z0int/refinement.py` | ABSENT |
| receipt spine | `src/z0int/receipt.py` | ALREADY EQUIVALENT (canonical; not duplicated) |
| CLI / onboard | `src/z0int/cli.py` | ALREADY EQUIVALENT (extended, not replaced) |
| capability_id | used across specialists | ALREADY EQUIVALENT |

## Patches 0001–0014

All applied cleanly via `git am --3way` (no conflicts, no skips).

## Compatibility work (this commit)

- Root CLI delegates: `z0int routine|cascade|aodl|repair|abab`
- Thin `z0int.abab` CLI (`summary` / `next` / `stop`)
- README + ROADMAP honesty sections (synthetic measured vs not product-validated)
- No second token ledger; receipt remains canonical
- AODL fixture validates on `kvnloo/aodl@nightly` (`9e8d616`)

## Gaps left intentional

- Live multi-session outcome join still required before production traffic
- ABAB is library + thin CLI; no autonomous overnight runner wired to GPU evolve yet
- Kerdoios provider selection remains external to this stack
