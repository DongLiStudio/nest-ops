import os,subprocess,sys
from pathlib import Path
if sys.platform!='darwin' or os.getuid()==0:raise SystemExit('Run as the logged-in macOS user')
label='org.nestops.collector';subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],check=False)
(Path.home()/'Library/LaunchAgents'/f'{label}.plist').unlink(missing_ok=True)
(Path.home()/'Library/Application Support/NestOps/collector.rb').unlink(missing_ok=True)
print('Collector removed. Private config retained; revoke its token at the center.')
