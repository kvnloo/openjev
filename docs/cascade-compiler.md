# Cascade Compiler

The cascade compiler optimizes the **representation ladder** for one bounded capability:

```text
routine -> MB/fly -> local semantic model -> frontier model
```

Kerdoios remains responsible for provider/resource allocation once a model stage is required.
z0int owns the earlier question: **can this decision safely stop at a cheaper representation?**

## Trace contract

Each evaluation row carries the verified target plus predictions/costs from every available stage:

```json
{
  "capability_id": "coding.needs_verification",
  "split": "dev",
  "session_id": "abc",
  "target": "yes",
  "predictions": {
    "routine": {"available": false},
    "mb": {
      "output": "yes",
      "confidence": 0.98,
      "premium_tokens": 0,
      "total_tokens": 0,
      "latency_ms": 0.3
    },
    "local_slm": {
      "output": "yes",
      "confidence": 0.91,
      "premium_tokens": 0,
      "total_tokens": 110,
      "latency_ms": 40
    },
    "frontier": {
      "output": "yes",
      "confidence": 1.0,
      "premium_tokens": 2200,
      "total_tokens": 2200,
      "latency_ms": 700
    }
  }
}
```

`premium_tokens` means the scarce quota being protected (Astra/Grok/frontier subscription or paid
API). Local inference can still report `total_tokens` for efficiency accounting while consuming
zero premium tokens.

## Optimization

`compile_cascade()` searches confidence thresholds **on dev only**. A candidate must satisfy:

```text
cascade success >= frontier baseline success - registered noninferiority margin
```

Among valid policies, V1 minimizes:

1. premium tokens per verified success;
2. then maximizes verified success;
3. then minimizes total tokens per success;
4. then minimizes latency.

The final frontier stage is terminal and must always be available, so every earlier stage fails open.

## Sealed credit

`credit_sealed()` applies the unchanged dev-selected thresholds to sealed sessions. Promotion requires
non-inferior success and lower premium-token consumption. Sealed examples do not participate in
threshold search.

## Future drift

A promoted cascade can be observed on future traffic. Once enough independent future sessions exist,
loss of non-inferiority demotes the policy. This returns traffic to the safer residual path until a new
policy is credited.

## Relationship to the Routine Compiler

A promoted deterministic routine appears as an optional first prediction stage with effectively zero
tokens. No match is represented as `available:false`, so the cascade falls through to the learned
specialist without special-case runtime code.

## Relationship to Kerdoios

The cascade is **not** a provider router. Example:

```text
z0int cascade says "needs model"
        -> WorkRequirement(capability_id=...)
        -> Kerdoios chooses local/free/premium execution portfolio
```

This keeps cognition decomposition separate from scarce-compute allocation.
