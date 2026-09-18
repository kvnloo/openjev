"""Contrastive evidence-sufficiency checks for context recipes.

Borrow Nimble's *measurement unit*, not its weights:

  original evidence          → preserve correct decision
  one relevant fact changes  → change decision appropriately
  irrelevant control         → preserve decision
  necessary evidence gone    → abstain / insufficient (not false)

Deterministic probes only. Synthetic lineage is explicit. Never maps
curation.accepted → verified_success.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from . import paths

SCHEMA_FAMILY = "z0int.contrast_family.v1"
SCHEMA_RESULT = "z0int.contrastive_eval.v1"
SCHEMA_DEPENDENCY = "z0int.evidence_dependency.v1"

Condition = Literal[
    "original",
    "relevant_edit",
    "irrelevant_control",
    "necessity_delete",
]


@dataclass
class EvidenceItem:
    id: str
    text: str
    necessary: bool = False
    # free-form fact key/value for deterministic probes
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContrastFamily:
    """Nearly-identical examples where one relevant fact flips the label."""

    family_id: str
    task_family: str
    question: str
    evidence: list[EvidenceItem]
    # original correct answer under full necessary evidence
    answer_original: str
    # answer after applying relevant_edit
    answer_after_relevant_edit: str
    relevant_edit: dict[str, Any]
    # which evidence ids are jointly necessary (deleting any → insufficient)
    necessary_ids: list[str]
    irrelevant_control: dict[str, Any] = field(default_factory=dict)
    source_family_id: str | None = None
    parent_example_id: str | None = None
    supervision: Literal["deterministic", "model_checked_synthetic", "live_verified"] = "deterministic"
    schema: str = SCHEMA_FAMILY

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "family_id": self.family_id,
            "task_family": self.task_family,
            "question": self.question,
            "evidence": [e.to_dict() for e in self.evidence],
            "answer_original": self.answer_original,
            "answer_after_relevant_edit": self.answer_after_relevant_edit,
            "relevant_edit": dict(self.relevant_edit),
            "necessary_ids": list(self.necessary_ids),
            "irrelevant_control": dict(self.irrelevant_control),
            "source_family_id": self.source_family_id,
            "parent_example_id": self.parent_example_id,
            "supervision": self.supervision,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ContrastFamily":
        ev = [
            EvidenceItem(
                id=str(e["id"]),
                text=str(e.get("text") or ""),
                necessary=bool(e.get("necessary", False)),
                facts=dict(e.get("facts") or {}),
            )
            for e in (raw.get("evidence") or [])
        ]
        return cls(
            family_id=str(raw["family_id"]),
            task_family=str(raw.get("task_family") or "unknown"),
            question=str(raw.get("question") or ""),
            evidence=ev,
            answer_original=str(raw["answer_original"]),
            answer_after_relevant_edit=str(raw["answer_after_relevant_edit"]),
            relevant_edit=dict(raw.get("relevant_edit") or {}),
            necessary_ids=[str(x) for x in (raw.get("necessary_ids") or [])],
            irrelevant_control=dict(raw.get("irrelevant_control") or {}),
            source_family_id=raw.get("source_family_id"),
            parent_example_id=raw.get("parent_example_id"),
            supervision=raw.get("supervision") or "deterministic",  # type: ignore[arg-type]
        )


def _facts_map(items: list[EvidenceItem]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        for k, v in it.facts.items():
            out[k] = v
    return out


def default_decision_probe(items: list[EvidenceItem], *, question: str = "") -> str | None:
    """Deterministic probe: answer from facts['decision'] if all necessary present.

    Returns None when any necessary evidence id is missing → insufficient, not false.
    """
    _ = question
    necessary = [it for it in items if it.necessary]
    if necessary and any(it.id.startswith("__missing__") for it in items):
        return None
    # if caller passed filtered list missing necessary, abstain
    # (probe itself doesn't know original necessary set — caller enforces)
    facts = _facts_map(items)
    if facts.get("_insufficient") is True:
        return None
    ans = facts.get("decision")
    if ans is None or ans == "":
        return None
    return str(ans)


def apply_condition(
    family: ContrastFamily,
    condition: Condition,
    *,
    keep_ids: set[str] | None = None,
) -> list[EvidenceItem]:
    """Materialize evidence under one of the four conditions (+ optional recipe filter)."""
    items = [copy.deepcopy(e) for e in family.evidence]
    if condition == "original":
        pass
    elif condition == "relevant_edit":
        edit = family.relevant_edit
        target_id = str(edit.get("evidence_id") or "")
        fact_key = str(edit.get("fact") or "decision")
        new_val = edit.get("value")
        for it in items:
            if it.id == target_id or (not target_id and fact_key in it.facts):
                it.facts = dict(it.facts)
                it.facts[fact_key] = new_val
                if "text_suffix" in edit:
                    it.text = f"{it.text} {edit['text_suffix']}".strip()
                break
    elif condition == "irrelevant_control":
        ctrl = family.irrelevant_control or {"evidence_id": items[0].id if items else "", "text_suffix": " [fmt]"}
        target_id = str(ctrl.get("evidence_id") or (items[0].id if items else ""))
        for it in items:
            if it.id == target_id:
                it.text = f"{it.text}{ctrl.get('text_suffix', ' [irrelevant]')}".strip()
                # deliberately do NOT change facts
                break
    elif condition == "necessity_delete":
        drop = set(family.necessary_ids)
        if not drop and items:
            drop = {it.id for it in items if it.necessary}
        items = [it for it in items if it.id not in drop]
        # mark insufficiency for probes that only see remaining facts
        if items:
            items[0].facts = dict(items[0].facts)
            items[0].facts["_insufficient"] = True
        else:
            items = [
                EvidenceItem(
                    id="__missing__all",
                    text="",
                    necessary=False,
                    facts={"_insufficient": True},
                )
            ]
    else:
        raise ValueError(f"unknown condition: {condition}")

    if keep_ids is not None:
        items = [it for it in items if it.id in keep_ids]
        # if any necessary id was filtered out, force insufficient
        needed = set(family.necessary_ids) or {it.id for it in family.evidence if it.necessary}
        if needed - set(keep_ids):
            if items:
                items[0].facts = dict(items[0].facts)
                items[0].facts["_insufficient"] = True
            else:
                items = [
                    EvidenceItem(
                        id="__missing__recipe",
                        text="",
                        facts={"_insufficient": True},
                    )
                ]
    return items


def expected_answer(family: ContrastFamily, condition: Condition) -> str | None:
    if condition == "original":
        return family.answer_original
    if condition == "relevant_edit":
        return family.answer_after_relevant_edit
    if condition == "irrelevant_control":
        return family.answer_original
    if condition == "necessity_delete":
        return None  # insufficient
    raise ValueError(condition)


def evaluate_family(
    family: ContrastFamily,
    *,
    probe: Callable[..., str | None] | None = None,
    keep_ids: set[str] | None = None,
    recipe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run four conditions. pair_pass = both sides of relevant contrast correct."""
    fn = probe or default_decision_probe
    conditions: list[Condition] = [
        "original",
        "relevant_edit",
        "irrelevant_control",
        "necessity_delete",
    ]
    rows: dict[str, Any] = {}
    for c in conditions:
        items = apply_condition(family, c, keep_ids=keep_ids)
        # necessity: if required evidence missing from items, probe must abstain
        needed = set(family.necessary_ids) or {it.id for it in family.evidence if it.necessary}
        present = {it.id for it in items if not str(it.id).startswith("__missing__")}
        if c != "necessity_delete" and needed - present:
            pred = None
        else:
            pred = fn(items, question=family.question)
        exp = expected_answer(family, c)
        ok = pred == exp
        rows[c] = {
            "condition": c,
            "predicted": pred,
            "expected": exp,
            "ok": ok,
            "n_evidence": len([i for i in items if not str(i.id).startswith("__missing__")]),
        }

    pair_pass = bool(rows["original"]["ok"] and rows["relevant_edit"]["ok"])
    robust = bool(rows["irrelevant_control"]["ok"] and rows["necessity_delete"]["ok"])
    full_pass = pair_pass and robust

    dep = {
        "schema": SCHEMA_DEPENDENCY,
        "family_id": family.family_id,
        "task_family": family.task_family,
        "necessary_evidence_ids": list(family.necessary_ids),
        "relevant_edit": dict(family.relevant_edit),
        "irrelevant_control": dict(family.irrelevant_control),
        "answer_original": family.answer_original,
        "answer_after_relevant_edit": family.answer_after_relevant_edit,
        "acquisition_recipe": recipe,
        "keep_ids": sorted(keep_ids) if keep_ids is not None else None,
        "supervision": family.supervision,
        "parent_example_id": family.parent_example_id,
        "source_family_id": family.source_family_id,
        # NOT verified_success — synthetic/eval only
        "curation_accepted": full_pass,
    }

    return {
        "schema": SCHEMA_RESULT,
        "family_id": family.family_id,
        "task_family": family.task_family,
        "conditions": rows,
        "pair_pass": pair_pass,
        "robust_pass": robust,
        "full_pass": full_pass,
        "dependency": dep,
        "recipe": recipe,
        "evaluated_at": time.time(),
    }


