"""Allowlisted local checks, outbound HTTPS only; no remote command API."""
import json,math,os,re,shutil,subprocess,time,urllib.request
from datetime import datetime,timedelta
from pathlib import Path

BACKUP_ID_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
TRANSPORT_ERROR_RE=re.compile(r'^[a-z][a-z0-9_]{0,63}$')
BACKREST_PAGE_SIZE=256
TRANSPORT_MAX_AGE=900

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
            cursor = database.execute('''
                SELECT o.status,o.snapshot_id,o.start_time_ms,o.operation
                FROM operations o JOIN operation_groups g ON o.ogid=g.ogid
                WHERE g.instance_id=? AND g.plan_id=? AND g.repo_guid=?
                  AND g.original_instance_keyid='' AND o.status<>1
                ORDER BY o.start_time_ms DESC,o.id DESC
            ''', (server, plan['id'], repos[0]['guid']))
            backups=[]
            # Walk pages in chronological order, filtering operation types in
            # Python without a small SQL LIMIT before unrelated hook/index/dry
            # run records can be discarded.
            while len(backups)<2:
                rows=cursor.fetchmany(BACKREST_PAGE_SIZE)
                if not rows:
                    break
                for row in rows:
                    if _backrest_operation_kind(row[3]) != 100:
                        continue
                    backups.append(row)
                    if len(backups)>=2:
                        break
                if backups and backups[0][0]!=2:
                    break
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
        running = status == 2
        # A running backup does not invalidate the last completed checked copy.
        if running:
            completed = next((r for r in backups[1:] if r[0] in (3, 4, 5, 6, 7)), None)
            if completed is None:
                return 'unknown'
            if completed[0] in (4, 5, 6):
                return 'down'
            if completed[0] == 7:
                return 'degraded'
            _, snapshot, started, _ = completed
        receipt = json.loads(Path(job['receipt']).read_text(encoding='utf-8'))
        if not isinstance(receipt, dict):
            return 'unknown'
        receipt_status = receipt.get('status')
        if receipt_status not in ('success', 'failed', 'failure', 'error'):
            return 'unknown'
        if receipt_status in ('failed', 'failure', 'error'):
            return 'down'
        checked = receipt.get('checked')
        if checked is None:
            return 'unknown'
        if checked is False:
            return 'down'
        if checked is not True:
            return 'unknown'
        if (receipt.get('profile') != server or
                not isinstance(snapshot, str) or not snapshot or
                receipt.get('snapshot') != snapshot or not isinstance(receipt.get('utc'), str)):
            return 'unknown'
        check_mode = receipt.get('check_mode')
        if check_mode is None:
            return 'unknown'
        if not isinstance(check_mode, str) or not check_mode:
            return 'unknown'
        if check_mode != 'read-data':
            return 'down'
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
    if age > max_age:
        return 'down'
    return 'maintenance' if running else 'up'


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
    running = active in ('active', 'activating', 'deactivating', 'reloading')
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
        if not isinstance(receipt, dict):
            return 'unknown'
        receipt_status = receipt.get('status')
        if receipt_status not in ('success', 'failed', 'failure', 'error'):
            return 'unknown'
        if receipt_status in ('failed', 'failure', 'error'):
            return 'down'
        checked = receipt.get('checked')
        if checked is None:
            return 'unknown'
        if checked is False:
            return 'down'
        if checked is not True:
            return 'unknown'
        if (receipt.get('profile') != server or
                not isinstance(receipt.get('snapshot'), str) or not receipt['snapshot'].strip() or
                not isinstance(receipt.get('utc'), str)):
            return 'unknown'
        check_mode = receipt.get('check_mode')
        if check_mode is None:
            return 'unknown'
        if not isinstance(check_mode, str) or not check_mode:
            return 'unknown'
        if check_mode != 'read-data':
            return 'down'
        stamp = receipt['utc']
        completed = datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
        if completed.utcoffset() != timedelta(0):
            return 'unknown'
        age = time.time() - completed.timestamp()
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return 'unknown'
    if age < -300:
        return 'unknown'
    if age > max_age:
        return 'down'
    return 'maintenance' if running else 'up'


def receipt_metadata(job, server):
    """Return a bounded snapshot summary; never return paths or raw errors."""
    if not isinstance(job, dict):
        return None
    path=job.get('receipt')
    if not isinstance(path,str) or not path:
        return None
    try:
        receipt=json.loads(Path(path).read_text(encoding='utf-8'))
        if (not isinstance(receipt,dict) or receipt.get('status')!='success' or
                receipt.get('checked') is not True or receipt.get('profile')!=server or
                receipt.get('check_mode')!='read-data' or
                not isinstance(receipt.get('snapshot'),str) or
                not BACKUP_ID_RE.fullmatch(receipt['snapshot']) or
                not isinstance(receipt.get('utc'),str)):
            return None
        stamp=receipt['utc']
        finished=datetime.fromisoformat(stamp[:-1]+'+00:00' if stamp.endswith('Z') else stamp)
        if finished.utcoffset()!=timedelta(0):
            return None
        now=time.time();checked=finished.timestamp()
        if checked<0 or checked>now+300:
            return None
        result={'schema_version':1,'checked_at':checked,
                'snapshot_id':receipt['snapshot'],'check_mode':'read-data'}
        transport=transport_metadata(job.get('transport_receipt'),server,now)
        if transport is not None:
            result['transport']=transport
        return result
    except (OSError,UnicodeError,ValueError,TypeError,OverflowError):
        return None


def transport_metadata(path, server, now):
    """Normalize the local no-write transport-probe receipt."""
    if not isinstance(path,str) or not path:
        return None
    try:
        receipt=json.loads(Path(path).read_text(encoding='utf-8'))
        if (not isinstance(receipt,dict) or receipt.get('schema')!=1 or
                receipt.get('profile')!=server or
                receipt.get('status') not in ('up','down','unknown') or
                not isinstance(receipt.get('checked_utc'),str)):
            return None
        stamp=receipt['checked_utc']
        checked=datetime.fromisoformat(stamp[:-1]+'+00:00' if stamp.endswith('Z') else stamp)
        if checked.utcoffset()!=timedelta(0):
            return None
        timestamp=checked.timestamp()
        if timestamp<0 or timestamp>now+300:
            return None
        unauth=receipt.get('unauthenticated_status')
        config=receipt.get('authenticated_config_status')
        error=receipt.get('error_code')
        if unauth not in (None,401) or config not in (None,'pinned'):
            return None
        if error is not None and (not isinstance(error,str) or not TRANSPORT_ERROR_RE.fullmatch(error)):
            return None
        if receipt['status']=='up' and (unauth!=401 or config!='pinned' or error is not None):
            return None
        state=receipt['status']
        if now-timestamp>TRANSPORT_MAX_AGE:
            state='down'
            if error is None:
                error='stale'
        return {'checked_at':timestamp,'state':state,
                'unauthenticated_status':unauth,'config_status':config,
                'error_code':error}
    except (OSError,UnicodeError,ValueError,TypeError,OverflowError):
        return None


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
        observation={'state':state}
        if 'backup_job' in service:
            metadata=receipt_metadata(service['backup_job'],config.get('server'))
            if metadata is not None:
                observation['backup']=metadata
            elif state in ('up','maintenance'):
                state='unknown'
                observation['state']='unknown'
        result[service['id']]=observation
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
