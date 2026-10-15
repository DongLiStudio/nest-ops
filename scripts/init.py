"""Create private deployment inputs once. Never overwrite an existing runtime."""
import json,os,secrets,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def initialize(root=ROOT):
    runtime=root/'runtime'
    if runtime.exists():raise SystemExit('runtime already exists; refusing to overwrite')
    old=os.umask(0o077)
    try:
        for name in ['homepage','images','console','history','status','secrets','tls','auth/config','auth/data']:(runtime/name).mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/'examples/registry.json',runtime/'console/registry.json')
        (runtime/'console/tokens.json').write_text('{}\n')
        (runtime/'console/entries.json').write_text('{}\n')
        (runtime/'console/notify.json').write_text('{"enabled": false}\n')
        (runtime/'secrets/status-token').write_text(secrets.token_urlsafe(48)+'\n')
        (runtime/'homepage.env').write_text('HOMEPAGE_AUTH_ENABLED=true\nHOMEPAGE_AUTH_SECRET='+secrets.token_urlsafe(48)+'\nHOMEPAGE_AUTH_PASSWORD='+secrets.token_urlsafe(24)+'\nHOMEPAGE_EXTERNAL_URL=https://dashboard.example.com\n')
        (runtime/'homepage/settings.yaml').write_text('title: 栖云 NestOps\ncolor: slate\nheaderStyle: boxed\n',encoding='utf-8')
        for name in ['services','bookmarks','widgets','docker','kubernetes']:(runtime/f'homepage/{name}.yaml').write_text('[]\n')
        runtime.chmod(0o755)
        (runtime/'tls').chmod(0o755)
    finally:os.umask(old)
    print('Private runtime created. Set the external URL and credentials; run prepare.py next.')
if __name__=='__main__':initialize()
