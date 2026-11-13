from arcagi_pipeline.historical_coverage import mappings


def test_historical_mapping_must_address_an_observation():
    old = {"slots": [{"id": "old", "behavior_requirements": [{
        "id": "behavior", "kind": "behavior", "observation_path": ["value"],
        "predicate": {"type": "equals", "value": 7},
    }]}]}
    mapping = {"slot_id": "new", "check_id": "check"}
    new = {"slots": [{"id": "new", "behavior_requirements": [{"id": "check"}]}],
           "historical_coverage": {"old/behavior": mapping}}
    rows, issues = mappings(old, new)
    assert not issues
    assert rows[0][-1]["value"] == 7
    mapping["observation_path"] = ["trajectory", 0, "observation", "value"]
    rows, issues = mappings(old, new)
    assert not rows
    assert issues[0]["requirement"] == "old/behavior"
