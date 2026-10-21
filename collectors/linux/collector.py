"""Allowlisted local checks, outbound HTTPS only; no remote command API."""
import json,math,os,shutil,subprocess,time,urllib.request
from datetime import datetime,timedelta
from pathlib import Path

def run(args):
    try:return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=8)
    except (OSError,subprocess.TimeoutExpired):return None

def systemd_properties(unit, properties):
    check = run(['systemctl', 'show', unit] +
                ['--property=' + name for name in properties])
    if not check or check.returncode != 0:
        return None
    return dict(line.split('=', 1) for line in check.stdout.splitlines() if '=' in line)


def _backrest_protobuf_fields(payload):
    """Decode the wire envelope without importing Backrest or reading secrets."""
    def varint(offset):
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(payload):
                raise ValueError('Truncated protobuf')
            byte = payload[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, offset
        raise ValueError('Invalid protobuf varint')

    offset = 0
    while offset < len(payload):
        key, offset = varint(offset)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError('Invalid protobuf field')
        if wire == 0:
            value, offset = varint(offset)
        elif wire == 1:
            value = payload[offset:offset + 8]
            offset += 8
        elif wire == 2:
            size, offset = varint(offset)
            value = payload[offset:offset + size]
            offset += size
        elif wire == 5:
            value = payload[offset:offset + 4]
            offset += 4
        else:
            raise ValueError('Unsupported protobuf wire type')
        if offset > len(payload):
            raise ValueError('Truncated protobuf field')
        yield field, wire, value


def _backrest_operation_kind(payload):
    """Identify real operations; a dry run cannot replace backup evidence.

    Backrest v1.14.1 Operation.op is a oneof at fields 100..108.
    OperationBackup.dry_run is nested bool field 5. Hook and indexing records
    and every dry run are ineligible, even if their status says success.
    """
    kind = None
    for field, wire, value in _backrest_protobuf_fields(payload):
        if wire == 2 and 100 <= field <= 108:
            kind = field
            if field == 100:
                dry_run = False
                for nested_field, nested_wire, nested_value in _backrest_protobuf_fields(value):
                    if nested_field == 5 and nested_wire == 0:
                        dry_run = bool(nested_value)
                if dry_run:
                    kind = None
    return kind


def backrest_job_state(job, server):
    """Require real local plan, runtime, latest backup and full-check receipt."""
    import sqlite3
    from contextlib import closing
    if not isinstance(job, dict) or not isinstance(server, str) or not server.strip():
        return 'unknown'
    required = ('service', 'config', 'oplog', 'plan', 'receipt')
    if any(not isinstance(job.get(k), str) or not job[k].strip() for k in required):
        return 'unknown'
    max_age = job.get('max_age_seconds', 93600)
    if isinstance(max_age, bool) or not isinstance(max_age, (int, float)):
        return 'unknown'
    try:
        if max_age <= 0 or not math.isfinite(max_age):
            return 'unknown'
    except OverflowError:
        return 'unknown'
    service = systemd_properties(job['service'], ('ActiveState', 'UnitFileState'))
    if not service or not service.get('ActiveState') or not service.get('UnitFileState'):
        return 'unknown'
    if service['ActiveState'] != 'active' or service['UnitFileState'] not in ('enabled', 'enabled-runtime'):
        return 'down'
    try:
        config = json.loads(Path(job['config']).read_text(encoding='utf-8'))
        if config.get('instance') != server:
            return 'unknown'
        plans = [p for p in config.get('plans', []) if p.get('id') == job['plan']]
        if len(plans) != 1:
            return 'unknown'
        plan = plans[0]
        schedule = plan.get('schedule', {})
        if 'disabled' in schedule:
            return 'down'
        if not isinstance(schedule.get('cron'), str) or not schedule['cron'].strip():
            return 'unknown'
        repos = [r for r in config.get('repos', []) if r.get('id') == plan.get('repo')]
        if len(repos) != 1 or not isinstance(repos[0].get('guid'), str) or not repos[0]['guid']:
            return 'unknown'
        uri = Path(job['oplog']).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as database:
            # Only local source records are eligible. Sync/imported records and
            # hooks are not evidence that this host completed its own backup.
            rows = database.execute('''
                SELECT o.status,o.snapshot_id,o.start_time_ms,o.operation
                FROM operations o JOIN operation_groups g ON o.ogid=g.ogid
                WHERE g.instance_id=? AND g.plan_id=? AND g.repo_guid=?
                  AND g.original_instance_keyid='' AND o.status<>1
                ORDER BY o.start_time_ms DESC,o.id DESC LIMIT 100
            ''', (server, plan['id'], repos[0]['guid'])).fetchall()
        backups = [row for row in rows if _backrest_operation_kind(row[3]) == 100]
        if not backups:
            return 'unknown'
        latest = backups[0]
        status, snapshot, started, _ = latest
        if status in (4, 5, 6):
            return 'down'
        if status == 7:
            return 'degraded'
        if status not in (2, 3):
            return 'unknown'
        # A running backup does not invalidate the last completed checked copy.
        if status == 2:
            completed = next((r for r in backups[1:] if r[0] in (3, 4, 5, 6, 7)), None)
            if completed is None:
                return 'unknown'
            if completed[0] in (4, 5, 6):
                return 'down'
            if completed[0] == 7:
                return 'degraded'
            _, snapshot, started, _ = completed
        receipt = json.loads(Path(job['receipt']).read_text(encoding='utf-8'))
        if (not isinstance(receipt, dict) or receipt.get('checked') is not True or
                receipt.get('profile') != server or
                not isinstance(snapshot, str) or not snapshot or
                receipt.get('snapshot') != snapshot or not isinstance(receipt.get('utc'), str)):
            return 'unknown'
        stamp = receipt['utc']
        finished = datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
        if finished.utcoffset() != timedelta(0) or finished.timestamp() < started / 1000 - 300:
            return 'unknown'
        age = time.time() - finished.timestamp()
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, KeyError,
            OverflowError, sqlite3.Error):
        return 'unknown'
    if age < -300:
        return 'unknown'
    return 'degraded' if age > max_age else 'up'


