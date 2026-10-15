"""Generate one scoped credential and allowlisted configuration from the directory."""
import json,os,secrets,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if len(sys.argv)!=3:raise SystemExit('Usage: add_collector.py server-id https://dashboard.example.com')
host,base=sys.argv[1:]
from urllib.parse import urlsplit
url=urlsplit(base)
if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise SystemExit('A clean HTTPS origin is required')
registry=json.loads((ROOT/'runtime/console/registry.json').read_text(encoding='utf-8'))
if host not in {s['id'] for s in registry['servers']}:raise SystemExit('Unknown registered server')
if not host.replace('-','').replace('_','').isalnum():raise SystemExit('Server id must be filename safe')
target=ROOT/'runtime/console/tokens.json';tokens=json.loads(target.read_text(encoding='utf-8'))
if host in tokens:raise SystemExit('Token exists; revoke and rotate explicitly')
token=secrets.token_urlsafe(48);tokens[host]=token
services=[{k:v for k,v in s.items() if k in ('id','containers','units','timer','process')} for s in registry['services'] if s['server']==host and not s.get('probe')]
old=os.umask(0o077)
try:
    output=ROOT/'runtime'/f'collector-{host}.json'
    with output.open('x') as f:json.dump({'endpoint':base.rstrip('/')+'/_ops/ingest','server':host,'token':token,'services':services},f,indent=2)
    temp=target.with_suffix('.tmp');temp.write_text(json.dumps(tokens,indent=2));temp.replace(target)
finally:os.umask(old)
print('Private collector configuration written. Restart console to reload tokens; transfer securely, then remove the transfer copy.')
