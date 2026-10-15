"""Push only allowlisted status fields over verified HTTPS. Never push the UPS API token."""
import json,os,time,urllib.request
from pathlib import Path
TOKEN=Path(os.environ.get('STATUS_TOKEN_FILE','/run/secrets/status-token')).read_text().strip()
UPSTREAM=os.environ.get('UPS_URL','http://frontend')
TARGET=os.environ['STATUS_URL']
if not TARGET.startswith('https://'):raise ValueError('HTTPS required for outbound status')
DDNS_URL=os.environ.get('DDNS_URL','http://host.docker.internal:9876/')
def get(url,token=None):
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token} if token else {})
    with urllib.request.urlopen(req,timeout=2) as r:return json.loads(r.read(65536))
def sample():
    out={'ups':{'status':'UNKNOWN'},'ddns':False}
    try:
        token=get(UPSTREAM+'/api/bootstrap')['token']
        data=get(UPSTREAM+'/api/status',token)
        out['ups']={k:data.get(k) for k in ['status','battery_charge','battery_runtime','load_percent']}
    except Exception:pass
    try:
        with urllib.request.urlopen(DDNS_URL,timeout=2) as r:out['ddns']=r.status<500
    except Exception:pass
    return out
while True:
    try:
        req=urllib.request.Request(TARGET,data=json.dumps(sample()).encode(),headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=4) as r:r.read(1024)
    except Exception:pass
    time.sleep(15)
