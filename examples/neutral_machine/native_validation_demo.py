"""Declared-runtime native probes and variants through a real worker and guardian."""
import json,hashlib
from pathlib import Path
from dataclasses import replace
from . import validation_demo
from .demo import prepare
from arcagi_pipeline.generated_game import GeneratedLevel,GeneratedUnit,CONTRACT,digest,level_configuration
from arcagi_pipeline.production import ModelSearch
from arcagi_pipeline.machine import run_machine

def artifacts():
 a=validation_demo.artifacts()
 def native_paths(node):
  if node.get('op')=='read' and 'observation' in node['path'] and node['path'][-1] in ('value','failed'):node['path'].insert(-1,'projected')
  for child in node.get('args',node.get('items',[])):native_paths(child)
 for o in a[2]['slots'][0]['validation_obligations']:native_paths(o['expression'])
 a[3]['files'][0]['content']='from examples.neutral_machine.native_validation_demo import create_plugin\n'
 return a
class NativePlugin:
 production_contract=CONTRACT
 def build(self,spec):
  from arcengine import Level,Sprite
  return Level(sprites=[Sprite([[1]],name='actor',x=spec['start'],y=0)],grid_size=(8,8),data={'target':spec['target']})
 def config(self,spec):return {'model':spec,'engine_level':level_configuration(self.build(spec))}
 def generate_units(self,inputs,seed):
  b=json.loads(inputs.blueprint.read_text(encoding='utf-8'));config=self.config({'start':2,'target':3})
  claims=[{'id':'progress','statement':'target completed','check':'native progress'}]+[{'id':o['id'],'statement':'neutral machine obligation','check':'machine program'} for o in b['slots'][0]['validation_obligations']]
  probes=({'id':'failure','kind':'reset_failure','steps':[{'op':'step','action':2}]*3+[{'op':'reset'}]},
          {'id':'base','kind':'behavior','steps':[{'op':'step','action':1}]},
          {'id':'variant','kind':'counterfactual','baseline':'base','variant':{'configuration':self.config({'start':4,'target':5})},'steps':[{'op':'step','action':1}]})
  # The reset is event4 instead of event2 in this native fixture.
  level=GeneratedLevel('native','0',config,{'completed':1},{'slot_id':'0','rule_ids':['move'],'config':config,'rationale':'neutral native validation','claims':claims},observation_bindings={'value':['actor','_x'],'failed':['failed']},probes=probes)
  yield GeneratedUnit('native-unit','engine','NativeEngine','raw-arcengine',(level,))
 def compile_levels(self,unit,module):return [self.build(l.configuration['model']) for l in unit.levels]
 def search_generated(self,level,cap):return ModelSearch('candidate',(1,),evidence={'configuration_sha256':digest(level.configuration),'goal_sha256':digest(level.goal),'model_version':'native-neutral/v1'})
 def smoke_action(self,unit):return 1
def create_plugin(context):return NativePlugin()
def run(root,template,timeout_seconds=30):
 root=Path(root).resolve();a=artifacts()
 # Failure occurs at event3 and reset at event4.
 e=a[2]['slots'][0]['validation_obligations'][0]['expression']
 e['args'][0]['args'][0]['path'][1]=3;e['args'][1]['args'][0]['path'][1]=4
 cfg,provider=prepare(root/'spec',a);c=json.loads(cfg.read_text());c['production_contract']=CONTRACT;c['timeout_seconds']=timeout_seconds;cfg.write_text(json.dumps(c))
 sp=cfg.parent/'workflow.json';spec=json.loads(sp.read_text());api=Path(__file__).resolve().parents[2]/'src/arcagi_pipeline'
 for task in spec['tasks']:
  if task['id'] in ('blueprint','implementation'):
   task['validation_program']='arcagi-validation-program/v1'
   task['inputs'].append({'id':'validation_program_api','role':'validation_program_api','path':str(api/'verification_program.py'),'sha256':hashlib.sha256((api/'verification_program.py').read_bytes()).hexdigest()})
  if task['id']=='blueprint':task['validation_inputs']={'evidence':'evidence','rules':'rules'}
  if task['id']=='implementation':
   task['execution_contract']=CONTRACT;task['inputs'].append({'id':'generated_game_api','role':'generated_game_api','path':str(api/'generated_game.py'),'sha256':hashlib.sha256((api/'generated_game.py').read_bytes()).hexdigest()})
 sp.write_text(json.dumps(spec))
 runtime=json.loads(template.read_text(encoding='utf-8'));runtime['python']=str((template.parent/runtime['python']).resolve());runtime['import_roots']=[str(Path(__file__).resolve().parents[2])]+[str((template.parent/p).resolve()) for p in runtime['import_roots']]
 source=Path(__file__).with_name('native_validation_source.py');runtime['sources']=[{'id':'engine','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]
 (cfg.parent/'runtime.json').write_text(json.dumps(runtime))
 return run_machine(cfg,root/'workflow',root/'execution',provider=provider)
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--runtime-template',type=Path,required=True);args=p.parse_args();result=run(args.output,args.runtime_template);print(json.dumps({'status':result['status'],'scope':result.get('completion_scope')}));raise SystemExit(0 if result['status']=='pass' else 1)
