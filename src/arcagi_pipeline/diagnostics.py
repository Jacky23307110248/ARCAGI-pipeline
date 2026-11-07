"""Append-only diagnostic facts, separate attribution and explicit continuation links.

Only structured codes select routes. Exceptions and plugin hints do not prove
whether a game artifact or the machine is responsible. No automatic repair.
"""
from __future__ import annotations
import contextvars
import functools
import hashlib
import inspect
import json
import os
import re
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

INSTRUCTIONS = {"artifact_revision":"Inspect recorded checks and responsible artifacts; preserve unrelated evidence and unknowns.","machine_diagnosis":"Diagnose runtime or protocol prerequisites; no game or core edit is authorized by this packet.","diagnosis_required":"Establish attribution from facts first; do not assume a game-artifact or machine repair."}
ROUTES = {"artifact":"artifact_revision", "runtime":"machine_diagnosis", "undetermined":"diagnosis_required"}
CODES = {
 "structure_invalid":("artifact","Declared structural check rejected the artifact"),
 "output_protocol_invalid":("artifact","Output violates the declared protocol"),
 "model_exhausted":("artifact","Declared finite model was exhausted"),
 "design_invalid":("artifact","Design references or required fields are invalid"),
 "claim_invalid":("artifact","Claim evidence does not satisfy the declared protocol"),
 "process_start_failed":("runtime","Declared execution process could not be started"),
 "model_review_return":("artifact","Model self-review requests an artifact revision"),
 "model_revision_requested":("artifact","Model requests a different stage revision"),
 "probe_limited":("artifact","Validation probe reached its declared resource bound"),
 "probe_execution_failed":("artifact","Validation probe requires artifact diagnosis"),
 "config_invalid":("runtime","Machine configuration does not satisfy its protocol"),
 "artifact_changed":("runtime","Artifact bytes differ from the recorded hash"),
 "input_drift":("runtime","Frozen inputs changed or became unavailable"),
 "provider_transport":("runtime","Provider transport failed"),
 "provider_http":("runtime","Provider returned an HTTP error"),
 "budget_limit":("runtime","Configured budget prevented execution"),
 "declared_dependency_missing":("runtime","A predeclared dependency check failed"),
 "engine_mismatch":("undetermined","Environment replay rejected or disagreed with the candidate"),
 "module_load_failed":("undetermined","Generated module could not be loaded"),
 "worker_timeout":("undetermined","Worker exceeded the configured time limit"),
 "workflow_not_ready":("runtime","Required workflow outputs are not complete"),
 "output_exists":("runtime","Output directory already exists; use a new attempt directory"),
 "worker_failed":("undetermined","Worker exited without a valid successful result"),
 "search_limited":("undetermined","Search returned a bounded or incomplete result"),
 "claim_inconclusive":("undetermined","Claim check returned unknown or rejected its claim"),
 "exception":("undetermined","Execution raised an exception; attribution needs evidence"),
}
ACTIVE = contextvars.ContextVar("arcagi_diagnostic_context", default=None)
SECRET_KEY = re.compile(r"(?i)(api.?key|password|secret|authorization|access.?token|env.file|provider.response|stdout|stderr)")
TOKEN = re.compile(r"(?i)(sk-[a-z0-9_-]{8,}|Bearer\s+[^\s\"']+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+)")
IDENTIFIER = re.compile(r"^[a-f0-9]{32}$")
def uid(): return uuid.uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def scrub(value):
    if isinstance(value, dict): return {str(k):"<redacted>" if SECRET_KEY.search(str(k)) else scrub(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [scrub(v) for v in value]
    if isinstance(value,str): return TOKEN.sub("<redacted>",value)
    if value is None or isinstance(value,(bool,int,float)): return value
    return {"type":type(value).__name__}
def exception_facts(exc):
    # Raw exception messages, local variables and source lines are never archived.
    locations=[]
    for frame in traceback.extract_tb(exc.__traceback__):
        try: digest=file_ref(frame.filename)["sha256"]
        except OSError: digest=None
        locations.append({"file":Path(frame.filename).name,"line":frame.lineno,"function":frame.name,"source_sha256":digest})
    return {"type":type(exc).__name__,"locations":locations}
def file_ref(path):
    path=Path(path).resolve()
    return {"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
class ArchiveWriteError(OSError): pass
class Fault(ValueError):
    def __init__(self, code, message=None):
        self.diagnostic_code=code
        super().__init__(message or CODES.get(code,CODES["exception"])[1])

def exclusive_json(path, value):
    """Publish fully written JSON with exclusive hard-link creation (no overwrite)."""
    temporary=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=".pending-",dir=path.parent)
        temporary=Path(name)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(scrub(value),stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.write("\n");stream.flush();os.fsync(stream.fileno())
        if os.name == "nt":
            # Windows rename is atomic and rejects an existing destination.
            os.rename(temporary,path)
        else:
            os.link(temporary,path)
    except Exception as exc:
        raise ArchiveWriteError("diagnostic archive write failed ("+type(exc).__name__+")") from None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)

def checked_id(value):
    if not isinstance(value,str) or not IDENTIFIER.fullmatch(value): raise ValueError("invalid diagnostic ID")
    return value
class Archive:
    def __init__(self, root): self.root=Path(root).resolve()
    def read(self, collection, identifier):
        return json.loads((self.root/collection/(checked_id(identifier)+".json")).read_text(encoding="utf-8"))
    def put(self, collection, value):
        identifier=uid();exclusive_json(self.root/collection/(identifier+".json"),dict(value,id=identifier));return identifier
    def start(self, component, *, previous_run=None, pending_events=()):
        if previous_run: self.read("runs",previous_run)
        for event in pending_events: self.read("events",event)
        run_id=self.put("runs",{"schema_version":"arcagi-diagnostic-run/v1","created_at":now(),"component":component,"previous_run":previous_run,"pending_events":list(pending_events)})
        return Context(self,run_id)
    def attribute(self,event_id,category,*,basis,source="operator",confidence="provisional",supersedes=None):
        event=self.read("events",event_id)
        if category not in ROUTES or not basis or confidence not in {"provisional","confirmed"}: raise ValueError("invalid attribution")
        if not isinstance(basis,list) or not all(isinstance(x,dict) and x for x in basis): raise ValueError("basis must be structured evidence")
        # Plugin statements may be archived as claims but never confirm machine defects.
        if source not in {"operator","independent_review","core_code"}: raise ValueError("untrusted attribution source")
        snapshot=self.view(event_id)
        latest=snapshot["attributions"][-1]["id"] if snapshot["attributions"] else None
        if supersedes != latest: raise ValueError("attribution lease does not match current history")
        attribution_id=self.put("attributions",{"created_at":now(),"event_id":event_id,"category":category,"route":ROUTES[category],"basis":basis,"source":source,"confidence":confidence,"supersedes":supersedes,"fact_sha256":file_ref(self.root/"events"/(event_id+".json"))["sha256"]})
        self.put("packets",{"schema_version":"arcagi-route-packet/v1","event_id":event_id,"attribution_id":attribution_id,"route":ROUTES[category],"fact":event,"instruction":INSTRUCTIONS[ROUTES[category]],"basis":basis})
        return attribution_id
    def resolve(self,event_id,*,run_id,check_refs):
        event=self.read("events",event_id);run=self.read("runs",run_id)
        if event_id not in run["pending_events"] or run_id==event["run_id"]: raise ValueError("resolution requires an explicitly linked successor run")
        if not check_refs: raise ValueError("resolution requires subsequent check files")
        refs=[file_ref(p) for p in check_refs]
        for ref in refs:
            check=json.loads(Path(ref["path"]).read_text(encoding="utf-8"))
            if check.get("event_id")!=event_id or check.get("run_id")!=run_id or check.get("status")!="pass" or not check.get("check") or not check.get("evidence_refs"):
                raise ValueError("resolution requires an event-bound passing check with evidence references")
            for evidence in check["evidence_refs"]:
                if file_ref(evidence["path"])["sha256"]!=evidence["sha256"]: raise ValueError("resolution evidence changed")
        return self.put("resolutions",{"created_at":now(),"event_id":event_id,"run_id":run_id,"check_refs":refs})
    def attach(self,event_id,refs,*,selector=None):
        self.read("events",event_id)
        for ref in refs:
            if file_ref(ref["path"])["sha256"]!=ref["sha256"]: raise ValueError("attachment hash mismatch")
        return self.put("attachments",{"created_at":now(),"event_id":event_id,"refs":refs,"selector":selector})
    def view(self,event_id=None):
        def load(collection):
            directory=self.root/collection
            return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]
        events=[self.read("events",event_id)] if event_id else load("events")
        attrs=load("attributions");resolutions=load("resolutions");runs=load("runs");outcomes=load("outcomes");attachments=load("attachments");route_packets=load("packets")
        result=[]
        for event in events:
            history=[a for a in attrs if a["event_id"]==event["id"]]
            digest=file_ref(self.root/"events"/(event["id"]+".json"))["sha256"]
            if any(a.get("fact_sha256")!=digest for a in history): raise ValueError("fact integrity mismatch")
            # Follow supersedes links, not lexicographic UUID or filesystem order.
            ordered=[];parent=None
            while True:
                children=[a for a in history if a["supersedes"]==parent]
                if not children: break
                if len(children)!=1: raise ValueError("attribution history fork; explicit review required")
                ordered.append(children[0]);parent=children[0]["id"]
            links=[x for x in runs if event["id"] in x["pending_events"]]
            resolved=[]
            for item in [r for r in resolutions if r["event_id"]==event["id"]]:
                valid=True
                for ref in item["check_refs"]:
                    try:
                        valid=valid and file_ref(ref["path"])["sha256"]==ref["sha256"]
                        check=json.loads(Path(ref["path"]).read_text(encoding="utf-8"))
                        for evidence in check["evidence_refs"]:
                            valid=valid and file_ref(evidence["path"])["sha256"]==evidence["sha256"]
                    except (OSError,ValueError,KeyError): valid=False
                resolved.append(dict(item,evidence_intact=valid))
            attached=[]
            for attachment in [a for a in attachments if a["event_id"]==event["id"]]:
                intact=True
                for ref in attachment["refs"]:
                    try: intact=intact and file_ref(ref["path"])["sha256"]==ref["sha256"]
                    except OSError: intact=False
                attached.append(dict(attachment,evidence_intact=intact))
            event_packets=[p for p in route_packets if p["event_id"]==event["id"]]
            current_packets=[p for p in event_packets if ordered and p["attribution_id"]==ordered[-1]["id"]]
            result.append({"fact":event,"route_packets":event_packets,"current_packet":current_packets[-1] if current_packets else None,"attachments":attached,"attributions":ordered,"current_attribution":ordered[-1] if ordered else None,"successor_runs":links,"outcomes":[o for o in outcomes if any(o["run_id"]==l["id"] for l in links)],"resolutions":resolved,"resolved":any(x["evidence_intact"] for x in resolved)})
        return result[0] if event_id else result

class Context:
    def __init__(self,archive,run_id,attempt_id=None): self.archive,self.run_id,self.attempt_id=archive,run_id,attempt_id
    def branch(self,component,task_id=None):
        identifier=self.archive.put("attempts",{"created_at":now(),"run_id":self.run_id,"parent_attempt":self.attempt_id,"component":component,"task_id":task_id})
        return Context(self.archive,self.run_id,identifier)
    def outcome(self,status,event_ref=None):
        self.archive.put("outcomes",{"created_at":now(),"run_id":self.run_id,"attempt_id":self.attempt_id,"status":status,"event_ref":event_ref})
    def record(self,code,stage,*,task_id=None,kind="error",expected=None,observed=None,refs=(),exc=None,claims=None):
        category,summary=CODES.get(code,CODES["exception"])
        event_id=self.archive.put("events",{"schema_version":"arcagi-error-fact/v1","created_at":now(),"run_id":self.run_id,"attempt_id":self.attempt_id,"stage":stage,"task_id":task_id,"code":code if code in CODES else "exception","kind":kind,"summary":summary,"expected":expected,"observed":observed,"artifact_refs":list(refs),"exception":exception_facts(exc) if exc else None,"plugin_claims":claims})
        attribution_id=self.archive.attribute(event_id,category,basis=[{"structured_code":code}],source="core_code")
        ref={"event_id":event_id,"run_id":self.run_id,"attempt_id":self.attempt_id,"category":category,"route":ROUTES[category],"attribution_id":attribution_id,"archive":str(self.archive.root)}
        packet_id=self.archive.view(event_id)["current_packet"]["id"]
        return dict(ref,packet_id=packet_id)
    def serial(self): return {"archive":str(self.archive.root),"run_id":self.run_id,"attempt_id":self.attempt_id}
    @classmethod
    def restore(cls,data):
        archive=Archive(data["archive"]);archive.read("runs",data["run_id"])
        if data.get("attempt_id"): archive.read("attempts",data["attempt_id"])
        return cls(archive,data["run_id"],data.get("attempt_id"))

def current():
    context=ACTIVE.get()
    if context is None: raise RuntimeError("diagnostic context not initialized")
    return context

def traced(component,default_code="exception"):
    """Public optional kwargs: log_dir, previous_run, pending_events; no budget changes."""
    def decorate(function):
        signature=inspect.signature(function)
        @functools.wraps(function)
        def wrapped(*args,**kwargs):
            log_dir=kwargs.pop("log_dir",None);previous=kwargs.pop("previous_run",None);pending=kwargs.pop("pending_events",())
            explicit=kwargs.pop("_diagnostics",None)
            bound=signature.bind_partial(*args,**kwargs).arguments
            owner=bound.get("self")
            parent=explicit or ACTIVE.get() or getattr(owner,"_diagnostic_context",None)
            if log_dir is not None or previous or pending or parent is None:
                path=next((bound[k] for k in ("output_dir","output","run_dir","spec_path","config_path","workflow_run") if bound.get(k) is not None),Path.cwd()/"run")
                root=Path(log_dir) if log_dir is not None else Path(path).resolve().parent/"error-archive"
                parent=Archive(root).start(component,previous_run=previous,pending_events=pending)
            task=bound.get("task");task_id=task.get("id") if isinstance(task,dict) else None
            context=parent.branch(component,task_id);token=ACTIVE.set(context)
            if owner is not None and function.__name__=="__init__": owner._diagnostic_context=context
            try:
                result=function(*args,**kwargs)
                context.outcome(result.get("status","complete") if isinstance(result,dict) else ("pass" if result==0 else "return") if isinstance(result,int) else "complete")
                return result
            except ArchiveWriteError: raise
            except Exception as exc:
                ref=getattr(exc,"diagnostic_ref",None)
                if ref is None:
                    code=getattr(exc,"diagnostic_code",None) or default_code
                    ref=context.record(code,component,task_id=task_id,exc=exc)
                    exc.diagnostic_ref=ref
                context.outcome("error",ref)
                raise
            finally: ACTIVE.reset(token)
        return wrapped
    return decorate

def packets(context,feedback):
    grouped={route:[] for route in ROUTES.values()}
    for item in feedback:
        if item.get("route") in grouped: grouped[item["route"]].append(item)
    return {route:{"schema_version":"arcagi-route-packet/v1","route":route,"events":items,"diagnostics":context.serial(),"instruction":INSTRUCTIONS[route]} for route,items in grouped.items()}
