"""Scripted offline controller exercise. No real model or game input."""
import hashlib,json,sys
from pathlib import Path
from .demo import prepare,fixture
from arcagi_pipeline.agent_workflow import Completion

PLUGIN = r'''from arcagi_pipeline.production import GeneratedTask,ModelSearch,EngineVerdict,FulfillmentVerdict,UnitVerdict
import json
class Plugin:
 plugin_id="controlled-neutral"
 def __init__(self,context):
  self.context=context;self.source=context.load_source("reference")
 def generate(self,inputs,seed):
  slots=json.loads(inputs.blueprint.read_text())["slots"]
  return [GeneratedTask(s["id"],s,{"slot_id":s["id"],"rule_ids":s["rule_ids"],"config":s["extension"],"rationale":s["purpose"],"claims":[{"id":"reached","statement":"reaches fixture target","check":"reference goal"}]}) for s in slots]
 def validate(self,task):return []
 def search(self,task,cap):
  if FAILURE:return ModelSearch("unknown",reason="synthetic bound")
  return ModelSearch("candidate",({"write":task.payload["extension"]["target"]},))
 def adapter(self,target):
  source=self.source
  class Adapter:
   def __init__(self):self.engine=source.ReferenceEngine(target);self.source_object=self.engine
   def reset(self):return {"items":[]}
   def step(self,action):self.engine.step(action);return {"items":list(self.engine.state["items"])}
  return Adapter()
 def replay(self,task,candidate):
  adapter=self.adapter(task.payload["extension"]["target"]);session=self.context.session(adapter,"reference")
  trace=[session.reset()]
  for action in candidate.actions:trace.append(session.step(action))
  return EngineVerdict(adapter.engine.won(),trace=tuple(trace),binding={"session_id":session.session_id})
 def assess(self,task,candidate,verdict):return FulfillmentVerdict({"reached":{"status":"pass","evidence":{"kind":"reference","data":verdict.trace}}})
 def validate_unit(self,tasks,records,context):return UnitVerdict("pass",{"coverage":{"status":"pass","evidence":{"kind":"count","data":len(tasks)}}})
 def adapter_smoke(self,context):
  session=context.session(self.adapter({"label":"smoke"}),"reference");states=[session.reset(),session.step({"write":{"label":"smoke"}})]
  return UnitVerdict("pass",{"reset_step":{"status":"pass","evidence":{"kind":"actual_interface_calls","data":{"session_id":session.session_id,"states":states}}}})
def create_plugin(context):return Plugin(context)
'''

def implementation(failure=False):return {"entrypoint":"plugin.py","factory":"create_plugin","files":[{"path":"plugin.py","content":"FAILURE="+repr(failure)+"\n"+PLUGIN}]}

def prepare_control(root,count=2,max_revisions=3):
 config,_=prepare(root,fixture(count))
 runtime_path=root/"runtime.json";runtime=json.loads(runtime_path.read_text());source=Path(__file__).with_name("engine.py").resolve()
 runtime["sources"]=[{"id":"reference","path":str(source),"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}];runtime_path.write_text(json.dumps(runtime))
 ledger=root/"ledger.json";ledger.write_text(json.dumps({"ceilings":{"requests":100,"total_tokens":10000000,"input_tokens_per_request":1000000,"output_tokens_per_request":1000000,"elapsed_seconds":86400,"rework_reserve_tokens":0},"requests_used":0,"total_tokens_used":0}))
 spec=root/"workflow.json";data=json.loads(spec.read_text());data["budget"].update(aggregate_ledger=str(ledger),max_attempts=10,max_tokens=1000000)
 spec.write_text(json.dumps(data))
 control=json.loads(config.read_text());control.update(schema_version="arcagi-control/v1",max_revisions=max_revisions,max_elapsed_seconds=3600,unknown_policy="revise",require_unit_checks=True)
 path=root/"control.json";path.write_text(json.dumps(control));return path

class ScriptedControlProvider:
 provider_id="scripted-no-api"
 def __init__(self,count=2,failures=1,invalid_diagnosis=False):self.count=count;self.failures=failures;self.implementations=0;self.calls=0;self.invalid_diagnosis=invalid_diagnosis
 def complete(self,messages,*,model,max_tokens):
  self.calls+=1;request=json.loads(messages[-1]["content"]);tid=request["task"]["id"]
  if tid in {"evidence","rules","blueprint"}:value=fixture(self.count)[["evidence","rules","blueprint"].index(tid)]
  else:
   if tid=='diagnosis-correction':value={}
   else:value=implementation(self.implementations<self.failures);self.implementations+=1
   feedback=next((json.loads(i["content"]) for i in request["input_artifacts"] if i["id"]=="control_feedback"),None)
   if feedback:
    check=feedback["checks"][0]
    value["diagnosis"]={"stage":check["stage"],"root_cause_status":"unconfirmed","candidate_causes":["synthetic test condition"],"completed_checks":[{"check_id":check["check_id"],"evidence_ref":check["evidence_ref"],"observed_status":check["status"]}],"excluded_causes":[],"missing_evidence":[],"next_check":{"operation":"rerun_declared_checks"},"fact_refs":feedback["evidence_refs"]}
    if self.invalid_diagnosis:value["diagnosis"]["completed_checks"]=[]
  return Completion(json.dumps(value),total_tokens=7)
