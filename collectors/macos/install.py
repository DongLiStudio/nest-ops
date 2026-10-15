"""Install a user LaunchAgent; requires a logged-in macOS user, not sudo."""
import json,os,plistlib,shutil,subprocess,sys
from pathlib import Path
if sys.platform!='darwin' or os.getuid()==0:raise SystemExit('Run as the logged-in macOS user')
if len(sys.argv)!=2:raise SystemExit('Usage: python3 install.py /path/to/config.json')
config=Path(sys.argv[1]).resolve();value=json.loads(config.read_text())
if not value['endpoint'].startswith('https://'):raise SystemExit('HTTPS required')
dest=Path.home()/'Library/Application Support/NestOps';dest.mkdir(parents=True,exist_ok=True);dest.chmod(0o700)
if (dest/'config.json').exists():raise SystemExit('Existing installation; upgrade explicitly')
shutil.copyfile(config,dest/'config.json');(dest/'config.json').chmod(0o600)
shutil.copyfile(Path(__file__).with_name('collector.rb'),dest/'collector.rb')
label='org.nestops.collector';plist=Path.home()/'Library/LaunchAgents'/f'{label}.plist';plist.parent.mkdir(exist_ok=True)
plist.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':['/usr/bin/ruby',str(dest/'collector.rb'),str(dest/'config.json')],'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15}))
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True)
print('Installed user collector. It runs after login.')
