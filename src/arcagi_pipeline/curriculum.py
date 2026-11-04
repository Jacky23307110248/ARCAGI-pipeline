from __future__ import annotations

from .contracts import CourseSlot


def validate_course(slots: list[CourseSlot]) -> list[str]:
    """Check ordered prerequisites; learning benefit remains an empirical hypothesis."""
    errors: list[str] = []
    ids = [slot.slot_id for slot in slots]
    if len(ids) != len(set(ids)):
        errors.append("slot IDs must be unique")
    seen: set[str] = set()
    for slot in slots:
        missing = set(slot.prerequisites) - seen
        if missing:
            errors.append(f"{slot.slot_id} has unmet or forward prerequisites: {sorted(missing)}")
        if not slot.objectives:
            errors.append(f"{slot.slot_id} has no objective")
        seen.add(slot.slot_id)
    return errors