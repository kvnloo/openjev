# Contrastive evidence-sufficiency

Nimble-inspired **unit of learning**, not Nimble weights.

| Condition | Expect |
|-----------|--------|
| original | preserve decision |
| relevant_edit | flip decision |
| irrelevant_control | preserve |
| necessity_delete | abstain (not false) |

```bash
z0int contrastive eval --json --store
z0int autoresearch enqueue --trace-id T --verifier-id V --kind contrastive_evidence --json
```

`curation_accepted` ≠ `verified_success`. Not production-credit-eligible.
