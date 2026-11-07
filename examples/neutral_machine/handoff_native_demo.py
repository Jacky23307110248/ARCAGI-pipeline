"""Three-target original ARCEngine regression for v2 handoff and evidence scope."""
import copy,json
from dataclasses import replace
from pathlib import Path
from . import native_validation_demo as native
from . import validation_demo
from .generated import design
from arcagi_pipeline.generated_game import GeneratedLevel,GeneratedUnit
from arcagi_pipeline.verification_program import VERSION

def artifacts():
    a=validation_demo.artifacts();b=a[2];b['slots']=design(3)[2]['slots']
    for i,s in enumerate(b['slots']):
        s['function_anchor_ids']=['progress','failure'];s['behavior_requirements'][0]['anchor_refs']=['progress']
        s['probe_plan']=[{'id':'failure','kind':'reset_failure'},{'id':'base','kind':'behavior'},{'id':'variant','kind':'counterfactual','baseline':'base'}]
        n=3+i*2
        def read(name,*p):return {'op':'read','trace':name,'path':list(p)}
        def eq(left,v):return {'op':'eq','args':[left,{'op':'literal','value':v}]}
        s['validation_obligations']=[
          {'id':'failure-reset','phase':'runtime','required':True,'function_ref':s['id'],'anchor_refs':['failure'],'evidence_refs':['e'],'proof_scope':'observed_witness','traces':['failure'],
           'expression':{'op':'and','args':[eq(read('failure','trajectory',n,'observation','projected','failed'),True),eq(read('failure','trajectory',n+1,'observation','projected','failed'),False),
             {'op':'sequence','items':[{'op':'event_ref','trace':'failure','index':n},{'op':'event_ref','trace':'failure','index':n+1}]}]}},
          {'id':'contrast','phase':'runtime','required':True,'function_ref':s['id'],'anchor_refs':['progress'],'evidence_refs':['e'],'proof_scope':'declared_comparison','traces':['base','variant'],
           'expression':{'op':'ne','args':[read('base','trajectory',1,'observation','projected','value'),read('variant','trajectory',1,'observation','projected','value')]}},
          {'id':'own-smoke','phase':'runtime','required':True,'function_ref':s['id'],'anchor_refs':['progress'],'evidence_refs':['e'],'proof_scope':'observed_witness','traces':['level_smoke'],
           'expression':eq(read('level_smoke','trajectory',0,'engine_position'),i)}]
    a[3]['diagnosis']={'next_check':{'operation':'adapter_smoke'}}
    a[3]['files'][0]['content']='from examples.neutral_machine.handoff_native_demo import create_plugin\n'
    return a

class Plugin(native.NativePlugin):
    def generate_units(self,inputs,seed):
        b=json.loads(inputs.blueprint.read_text(encoding='utf-8'));levels=[]
        for i,s in enumerate(b['slots']):
            start=2+i*2;cfg=self.config({'start':start,'target':start+1});variant=self.config({'start':start+1,'target':start+2})
            claims=[{'id':'progress','statement':'target completed','check':'original engine'}]+[{'id':o['id'],'statement':'neutral execution obligation','check':'machine program'} for o in s['validation_obligations']]
            probes=({'id':'failure','kind':'reset_failure','steps':[{'op':'step','action':2}]*(start+1)+[{'op':'reset'}]},
                    {'id':'base','kind':'behavior','steps':[{'op':'step','action':2}]},
                    {'id':'variant','kind':'counterfactual','baseline':'base','variant':{'configuration':variant},'steps':[{'op':'step','action':2}]})
            levels.append(GeneratedLevel(str(i),s['id'],cfg,{'completed':i+1},{'slot_id':s['id'],'rule_ids':s['rule_ids'],'config':cfg,'rationale':'neutral scoped validation','claims':claims},
                observation_bindings={'value':['actor','_x'],'failed':['failed']},probes=probes))
        yield GeneratedUnit('neutral-three-targets','engine','NativeEngine','raw-arcengine',tuple(levels))
    def level_smoke_action(self,unit,level):return 2

def create_plugin(context):return Plugin()
def run(output,template):
    original=native.artifacts
    try:native.artifacts=artifacts;return native.run(output,template,timeout_seconds=180)
    finally:native.artifacts=original
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--runtime-template',required=True,type=Path);a=p.parse_args()
    report=run(a.output,a.runtime_template);print(json.dumps({'status':report['status'],'scope':report.get('completion_scope')}));raise SystemExit(0 if report['status']=='pass' else 1)
