"""Offline interface exercise with scripted model responses; no intelligence claim."""
import argparse,json
from pathlib import Path
from arcagi_pipeline.agent_workflow import Completion
from arcagi_pipeline.machine import run_machine

class ScriptedProvider:
    provider_id="scripted-fixture/v1"
    def __init__(self, artifacts): self.artifacts=iter(artifacts)
    def complete(self,messages,*,model,max_tokens):
        return Completion(json.dumps(next(self.artifacts)),total_tokens=100)

def fixture(count=3,mode=None):
    evidence={"evidence":[{"id":"observation","kind":"synthetic_observation","source_ref":"synthetic:fixture","content":"fixture data; not a real game"}]}
    rules={"rules":[{"id":r,"statement":"fixture operation "+r,"status":"supported","evidence_refs":["observation"]} for r in ["record","inspect"]]}
    slots=[]
    for i in range(count):
        slots.append({"id":"slot-"+str(i),"purpose":"fixture contrast" if i%2==0 else "fixture combination", "rationale":"synthetic nonmonotonic interface exercise", "prerequisites":[] if i==0 else ["slot-"+str(i-1)],"rule_ids":["record"] if i%2==0 else ["record","inspect"],"evidence_refs":["observation"],"extension":{"target":{"label":str(i)},"mode":mode if i==count-1 else None}})
    here=Path(__file__).parent
    implementation={"entrypoint":"plugin.py","factory":"create_plugin","files":[{"path":name,"content":(here/name).read_text()} for name in ["plugin.py","engine.py"]]}
    return [evidence,rules,{"slots":slots},implementation]

def prepare(root,artifacts):
    root.mkdir(parents=True)
    (root/"prompt.md").write_text("Synthetic offline task. Return the supplied output schema. No private reasoning.")
    names=["evidence","rules","blueprint","implementation"]
    tasks=[]
    for i,name in enumerate(names):
        tasks.append({"id":name,"stage":name,"prompt":"prompt.md","depends_on":names[:i],"inputs":[{"id":n,"role":n,"from_task":n} for n in names[:i]],"max_output_tokens":12000,"output_schema":{"type":"object"}})
    from arcagi_pipeline.execution_contract import CONTRACT, FILES
    import arcagi_pipeline.production as api
    import hashlib
    tasks[-1]["execution_contract"]=CONTRACT
    for role,name in FILES.items():
        path=Path(api.__file__).with_name(name)
        tasks[-1]["inputs"].append({"id":role,"role":role,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    spec={"schema_version":"arcagi-agent-workflow/v1","workflow_id":"neutral-demo","unit_id":"neutral-unit","game":{"id":"synthetic-records","version":"fixture"},"provider":{"id":"deepseek-chat-completions/v1","model":"scripted-no-api"},"budget":{"max_attempts":4,"max_tokens":200000},"tasks":tasks}
    (root/"workflow.json").write_text(json.dumps(spec))
    import sys
    runtime={"schema_version":"arcagi-runtime/v1","python":str(Path(sys.executable).resolve()),"python_sha256":hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),"import_roots":[],"dependencies":[],"sources":[],"timeout_seconds":30}
    (root/"runtime.json").write_text(json.dumps(runtime))
    config={"production_contract":"design-production/v1","runtime_config":"runtime.json","workflow_spec":"workflow.json","roles":dict(zip(names,names)),"seed":0,"cap":50,"timeout_seconds":30}
    path=root/"machine.json";path.write_text(json.dumps(config))
    return path,ScriptedProvider(artifacts)

def demo(output,count=3,mode=None):
    config,provider=prepare(output/"spec",fixture(count,mode))
    return run_machine(config,output/"workflow",output/"execution",provider=provider)
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args()
    print(json.dumps(demo(a.output_dir)))