def recipe_keep_ids(family: ContrastFamily, recipe: dict[str, Any]) -> set[str]:
    """Map a context-policy-like recipe onto evidence ids.

    recipe keys:
      keep_evidence_ids: explicit allow-list
      drop_evidence_ids: deny-list
      keep_necessary_only: bool
      ops / ablate: ignored here (policy cost lives in autoresearch replay)
    """
    all_ids = {e.id for e in family.evidence}
    if recipe.get("keep_evidence_ids"):
        return set(str(x) for x in recipe["keep_evidence_ids"]) & all_ids
    keep = set(all_ids)
    if recipe.get("drop_evidence_ids"):
        keep -= {str(x) for x in recipe["drop_evidence_ids"]}
    if recipe.get("keep_necessary_only"):
        nec = set(family.necessary_ids) or {e.id for e in family.evidence if e.necessary}
        keep &= nec
    return keep


def evaluate_recipe_on_family(family: ContrastFamily, recipe: dict[str, Any]) -> dict[str, Any]:
    keep = recipe_keep_ids(family, recipe)
    out = evaluate_family(family, keep_ids=keep, recipe=recipe)
    out["recipe_keep_ids"] = sorted(keep)
    return out


def example_project_status_family() -> ContrastFamily:
    """Built-in deterministic fixture: project status depends on RFC rev + supersede."""
    return ContrastFamily(
        family_id="fixture.project_status.v1",
        task_family="context.project_status",
        question="What is the current accepted plan revision for this project?",
        evidence=[
            EvidenceItem(
                id="rfc_rev",
                text="RFC-12 revision is 3.",
                necessary=True,
                facts={"rfc_revision": 3, "decision": "rev-3"},
            ),
            EvidenceItem(
                id="supersede",
                text="Decision log: no superseding plan.",
                necessary=True,
                facts={"superseded": False},
            ),
            EvidenceItem(
                id="old_summary",
                text="Yesterday summary still said rev-2 was active.",
                necessary=False,
                facts={"stale_summary": "rev-2"},
            ),
            EvidenceItem(
                id="unrelated_chat",
                text="Unrelated chat about lunch.",
                necessary=False,
                facts={"chat": "lunch"},
            ),
        ],
        answer_original="rev-3",
        answer_after_relevant_edit="rev-4",
        relevant_edit={
            "evidence_id": "rfc_rev",
            "fact": "decision",
            "value": "rev-4",
            "text_suffix": "(edited: revision is 4)",
        },
        necessary_ids=["rfc_rev", "supersede"],
        irrelevant_control={"evidence_id": "unrelated_chat", "text_suffix": " [whitespace reformatted]"},
        source_family_id="fixture",
        parent_example_id="fixture.project_status.base",
        supervision="deterministic",
    )


def store_dependency(record: dict[str, Any]) -> Path:
    d = paths.home() / "evidence_dependencies"
    d.mkdir(parents=True, exist_ok=True)
    fid = str(record.get("family_id") or "unknown")
    path = d / f"{fid.replace('/', '_')}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # also append journal
    journal = d / "journal.jsonl"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), **record}, sort_keys=True) + "\n")
    return path


def family_fingerprint(family: ContrastFamily) -> str:
    blob = json.dumps(family.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
