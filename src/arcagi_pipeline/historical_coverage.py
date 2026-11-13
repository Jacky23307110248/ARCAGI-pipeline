"""Recheck pinned historical behavior against the revised engine sessions."""

def requirements(blueprint):
    for slot in blueprint["slots"]:
        for row in slot.get("behavior_requirements", []):
            predicate = row["predicate"]
            normalized = dict(row)
            if isinstance(predicate, dict):
                normalized["predicate"] = predicate["type"]
                if "value" in predicate:
                    normalized["value"] = predicate["value"]
            yield slot["id"], normalized


def mappings(previous, current):
    declared = current.get("historical_coverage", {})
    slots = {slot["id"]: slot for slot in current["slots"]}
    rows = []
    issues = []
    targets = {}
    for slot, requirement in requirements(previous):
        key = slot + "/" + requirement["id"]
        entry = declared.get(key) if isinstance(declared, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        target = entry.get("slot_id")
        check = entry.get("check_id")
        path = entry.get("observation_path", requirement.get("observation_path"))
        target_slot = slots.get(target) if isinstance(target, str) else None
        checks = [] if target_slot is None else (
            target_slot.get("behavior_requirements", []) +
            [r for r in target_slot.get("validation_obligations", [])
             if r.get("required") and r.get("phase") != "research"])
        if (not any(r["id"] == check for r in checks)
                or (slot in slots and target != slot)
                or (target_slot is not None and targets.get(target, slot) != slot)
                or any(existing_target != target for existing_target, old_slot in targets.items()
                       if old_slot == slot)
                or not isinstance(path, list) or not path
                or path[0] in ("trajectory", "observation")
                or any(type(part) not in (str, int) for part in path)
                or requirement.get("predicate") not in ("equals", "contains", "changes")):
            issues.append({"artifact": "blueprint", "code": "historical_coverage_mapping_invalid",
                           "requirement": key})
        else:
            targets[target] = slot
            rows.append((key, target, check, {**requirement, "observation_path": path}))
    return rows, issues


def evaluate(rows, tasks, units):
    from .generated_game import behavior_check

    receipts = []
    for unit in units:
        sessions = {s["session_id"]: s for s in unit.get("sessions", [])}
        for key, slot, check, requirement in rows:
            matches = [t for t in tasks if t["unit_id"] == unit["unit_id"] and t["slot_id"] == slot]
            task = matches[0] if len(matches) == 1 else {}
            replay = task.get("replay", {})
            session = sessions.get(replay.get("session_id"), {})
            trajectory = session.get("trajectory", [])
            result = {"status": "unknown", "reason": "historical_replay_evidence_missing"}
            if (replay.get("accepted") is True and trajectory
                    and session.get("purpose") == "generated_level_replay"
                    and session.get("task_id") == task["task_id"]
                    and task.get("unit_artifact_sha256")
                    and session.get("unit_artifact_sha256") == task["unit_artifact_sha256"]
                    and replay.get("loaded_configuration_sha256")
                    and session.get("loaded_configuration_sha256") == replay["loaded_configuration_sha256"]
                    and (task.get("fulfillment", {}).get(check) or task.get("obligations", {}).get(check) or {}).get("status") == "pass"):
                result = behavior_check(requirement, trajectory, trajectory[0]["engine_position"])
            receipts.append({**result, "requirement": key, "slot_id": slot, "check_id": check,
                             "unit_id": unit["unit_id"], "session_id": replay.get("session_id"),
                             "loaded_configuration_sha256": replay.get("loaded_configuration_sha256")})
    return receipts
