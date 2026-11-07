"""Locked aggregate request reservation and idempotent settlement across workflows.

Existing counters/attempts are retained. New requests live under runtime_requests
in the same ledger; no provider body or credentials enter the ledger.
"""
from __future__ import annotations
import json,os,time,tempfile
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from .diagnostics import Fault

def utc():return datetime.now(timezone.utc).isoformat()
@contextmanager
def file_lock(path,timeout=30):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open("a+b")
    try:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b"0");stream.flush()
        started=time.monotonic()
        while True:
            stream.seek(0)
            try:
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()-started>=timeout:raise Fault("budget_limit","budget/workflow lock unavailable")
                time.sleep(.05)
        try:yield
        finally:
            stream.seek(0)
            if os.name=="nt":msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    finally:stream.close()

def atomic_json(path,data):
    fd,name=tempfile.mkstemp(prefix=".ledger-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(data,stream,ensure_ascii=True,indent=2,allow_nan=False)
            stream.write("\n");stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

class AggregateBudget:
    def __init__(self,path):self.path=Path(path).resolve()
    @contextmanager
    def transaction(self):
        with file_lock(self.path.with_suffix(self.path.suffix+".lock")):
            data=json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data.get("ceilings"),dict):raise Fault("config_invalid","aggregate ledger requires ceilings")
            data.setdefault("runtime_requests",{})
            yield data
            atomic_json(self.path,data)
    def snapshot(self):
        # Hold the lock even for a read, so another process cannot race a replacement.
        with file_lock(self.path.with_suffix(self.path.suffix+".lock")):
            return json.loads(self.path.read_text(encoding="utf-8-sig"))
    def reserve(self,request_id,*,reservation,input_ceiling,output_cap,metadata,rework=False):
        with self.transaction() as d:
            requests=d["runtime_requests"]
            if request_id in requests:
                prior=requests[request_id]
                if prior["reserved_tokens"]!=reservation or prior["metadata"]!=metadata:raise Fault("config_invalid","request ID reused with different inputs")
                return prior
            c=d["ceilings"]
            if any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in (reservation,input_ceiling,output_cap)):raise Fault("config_invalid","invalid reservation")
            if input_ceiling>c["input_tokens_per_request"] or output_cap>c["output_tokens_per_request"]:raise Fault("budget_limit","aggregate per-request limit")
            inflight=sum(r["reserved_tokens"] for r in requests.values() if r["status"]=="reserved")
            token_limit=c["total_tokens"] if rework else c["total_tokens"]-c.get("rework_reserve_tokens",0)
            if d.get("requests_used",0)+1>c["requests"] or d.get("total_tokens_used",0)+inflight+reservation>token_limit:raise Fault("budget_limit","aggregate budget exhausted")
            started=d.get("started_at_utc")
            if started and (datetime.now(timezone.utc)-datetime.fromisoformat(started.replace("Z","+00:00"))).total_seconds()>c["elapsed_seconds"]:raise Fault("budget_limit","aggregate elapsed limit")
            if not started:d["started_at_utc"]=utc()
            entry={"request_id":request_id,"status":"reserved","reserved_tokens":reservation,"input_ceiling":input_ceiling,"output_cap":output_cap,"metadata":metadata,"reserved_at":utc()}
            requests[request_id]=entry;d["requests_used"]=d.get("requests_used",0)+1
            d["tokens_inflight_reserved"]=inflight+reservation;d["updated_at_utc"]=utc()
            return entry
    def settle(self,request_id,*,reported_total=None,prompt_tokens=None,completion_tokens=None,status="complete"):
        with self.transaction() as d:
            entry=d["runtime_requests"][request_id]
            if entry["status"]!="reserved":return entry
            if reported_total is not None and (isinstance(reported_total,bool) or not isinstance(reported_total,int) or reported_total<0):raise Fault("config_invalid","invalid provider usage")
            charge=entry["reserved_tokens"] if reported_total is None else reported_total
            entry.update(status="settled",outcome=status,charged_tokens=charge,reported_total_tokens=reported_total,prompt_tokens=prompt_tokens,completion_tokens=completion_tokens,settled_at=utc())
            d["total_tokens_used"]=d.get("total_tokens_used",0)+charge
            if reported_total is None:d["unreported_usage_tokens_reserved"]=d.get("unreported_usage_tokens_reserved",0)+charge
            if prompt_tokens is not None:d["input_tokens_used"]=d.get("input_tokens_used",0)+prompt_tokens
            if completion_tokens is not None:d["output_tokens_used"]=d.get("output_tokens_used",0)+completion_tokens
            d["tokens_inflight_reserved"]=sum(r["reserved_tokens"] for r in d["runtime_requests"].values() if r["status"]=="reserved")
            d["updated_at_utc"]=utc()
            if charge>entry["reserved_tokens"]:d["status"]="provider_budget_violation"
            return entry
