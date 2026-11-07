import json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
from arcagi_pipeline.budget import AggregateBudget,atomic_json
from arcagi_pipeline.diagnostics import Fault
from arcagi_pipeline.agent_workflow import WorkflowRunner,Completion,WorkflowError
from examples.neutral_machine.demo import prepare,fixture,ScriptedProvider

class BudgetTests(unittest.TestCase):
    def ledger(self,root):
        path=root/"ledger.json"
        atomic_json(path,{"ceilings":{"requests":100,"total_tokens":1000000,"input_tokens_per_request":500000,"output_tokens_per_request":100000,"elapsed_seconds":86400,"rework_reserve_tokens":100000},"requests_used":45,"total_tokens_used":194064,"unreported_usage_tokens_reserved":60000,"attempts":[{"legacy":True}]})
        return AggregateBudget(path)
    def reserve(self,b,rid="a",**kwargs):
        return b.reserve(rid,reservation=100,input_ceiling=60,output_cap=40,metadata={"workflow_state":"fixture"},**kwargs)
    def test_legacy_preserved_and_settlement_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            b=self.ledger(Path(td));self.reserve(b);self.reserve(b)
            b.settle("a",reported_total=12,prompt_tokens=7,completion_tokens=5)
            b.settle("a",reported_total=12,prompt_tokens=7,completion_tokens=5)
            d=b.snapshot();self.assertEqual(d["requests_used"],46);self.assertEqual(d["total_tokens_used"],194076);self.assertEqual(d["tokens_inflight_reserved"],0);self.assertEqual(d["attempts"],[{"legacy":True}])
    def test_unknown_usage_charges_reservation_once(self):
        with tempfile.TemporaryDirectory() as td:
            b=self.ledger(Path(td));self.reserve(b);b.settle("a",status="interrupted");b.settle("a",status="interrupted")
            d=b.snapshot();self.assertEqual(d["total_tokens_used"],194164);self.assertEqual(d["unreported_usage_tokens_reserved"],60100)
    def test_pending_reservations_enforce_total_ceiling(self):
        with tempfile.TemporaryDirectory() as td:
            b=self.ledger(Path(td));d=b.snapshot();d["ceilings"]["total_tokens"]=194264;d["ceilings"]["rework_reserve_tokens"]=0;atomic_json(b.path,d)
            self.reserve(b,"a");self.reserve(b,"b")
            with self.assertRaises(Fault):self.reserve(b,"c")
            self.assertEqual(b.snapshot()["requests_used"],47)
    def test_rework_reserve_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as td:
            b=self.ledger(Path(td));d=b.snapshot();d["ceilings"]["total_tokens"]=194264;d["ceilings"]["rework_reserve_tokens"]=150;atomic_json(b.path,d)
            with self.assertRaises(Fault):self.reserve(b)
            self.reserve(b,rework=True)
    def test_request_and_time_limits(self):
        for field,value in [("requests",45),("input_tokens_per_request",50),("output_tokens_per_request",30),("elapsed_seconds",1)]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as td:
                b=self.ledger(Path(td));d=b.snapshot();d["ceilings"][field]=value
                if field=="elapsed_seconds":d["started_at_utc"]="2000-01-01T00:00:00+00:00"
                atomic_json(b.path,d)
                with self.assertRaises(Fault):self.reserve(b)
    def test_concurrent_requests_cannot_overreserve(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as td:
            b=self.ledger(Path(td));d=b.snapshot();d["ceilings"].update(total_tokens=194364,rework_reserve_tokens=0);atomic_json(b.path,d)
            def request(i):
                try:self.reserve(b,str(i));return True
                except Fault:return False
            with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(request,range(8)))
            self.assertEqual(sum(results),3)
            self.assertEqual(b.snapshot()["requests_used"],48)
    def config(self,root):
        b=self.ledger(root);config,provider=prepare(root/"spec",fixture(1));spec_path=config.parent/"workflow.json";s=json.loads(spec_path.read_text());s["budget"].update(max_attempts=10,max_tokens=2000000,max_input_tokens_per_request=500000,max_output_tokens_per_request=100000,aggregate_ledger=str(b.path));spec_path.write_text(json.dumps(s));return b,spec_path,provider
    def test_several_workflows_share_single_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,spec,provider=self.config(root)
            WorkflowRunner(spec,root/"a",provider=provider).run()
            WorkflowRunner(spec,root/"b",provider=ScriptedProvider(fixture(1))).run()
            d=b.snapshot();self.assertEqual(d["requests_used"],53);self.assertEqual(d["total_tokens_used"],194864)
            self.assertEqual(len(d["runtime_requests"]),8)
    def test_interrupted_local_state_reconciles_settled_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,spec,provider=self.config(root)
            runner=WorkflowRunner(spec,root/"run",provider=provider)
            original=runner._finalize_staged_output
            def crash(*args,**kwargs):
                attempt=args[1];runner._settle_aggregate(attempt,reported=attempt["reported_tokens"]);raise KeyboardInterrupt()
            with patch.object(runner,"_finalize_staged_output",side_effect=crash):
                with self.assertRaises(KeyboardInterrupt):runner.run()
            d=b.snapshot();self.assertEqual(d["total_tokens_used"],194164)
            recovered=WorkflowRunner(spec,root/"run",provider=ScriptedProvider(fixture(1)[1:]))
            recovered.run();self.assertEqual(b.snapshot()["total_tokens_used"],194464)
            self.assertEqual(recovered.data["budget"]["tokens_used"],400)
    def test_reservation_before_local_save_is_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,spec,provider=self.config(root)
            runner=WorkflowRunner(spec,root/"run",provider=provider)
            data=runner.data;task=data["tasks"][0]
            b.reserve("orphan",reservation=100,input_ceiling=60,output_cap=40,metadata={"workflow_state":str(runner.state_path),"workflow_id":data["workflow_id"],"task_id":task["id"],"attempt":1,"fingerprint":"frozen"})
            runner._recover_aggregate_orphans();first=b.snapshot()["total_tokens_used"]
            runner._recover_aggregate_orphans();self.assertEqual(first,b.snapshot()["total_tokens_used"])
            self.assertEqual(runner.data["budget"]["attempts_used"],1);self.assertEqual(runner.data["budget"]["tokens_used"],100)
    def test_truncated_reply_charged_and_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,spec,_=self.config(root)
            class Truncated:
                provider_id="fixture"
                def complete(self,*args,**kwargs):return Completion('{"looks":"valid"}',total_tokens=7,finish_reason="length",response_model="fixture-revision")
            runner=WorkflowRunner(spec,root/"run",provider=Truncated())
            with self.assertRaises(WorkflowError):runner.run()
            self.assertEqual(b.snapshot()["total_tokens_used"],194071)
            self.assertEqual(runner.data["attempt_log"][0]["finish_reason"],"length")
            self.assertEqual(runner.data["attempt_log"][0]["response_model"],"fixture-revision")
    def test_model_timeout_and_large_limits_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,spec,_=self.config(root);s=json.loads(spec.read_text());s["budget"].update(max_attempts=20000,max_tokens=200000000,max_input_tokens_per_request=600000,max_output_tokens_per_request=393216,max_elapsed_seconds=2592000);s["provider"]["timeout_seconds"]=600;spec.write_text(json.dumps(s))
            self.assertTrue(WorkflowRunner(spec,plan_only=True).plan()["valid"])
            s["provider"]["timeout_seconds"]=False;spec.write_text(json.dumps(s))
            with self.assertRaises(WorkflowError):WorkflowRunner(spec,plan_only=True)
if __name__=="__main__":unittest.main()
