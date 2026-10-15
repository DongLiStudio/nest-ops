"""Offline release gate: source provenance, generic configuration and Python syntax."""
import ast,hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errors=[]
manifest=json.loads((ROOT/'upstream-manifest.json').read_text(encoding='utf-8'))
actual={str(p.relative_to(ROOT/'vendor/homepage')).replace('\\','/') for p in (ROOT/'vendor/homepage').rglob('*') if p.is_file()}
if actual!=set(manifest['files']):errors.append('upstream archive file set differs')
for name,expected in manifest['files'].items():
    p=ROOT/'vendor/homepage'/name
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:errors.append('upstream mismatch: '+name)
if (ROOT/'vendor/homepage/.git').exists():errors.append('nested upstream git repository')
for p in ROOT.rglob('*'):
    if not p.is_file() or any(x in p.relative_to(ROOT).parts for x in ['vendor','runtime','.git','__pycache__']):continue
    if p.suffix=='.py':
        try:ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
        except SyntaxError as e:errors.append(str(e))
    if p.suffix.lower() in ['.png','.jpg','.webp','.ico']:continue
    try:text=p.read_text(encoding='utf-8')
    except UnicodeError:continue
    # Example ranges and local interfaces are acceptable; other IPv4 literals are private deployment facts.
    for ip in re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])',text):
        if ip not in ['127.0.0.1','0.0.0.0'] and not ip.startswith(('192.0.2.','198.51.100.','203.0.113.')):errors.append(str(p.relative_to(ROOT))+': non-example IP')
    if re.search(r'-----BEGIN (?:OPENSSH|RSA|EC|PRIVATE).*KEY-----',text):errors.append(str(p)+': private key marker')
registry=json.loads((ROOT/'examples/registry.json').read_text(encoding='utf-8'))
servers={s['id'] for s in registry['servers']}
if len(servers)!=len(registry['servers']):errors.append('duplicate server ids')
ids=set()
for s in registry['services']:
    if s['id'] in ids or s['server'] not in servers:errors.append('invalid service identity')
    ids.add(s['id'])
for name in ['compose.yml','LICENSE','README.md','homepage/custom.js','homepage/custom.css','console/ui.js','console/ui.css']:
    if not (ROOT/name).is_file():errors.append('missing '+name)
if errors:
    print('\n'.join(errors));sys.exit(1)
print(f'PASS: {len(manifest["files"])} pinned upstream files, generic examples and Python syntax')
