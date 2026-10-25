"""Opt-in physical acceptance using the exact GUI helper and its Keychain identity."""
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'dist/Pocket Dimension.app/Contents/Resources/cli/pocket-dimension-gui'

def main():
    report={'checks':[]}
    def run(*args,expected=0):
        result=subprocess.run([str(HELPER),*map(str,args)],capture_output=True,text=True,timeout=120)
        if result.returncode!=expected:raise AssertionError(f'{args[0]} exited {result.returncode}: {result.stderr}')
        return json.loads(result.stdout) if result.stdout.strip().startswith('{') or result.stdout.strip().startswith('[') else result.stdout
    def check(name,action):
        started=time.monotonic();action();report['checks'].append({'check':name,'passed':True,'seconds':round(time.monotonic()-started,2)});print('PASS '+name,flush=True)
    def require(value):
        if not value:raise AssertionError('Acceptance assertion failed')
    state=run('connect','--json')
    if not state.get('selected') or not state.get('name','').startswith('PocketDimension-USB-check-'):raise RuntimeError('Refusing to test outside generated fixture')
    report.update(device=state['device_id'],scope=state['name'])
    with tempfile.TemporaryDirectory(prefix='PocketDimension-Secure-USB-') as temporary:
        root=Path(temporary);data=bytes(i%251 for i in range(65536))*192;checksum=hashlib.sha256(data).hexdigest()
        source=root/'source.bin';source.write_bytes(data)
        check('persisted trust and repeated encrypted sessions',lambda:require(run('connect','--json')['scope_id']==state['scope_id']))
        def tree():
            result=run('pull',root/'tree','--json','--resume');require(result['files']==2 and (root/'tree/large.bin').read_bytes()==data and (root/'tree/nested/empty').stat().st_size==0)
        check('recursive encrypted download and SHA-256 readback',tree)
        def cancel(command,*args):
            process=subprocess.Popen([str(HELPER),command,*map(str,args),'--resume','--json','--json-progress'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                for line in process.stderr:
                    try:event=json.loads(line)
                    except ValueError:continue
                    if event.get('event')=='progress' and event.get('bytes',0)>=131072:
                        process.send_signal(signal.SIGINT);break
                out,err=process.communicate(timeout=40)
                require(process.returncode==130)
            finally:
                if process.poll() is None:process.kill();process.communicate()
        def download_resume():
            destination=root/'resumed.bin';cancel('pull',destination,'--remote','large.bin');require(not destination.exists())
            result=run('pull',destination,'--remote','large.bin','--resume','--json');require(result['resumed_bytes']>=131072 and destination.read_bytes()==data and result['sha256']==checksum)
        check('encrypted cancellation, session reuse and download resume',download_resume)
        def upload_resume():
            cancel('push',source,'--remote','uploaded.bin')
            require(not any(entry['name']=='uploaded.bin' for entry in run('ls','--json')))
            result=run('push',source,'--remote','uploaded.bin','--resume','--json');require(result['resumed_bytes']>=131072 and result['sha256']==checksum)
            run('pull',root/'readback','--remote','uploaded.bin','--json');require((root/'readback').read_bytes()==data)
        check('encrypted upload cancellation, resume and independent readback',upload_resume)
        def conflicts():
            require(run('push',source,'--remote','uploaded.bin','--conflict','skip','--json')['status']=='skipped')
            require(run('push',source,'--remote','uploaded.bin','--conflict','keep-both','--json')['remote']!='uploaded.bin')
            run('push',source,'--remote','uploaded.bin','--json',expected=1)
            run('pull',root/'wrong-phone','--remote','large.bin','--device-id','wrong','--json',expected=1);require(not (root/'wrong-phone').exists())
        check('no-overwrite policies and wrong-phone refusal',conflicts)
        finish=root/'__finish__';finish.mkdir()
        # The fixture deliberately closes its Activity as soon as this marker exists.
        result=subprocess.run([str(HELPER),'push',str(finish),'--json'],capture_output=True,text=True,timeout=40)
        report['cleanup_requested']=True
    (ROOT/'build/security-phone-acceptance.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
