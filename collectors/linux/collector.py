"""Allowlisted local checks, outbound HTTPS only; no remote command API."""
import json,os,shutil,subprocess,time,urllib.request
from pathlib import Path

def run(args):
    try:return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=8)
    except (OSError,subprocess.TimeoutExpired):return None

def collect(config):
    docker=shutil.which('docker') or ('/usr/local/bin/docker' if Path('/usr/local/bin/docker').exists() else None)
    states={}; result={}
    if docker:
        p=run([docker,'ps','-a','--format','{{.Names}}|{{.State}}|{{.Status}}'])
        if p and p.returncode==0:
            for line in p.stdout.splitlines():
                name,state,status=line.split('|',2);states[name]='degraded' if 'unhealthy' in status else 'up' if state=='running' else 'down'
    for service in config['services']:
        state='unknown'; names=service.get('containers',[]);units=service.get('units',[])
        if names:
            values=[states.get(n,'down' if docker and p and p.returncode==0 else 'unknown') for n in names]
            state='down' if 'down' in values else 'degraded' if 'degraded' in values else 'unknown' if 'unknown' in values else 'up'
        elif units:
            values=[]
            for unit in units:
                check=run(['systemctl','is-active',unit]); values.append('up' if check and check.returncode==0 else 'down' if check and check.stdout.strip() in ['inactive','failed','unknown'] else 'unknown')
            state='down' if 'down' in values else 'unknown' if 'unknown' in values else 'up'
        elif service.get('timer'):
            check=run(['systemctl','show',service['timer'],'--property=Result','--property=ExecMainStatus','--property=ExecMainExitTimestampMonotonic'])
            if check and check.returncode==0:
                values=dict(line.split('=',1) for line in check.stdout.splitlines() if '=' in line)
                state='unknown' if values.get('ExecMainExitTimestampMonotonic','0')=='0' else 'up' if values.get('Result')=='success' and values.get('ExecMainStatus')=='0' else 'down'
        elif service.get('process'):
            check=run(['pgrep','-x',service['process']]);state='up' if check and check.returncode==0 else 'down' if check and check.returncode==1 else 'unknown'
        result[service['id']]={'state':state}
    metrics={}
    try:metrics['load1']=round(os.getloadavg()[0],2)
    except (OSError,AttributeError):pass
    disk=shutil.disk_usage('/');metrics['disk_percent']=round(100*disk.used/disk.total,1)
    try:
        info=dict((line.split(':')[0],int(line.split()[1])) for line in Path('/proc/meminfo').read_text().splitlines())
        metrics['memory_percent']=round(100*(1-info['MemAvailable']/info['MemTotal']),1)
        metrics['uptime_seconds']=float(Path('/proc/uptime').read_text().split()[0])
    except (OSError,ValueError,KeyError):pass
    return {'services':result,'metrics':metrics}

if __name__=='__main__':
    import sys
    config=json.loads(Path(sys.argv[1]).read_text())
    if not config['endpoint'].startswith('https://'):raise ValueError('HTTPS required')
    while True:
        try:
            data=json.dumps(collect(config)).encode()
            req=urllib.request.Request(config['endpoint'],data=data,headers={'Content-Type':'application/json','Authorization':'Bearer '+config['token'],'X-Server-Profile':config['server']})
            with urllib.request.urlopen(req,timeout=10) as r:r.read(128)
        except Exception:pass
        time.sleep(60)
