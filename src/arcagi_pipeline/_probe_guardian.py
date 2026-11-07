"""Stdlib-only bounded process supervisor; never imports game code."""
import json,subprocess,sys,time,os
from pathlib import Path
def main():
 request=Path(sys.argv[1]);outcome=Path(sys.argv[2])
 data=json.loads(request.read_text(encoding='utf-8'))
 remaining=data['deadline']-time.monotonic();result={'status':'timeout'}
 if remaining>0:
  try:
   child=subprocess.Popen(data['command'],cwd=data['cwd'],env=os.environ.copy(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    code=child.wait(timeout=max(.001,data['deadline']-time.monotonic()));result={'status':'exited','returncode':code,'pid':child.pid}
   except subprocess.TimeoutExpired:
    child.kill();child.wait();result={'status':'timeout','pid':child.pid}
  except OSError:result={'status':'start_failed'}
 with outcome.open('x',encoding='utf-8') as stream:json.dump(result,stream)
if __name__=='__main__':main()