def backup_job_state(job, server):
    """Observe a scheduled job and its durable receipt without executing it."""
    if isinstance(job, dict) and job.get('scheduler') == 'backrest':
        return backrest_job_state(job, server)
    if not isinstance(job, dict) or not isinstance(server, str) or not server.strip():
        return 'unknown'
    for key in ('timer', 'service', 'receipt'):
        if not isinstance(job.get(key), str) or not job[key].strip():
            return 'unknown'
    max_age = job.get('max_age_seconds', 93600)
    if (isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or max_age <= 0):
        return 'unknown'
    try:
        if not math.isfinite(max_age):
            return 'unknown'
    except OverflowError:
        return 'unknown'
    timer = systemd_properties(job['timer'], ('ActiveState', 'UnitFileState'))
    if timer is None or not timer.get('ActiveState') or not timer.get('UnitFileState'):
        return 'unknown'
    if (timer['ActiveState'] != 'active' or
            timer['UnitFileState'] not in ('enabled', 'enabled-runtime')):
        return 'down'
    service = systemd_properties(job['service'], ('ActiveState', 'Result', 'ExecMainStatus'))
    if service is None:
        return 'unknown'
    active = service.get('ActiveState')
    if active == 'failed':
        return 'down'
    if active == 'inactive':
        if not service.get('Result') or not service.get('ExecMainStatus'):
            return 'unknown'
        if service['Result'] != 'success' or service['ExecMainStatus'] != '0':
            return 'down'
    elif active not in ('active', 'activating', 'deactivating', 'reloading'):
        return 'unknown'
    # A running retry can retain the previous failure result until it exits.
    # On reboot systemd may forget the last exit, so rely on a persistent receipt.
    try:
        receipt = json.loads(Path(job['receipt']).read_text(encoding='utf-8'))
        if (not isinstance(receipt, dict) or receipt.get('checked') is not True or
                receipt.get('profile') != server or
                not isinstance(receipt.get('snapshot'), str) or not receipt['snapshot'].strip() or
                not isinstance(receipt.get('utc'), str)):
            return 'unknown'
        stamp = receipt['utc']
        completed = datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
        if completed.utcoffset() != timedelta(0):
            return 'unknown'
        age = time.time() - completed.timestamp()
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return 'unknown'
    if age < -300:
        return 'unknown'
    return 'degraded' if age > max_age else 'up'


def docker_command(config):
    if 'docker_binary' not in config:
        return shutil.which('docker') or ('/usr/local/bin/docker' if Path('/usr/local/bin/docker').exists() else None)
    binary = config['docker_binary']
    if not isinstance(binary, str) or not binary or chr(0) in binary:
        return None
    try:
        target = Path(binary)
        if target.is_absolute() and target.is_file() and os.access(binary, os.X_OK):
            return binary
    except (OSError, ValueError):
        pass
    return None


def collect(config):
    docker=docker_command(config)
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
        elif 'backup_job' in service:
            state=backup_job_state(service['backup_job'],config.get('server'))
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
