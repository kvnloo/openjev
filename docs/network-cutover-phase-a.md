# Network cutover — Phase A (z0intelligence)

Implements the P0 control-plane pieces from `z0intelligence-network-cutover`:

- **Production preflight** (`z0int.preflight`) — no Evolution Lab on the live path
- **Candidate artifacts** (`z0int.artifacts`) — hash-verified import; never promotes
- **autoresearchd** (`z0int.autoresearch`) — verified-trajectory context-policy ABAB V0
- **Kerdoios projection** (`z0int kerdoios export-observations`)
- **Harness identity** (`z0int.harness_id`)

Still `execution=log_only`. Do not flip live host consume in this change.

## CLI

```bash
z0int preflight "…"
z0int artifacts inspect|import|list …
z0int autoresearch status|enqueue|run-once|daemon|pause|resume|report
z0int kerdoios export-observations --output …
```
