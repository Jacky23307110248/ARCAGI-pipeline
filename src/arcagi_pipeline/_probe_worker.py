"""One isolated original-engine probe, under the stdlib guardian deadline."""
import json,sys,hashlib,importlib.util,uuid
from pathlib import Path
from dataclasses import replace
from .generated_game import GeneratedUnit,GeneratedLevel,NativeDriver,SourceDriver,level_configuration
from .verification_program import _run_probes_in_process

def worker(request,response):
 data=json.loads(Path(request).read_text(encoding='utf-8'));body=data['unit']
 unit=GeneratedUnit(**{**body,'levels':tuple(GeneratedLevel(**l) for l in body['levels'])})
 source=Path(data['recipe']['source_path'])
 if hashlib.sha256(source.read_bytes()).hexdigest()!=data['source_sha']:raise ValueError('source_changed')
 module=None;plugin=None;factory=data['recipe'].get('factory')
 if unit.profile=='raw-arcengine':
  if not factory:raise ValueError('native_probe_factory_required')
  for ref in factory['file_refs']+[{'path':factory['runtime_config'],'sha256':factory['runtime_config_sha256']}]:
   if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('probe_factory_or_runtime_changed')
  from .runtime_control import RuntimeContext
  context=RuntimeContext(Path(factory['runtime_config']));entry=Path(factory['module_path']);sys.path.insert(0,str(entry.parent))
  spec=importlib.util.spec_from_file_location('_isolated_probe_plugin',entry,submodule_search_locations=[str(entry.parent)])
  plugin_module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=plugin_module;spec.loader.exec_module(plugin_module)
  plugin=getattr(plugin_module,factory['factory_name'])(context)
  module=context.modules.get(unit.source_id) or context.load_source(unit.source_id)
 else:
  spec=importlib.util.spec_from_file_location('_isolated_original_'+uuid.uuid4().hex,source)
  module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
 cls=getattr(module,unit.engine_class)
 if cls.__module__!=module.__name__:raise ValueError('original_class_required')
 methods={k:v for k,v in vars(cls).items() if callable(v)}
 def fresh():
  if any(getattr(cls,k) is not v for k,v in methods.items()):raise ValueError('source_method_changed')
  e=cls(**unit.constructor)
  return NativeDriver(e) if unit.profile=='raw-arcengine' else SourceDriver(e,module)
 if plugin:
  compiled=plugin.compile_levels(unit,module)
  if [level_configuration(x) for x in compiled]!=data['expected']:raise ValueError('isolated_compilation_mismatch')
 else:compiled=[l.configuration for l in unit.levels]
 result=_run_probes_in_process(unit,data['index'],fresh,compiled,data['expected'],data['unit_hash'],data['source_sha'],data['runtime_id'],data['cap'],Path(data['output_dir']),
           compile_variant=(lambda changed:plugin.compile_levels(changed,module)) if plugin else None,session_id=data['session_id'])
 if hashlib.sha256(source.read_bytes()).hexdigest()!=data['source_sha'] or any(getattr(cls,k) is not v for k,v in methods.items()):raise ValueError('source_changed')
 if factory:
  for ref in factory['file_refs']:
   if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('probe_factory_changed')
 with Path(response).open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,allow_nan=False)
if __name__=='__main__':
 try:worker(sys.argv[1],sys.argv[2])
 except Exception as exc:
  from .diagnostics import exception_facts
  with Path(sys.argv[2]+'.error.json').open('x',encoding='utf-8') as stream:json.dump(exception_facts(exc),stream)
  raise SystemExit(1)
