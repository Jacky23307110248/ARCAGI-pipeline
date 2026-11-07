"""Offline end-to-end validation-program workflow using a scripted provider."""
import json,hashlib
from pathlib import Path
from dataclasses import replace
from .generated import Plugin,design
from .demo import prepare
from arcagi_pipeline.verification_program import VERSION
from arcagi_pipeline.generated_game import CONTRACT
from arcagi_pipeline.machine import run_machine

def read(trace,*path):return {'op':'read','trace':trace,'path':list(path)}
def literal(value):return {'op':'literal','value':value}
def op(name,*args):return {'op':name,'args':list(args)}
def obligation(name,traces,expression,anchor='failure'):
 return {'id':name,'phase':'runtime','required':True,'function_ref':'0','anchor_refs':[anchor],'evidence_refs':['e'],
         'proof_scope':'declared_comparison' if 'variant' in traces else 'trace_comparison' if len(traces)>1 else 'observed_witness','traces':traces,'expression':expression}
def artifacts():
 e,r,b=design(1)
 b.update(validation_program_version=VERSION,commitment_version=1,function_anchors=[{'id':a,'statement':statement,'evidence_refs':['e']} for a,statement in [('progress','Original target completion'),('failure','Original negative-value failure and reset')]])
 s=b['slots'][0];s['function_anchor_ids']=['progress','failure'];s['behavior_requirements'][0]['anchor_refs']=['progress']
 s['probe_plan']=[{'id':'failure','kind':'reset_failure'},{'id':'base','kind':'behavior'},{'id':'variant','kind':'counterfactual','baseline':'base'}]
 s['validation_obligations']=[
  obligation('failure-reset',['failure'],op('and',op('eq',read('failure','trajectory',1,'observation','failed'),literal(True)),op('eq',read('failure','trajectory',2,'observation','failed'),literal(False)))),
  obligation('comparison',['base','variant'],op('ne',read('base','trajectory',1,'observation','value'),read('variant','trajectory',1,'observation','value')),'progress'),
  obligation('whole-unit',['unit','smoke'],op('and',op('eq',read('unit','status'),literal('complete')),op('eq',read('smoke','status'),literal('complete'))),'progress')]
 s['validation_obligations'][-1]['observation_scope']='unit'
 bundle={'entrypoint':'plugin.py','factory':'create_plugin','files':[{'path':'plugin.py','content':'from examples.neutral_machine.validation_demo import create_plugin\n'}]}
 return [e,r,b,bundle]
class ValidationPlugin(Plugin):
 def generate_units(self,inputs,seed):
  blueprint=json.loads(inputs.blueprint.read_text(encoding='utf-8'))
  for unit in super().generate_units(inputs,seed):
   levels=[]
   for level in unit.levels:
    claims=level.construction['claims']+[{'id':r['id'],'statement':'Neutral runtime obligation','check':'machine-owned trace expression'} for r in blueprint['slots'][0]['validation_obligations']]
    probes=({'id':'failure','kind':'reset_failure','steps':[{'op':'step','action':{'delta':-20}},{'op':'reset'}]},
            {'id':'base','kind':'behavior','steps':[{'op':'step','action':'forward'}]},
            {'id':'variant','kind':'counterfactual','baseline':'base','variant':{'configuration':{'start':10,'target':11}},'steps':[{'op':'step','action':'forward'}]})
    levels.append(replace(level,construction={**level.construction,'claims':claims},probes=probes))
   yield replace(unit,levels=tuple(levels))
def create_plugin(context):return ValidationPlugin(1)
def run(root):
 root=Path(root).resolve();cfg,provider=prepare(root/'spec',artifacts());config=json.loads(cfg.read_text());config['production_contract']=CONTRACT;cfg.write_text(json.dumps(config))
 specpath=cfg.parent/'workflow.json';spec=json.loads(specpath.read_text());api=Path(__file__).resolve().parents[2]/'src/arcagi_pipeline'
 for task in spec['tasks']:
  if task['id'] in ('blueprint','implementation'):
   task['validation_program']=VERSION
   task['inputs'].append({'id':'validation_program_api','role':'validation_program_api','path':str(api/'verification_program.py'),'sha256':hashlib.sha256((api/'verification_program.py').read_bytes()).hexdigest()})
  if task['id']=='blueprint':task['validation_inputs']={'evidence':'evidence','rules':'rules'}
  if task['id']=='implementation':
   task['execution_contract']=CONTRACT
   task['inputs'].append({'id':'generated_game_api','role':'generated_game_api','path':str(api/'generated_game.py'),'sha256':hashlib.sha256((api/'generated_game.py').read_bytes()).hexdigest()})
 specpath.write_text(json.dumps(spec))
 runtimepath=cfg.parent/'runtime.json';runtime=json.loads(runtimepath.read_text());source=Path(__file__).with_name('generated_source.py')
 runtime['import_roots']=[str(Path(__file__).resolve().parents[2])];runtime['sources']=[{'id':'engine','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}];runtimepath.write_text(json.dumps(runtime))
 report=run_machine(cfg,root/'workflow',root/'execution',provider=provider)
 return report
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();result=run(a.output);print(json.dumps({'status':result['status'],'scope':result.get('completion_scope')}));raise SystemExit(0 if result['status']=='pass' else 1)
