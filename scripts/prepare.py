"""Render only generic host placeholders and copy the reviewed extension assets."""
import re,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def prepare(root=ROOT):
    values={}
    for line in (root/'.env').read_text(encoding='utf-8').splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);values[k]=v.strip()
    text=(root/'examples/nginx.conf').read_text(encoding='utf-8')
    for key in ['DASHBOARD_HOST','AUTH_HOST']:
        value=values.get(key,'')
        if not re.fullmatch(r'[a-zA-Z0-9.-]+',value):raise SystemExit('Invalid or missing '+key)
        text=text.replace('@'+key+'@',value)
    (root/'runtime/nginx.conf').write_text(text)
    (root/'runtime/nginx.conf').chmod(0o644)
    env=root/'runtime/homepage.env';lines=env.read_text(encoding='utf-8').splitlines()
    lines=[('HOMEPAGE_EXTERNAL_URL=https://'+values['DASHBOARD_HOST']) if x.startswith('HOMEPAGE_EXTERNAL_URL=') else x for x in lines]
    env.write_text('\n'.join(lines)+'\n')
    for name in ['custom.js','custom.css']:
        source=root/'homepage'/name
        if not source.is_file():raise SystemExit('Missing reviewed frontend asset: '+str(source))
        shutil.copyfile(source,root/'runtime/homepage'/name)
    print('Rendered configuration. Review it before starting Compose.')
if __name__=='__main__':prepare()
