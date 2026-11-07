import json
from arcagi_pipeline.production import GeneratedTask, ModelSearch, EngineVerdict, FulfillmentVerdict
from .engine import ReferenceEngine
class Plugin:
    plugin_id = "synthetic-records"
    def generate(self, inputs, seed):
        bp = json.loads(inputs.blueprint.read_text())
        return [GeneratedTask(s["id"], s, {"slot_id":s["id"], "rule_ids":s["rule_ids"],
            "config":s["extension"], "rationale":s["purpose"],
            "claims":[{"id":"record-match", "statement":"constructed task reaches declared target", "check":"compare reference environment result with target"}]}) for s in bp["slots"]]
    def validate(self, task): return []
    def search(self, task, cap):
        mode=task.payload["extension"].get("mode")
        if mode == "unknown": return ModelSearch("unknown",reason="synthetic_limit")
        return ModelSearch("candidate",({"write":task.payload["extension"]["target"]},))
    def replay(self, task, candidate):
        env=ReferenceEngine(task.payload["extension"]["target"])
        trace=[]
        for i,a in enumerate(candidate.actions):
            if not env.step(a): return EngineVerdict(False,"illegal",tuple(trace),i)
            trace.append({"action":a,"state":{"items":list(env.state["items"])}})
        accepted=env.won() and task.payload["extension"].get("mode") != "engine_reject"
        return EngineVerdict(accepted,"reference_goal" if accepted else "rejected",tuple(trace),binding={"environment":"synthetic-reference-v1"})
    def assess(self, task, candidate, verdict):
        if task.payload["extension"].get("mode") == "missing_check": return FulfillmentVerdict({})
        if task.payload["extension"].get("mode") == "bad_evidence": return FulfillmentVerdict({"record-match":{"status":"pass","evidence":"trust me"}})
        return FulfillmentVerdict({"record-match":{"status":"pass","evidence":{"kind":"reference_replay_comparison","data":{"trace":list(verdict.trace),"target":task.payload["extension"]["target"]}}}})
def create_plugin(): return Plugin()
