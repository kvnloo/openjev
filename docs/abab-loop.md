# ABAB credit loop

z0int's future research loop follows the Frontier KB invariant that **experiments are the population**.
Models, data recipes, routines, cascades and policies are implementation variables inside an experiment;
none is the fitness by itself.

## A / B / C

```text
A  diagnose the highest-value uncertainty
   priority = EIG × impact × decision-change × transferability / cost

B  execute one frozen experiment
   validity -> activation -> sealed credit

C  when rival hypotheses remain, run the cheapest discriminating experiment
   whose expected information gain exceeds the value of more research
```

C's EIG is computed from rival hypotheses' differing predicted outcomes; the loop does not trust a
hand-written claim that an experiment is informative when a cheaper test separates the hypotheses better.

## Credit is not a point estimate

- Routine regions require meaningful lift over the trivial output prior, with an approximately 2-sigma gate.
- Cascades are compared to the frontier baseline **paired on the same sealed tasks**. Promotion uses the lower
  confidence bound of the success delta, not only the mean success percentage.
- Activation is explicit: a cascade that never actually absorbs a row is dead code, even if its metrics look safe.
- Transfer retention is a separate credit dimension; a niche result that evaporates across task families is not a
  transferable win.

## Battery separation

Search receives only open/train/dev data. `RoutineSealedGate` and `CascadeSealedGate` expose a tiny scoring API;
sealed rows are not an argument to candidate generation. A battery manifest exposes only an id and counts.

Before self-evolution, calibrate the gate on known-good and known-bad controls. If the gate cannot reliably accept
known-good changes and reject known-bad ones, stop: the methodology is not ready to optimize itself.

## Archive

Every experiment is retained, including failures. The archive exposes:

- Pareto fronts per capability/pathology niche;
- a separate learned-only front so deterministic/free controls do not hide learned progress;
- failure classes and hypothesis-killing negative results;
- cost per credited experiment;
- no-update streaks and rising-cost stop rules.

A globally losing experiment may remain a niche champion or stepping stone.

## Rollout boundary

Credited research remains offline until rollout. AODL production compilation accepts only a `promoted` cascade.
Uncredited policies may be compiled only with `allow_uncredited_shadow=True`; the resulting plan is marked
`trafficEligible=false`. Thus Evolution Lab may explore freely while the hot plane only executes credited strategy.

## Stop

Stop or change the research agenda when:

- repeated `NO_UPDATE` / rejected experiments collapse information gain;
- all remaining proposals fall below the priority floor;
- cost per credited experiment rises repeatedly;
- a C experiment dominates another research wave;
- the decision is already invariant to the unresolved uncertainty.

The target is not more experiments. It is **validated understanding and verified frontier movement per unit cost**.
