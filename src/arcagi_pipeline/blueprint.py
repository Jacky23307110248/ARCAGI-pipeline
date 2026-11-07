"""Game-neutral links between evidence, hypotheses, course slots and construction."""
from __future__ import annotations
from typing import Any

def validate_design(evidence: dict, rules: dict, blueprint: dict) -> list[str]:
    errors = []
    if not all(isinstance(value,dict) for value in (evidence,rules,blueprint)):
        return ["design inputs must be objects"]
    def indexed(rows, label):
        if not isinstance(rows, list) or not rows:
            errors.append(label + " must be nonempty")
            return {}
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                errors.append(label + " entries require IDs")
                continue
            if row["id"] in result: errors.append(label + " has duplicate IDs")
            result[row["id"]] = row
        return result
    ev = indexed(evidence.get("evidence"), "evidence")
    rs = indexed(rules.get("rules"), "rules")
    slots = indexed(blueprint.get("slots"), "slots")
    for row in ev.values():
        if not row.get("source_ref") or not row.get("kind"):
            errors.append("evidence requires source_ref and kind")
    for row in rs.values():
        if not row.get("statement") or not isinstance(row.get("status"),str) or row.get("status") not in {"hypothesis", "supported", "conflicting", "unknown"}:
            errors.append("rule requires statement and epistemic status")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(x,str) or x not in ev for x in refs):
            errors.append("rule has missing or unknown evidence_refs")
    previous = set()
    for sid, slot in slots.items():
        if not slot.get("purpose") or not slot.get("rationale"):
            errors.append("slot requires purpose and rationale")
        deps = slot.get("prerequisites", [])
        selected = slot.get("rule_ids", [])
        refs = slot.get("evidence_refs", [])
        if not isinstance(deps, list) or any(not isinstance(x,str) or x not in previous for x in deps):
            errors.append("slot has unknown or forward prerequisites")
        if not isinstance(selected, list) or any(not isinstance(x,str) or x not in rs for x in selected):
            errors.append("slot has unknown rule_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(x,str) or x not in ev for x in refs):
            errors.append("slot has missing or unknown evidence_refs")
        # No monotonicity, role vocabulary, mechanism or slot-count assumption.
        previous.add(sid)
    return errors

def validate_construction(task, rules: dict, blueprint: dict) -> list[str]:
    c = task.construction
    slots = {s["id"]: s for s in blueprint["slots"]}
    if not isinstance(c,dict): return ["construction must be an object"]
    if not isinstance(c.get("slot_id"),str) or c.get("slot_id") not in slots: return ["construction has unknown slot_id"]
    slot = slots[c["slot_id"]]
    selected = c.get("rule_ids")
    errors = []
    if not isinstance(selected, list) or any(not isinstance(x,str) for x in selected) or set(selected) != set(slot.get("rule_ids", [])):
        errors.append("construction rule_ids differ from declared slot rule selection")
    if not isinstance(c.get("config"), dict) or not c.get("rationale"):
        errors.append("construction requires config and rationale")
    # Presence versus necessity is a claim of the game plugin, not a core inference.
    claims = c.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("construction requires testable claims")
    else:
        ids = set()
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("id"),str) or not claim["id"] or claim["id"] in ids:
                errors.append("claim IDs must be nonempty and unique")
                continue
            ids.add(claim["id"])
            if not claim.get("statement") or not claim.get("check"):
                errors.append("claim requires statement and check description")
    return errors

def validate_fulfillment(task, assessment) -> list[str]:
    expected = {c["id"] for c in task.construction["claims"]}
    checked = assessment.checks
    if not isinstance(checked, dict) or set(checked) != expected:
        return ["fulfillment must address every construction claim"]
    errors = []
    for row in checked.values():
        if not isinstance(row, dict) or not isinstance(row.get("status"),str) or row.get("status") not in {"pass", "return", "unknown"} or not isinstance(row.get("evidence"), dict) or not row["evidence"]:
            errors.append("claim check requires status and recorded evidence")
            continue
        evidence = row["evidence"]
        if not isinstance(evidence.get("kind"), str) or not evidence["kind"].strip() or "data" not in evidence or evidence["data"] is None:
            errors.append("claim evidence requires kind and non-null data")
    return errors
