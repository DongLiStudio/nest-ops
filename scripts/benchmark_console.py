"""Synthetic read-model benchmark, not a production/network measurement."""
import gzip,importlib.util,io,json,os,statistics,subprocess,tempfile,time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def run(source,label,base):
    directory=base/label;directory.mkdir()
    os.environ.update(OPS_DATA=str(directory),OPS_REGISTRY=str(ROOT/'examples/registry.json'),OPS_TOKENS=str(base/'tokens.json'))
    spec=importlib.util.spec_from_loader(label,loader=None);module=importlib.util.module_from_spec(spec)
    module.__file__=str(ROOT/'console/server.py');exec(compile(source,module.__file__,'exec'),module.__dict__)
    now=time.time()
    module.SERVICES={f'demo:{i}':dict(id=f'demo:{i}',server='demo',name=f'Demo {i}',app=True,category='demo') for i in range(67)}
    module.SERVERS={'demo':dict(id='demo',name='Demo',address='192.0.2.1')}
    with module.DB:
        for sid in module.SERVICES:
            module.DB.executemany('INSERT INTO spans(service,start,end,state,detail,ms) VALUES(?,?,?,?,?,?)',[(sid,now-86400+j*3600,now-86400+(j+1)*3600,'down' if j==6 else 'up','synthetic',42) for j in range(24)])
            module.DB.executemany('INSERT INTO samples VALUES(?,?,?)',[(sid,now-j*300,42+j%10) for j in range(288)])
    def household(*args,**kwargs):time.sleep(.08);return io.BytesIO(b'{}')
    timings=[];payload=None
    with patch.object(module.urllib.request,'urlopen',side_effect=household):
        for _ in range(5):
            start=time.perf_counter();payload=module.snapshot('24h');timings.append((time.perf_counter()-start)*1000)
    raw=json.dumps(payload,ensure_ascii=False,separators=(', ',': ')).encode()
    result=dict(first_ms=round(timings[0],3),repeat_median_ms=round(statistics.median(timings[1:]),3),json_bytes=len(raw),gzip_bytes=len(gzip.compress(raw,compresslevel=4,mtime=0)))
    module.DB.close();return result

if __name__=='__main__':
    baseline=subprocess.check_output(['git','show','a680e23:console/server.py'],cwd=ROOT).decode()
    current=(ROOT/'console/server.py').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as directory:
        base=Path(directory);(base/'tokens.json').write_text('{}')
        print(json.dumps({'scope':'67 synthetic services; 19296 samples; household delay 2x80ms; no authentication/network timing','before':run(baseline,'before',base),'after':run(current,'after',base)},indent=2))
