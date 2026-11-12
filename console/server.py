"""Private operations read model. No SSH keys, Docker socket or control API."""
import concurrent.futures, datetime, gzip, hmac, json, math, os, re, sqlite3, threading, time
import urllib.request, urllib.error, smtplib, ssl, socket
from email.message import EmailMessage
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from service_schema import validate_registry
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('OPS_DATA', '/var/lib/home-console'))
DATA.mkdir(parents=True, exist_ok=True)
REGISTRY = json.loads(Path(os.environ.get('OPS_REGISTRY', str(ROOT / 'registry.json'))).read_text(encoding='utf-8-sig'))
try:
    validate_registry(REGISTRY)
except ValueError as error:
    raise SystemExit('invalid registry: ' + str(error)) from error
SERVICES = {s['id']: s for s in REGISTRY['services']}
SERVERS = {s['id']: s for s in REGISTRY['servers']}

def qualify_service_names(services, servers):
    """Disambiguate shared admin tools and cross-host duplicate service names."""
    names = {}
    for service in services.values():
        names.setdefault(service['name'].strip().casefold(), set()).add(service['server'])
    tools = {'1panel', 'clash', 'clash 代理后台', 'clash 代理内核', 'allinssl', '宝塔', '宝塔面板'}
    for service in services.values():
        name = service['name'].strip()
        key = name.casefold()
        host = servers.get(service['server'], {}).get('name', service['server'])
        if (key in tools or len(names[key]) > 1) and not name.endswith(' · ' + host):
            service['name'] = name + ' · ' + host

qualify_service_names(SERVICES, SERVERS)

def apply_private_entries(services, filename):
    """Keep private entry suffixes out of Git and expose them only behind auth."""
    if not filename: return
    entries = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    for sid, href in entries.items():
        if sid not in services or not isinstance(href, str):
            raise ValueError('invalid private entry')
        if urlsplit(href).scheme not in ('https', 'http', 'hometunnel'):
            raise ValueError('invalid private entry protocol')
        services[sid]['href'] = href

apply_private_entries(SERVICES, os.environ.get('OPS_ENTRIES'))
TOKENS = json.loads(Path(os.environ.get('OPS_TOKENS', str(ROOT/'private/tokens.json'))).read_text())
LOCK = threading.RLock()
DB = sqlite3.connect(str(DATA/'history.sqlite'), check_same_thread=False)
DB.execute('PRAGMA journal_mode=WAL')
DB.executescript('''
    CREATE TABLE IF NOT EXISTS spans(id INTEGER PRIMARY KEY,service TEXT,start REAL,end REAL,state TEXT,detail TEXT,ms REAL);
CREATE INDEX IF NOT EXISTS spans_lookup ON spans(service,start,end);
CREATE INDEX IF NOT EXISTS spans_latest ON spans(service,id DESC);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,service TEXT,time REAL,old TEXT,state TEXT,detail TEXT,source TEXT NOT NULL DEFAULT 'aggregate');
CREATE INDEX IF NOT EXISTS events_time ON events(time DESC,id DESC);
CREATE INDEX IF NOT EXISTS events_service_time ON events(service,time DESC,id DESC);
CREATE INDEX IF NOT EXISTS events_service_state_id ON events(service,state,id DESC);
CREATE TABLE IF NOT EXISTS hosts(id TEXT PRIMARY KEY,time REAL,metrics TEXT);
CREATE TABLE IF NOT EXISTS notices(event INTEGER PRIMARY KEY,status TEXT,attempts INTEGER,last_attempt REAL,error_kind TEXT);
CREATE TABLE IF NOT EXISTS host_health(id TEXT PRIMARY KEY,state TEXT);
CREATE TABLE IF NOT EXISTS samples(service TEXT,time REAL,ms REAL);
CREATE INDEX IF NOT EXISTS samples_lookup ON samples(service,time);
CREATE TABLE IF NOT EXISTS probe_attempts(id INTEGER PRIMARY KEY,service TEXT,time REAL,attempt INTEGER,state TEXT,kind TEXT,ms REAL);
CREATE INDEX IF NOT EXISTS probe_attempts_time ON probe_attempts(time);
CREATE TABLE IF NOT EXISTS probe_alert_state(service TEXT PRIMARY KEY,failures INTEGER,confirmed INTEGER,down_event INTEGER,last_cycle REAL);
CREATE TABLE IF NOT EXISTS probe_notifiable(event INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS service_channels(
        service TEXT NOT NULL,channel TEXT NOT NULL,observed_at REAL NOT NULL,
        state TEXT NOT NULL,detail TEXT,ms REAL,
        PRIMARY KEY(service,channel));
CREATE TABLE IF NOT EXISTS backup_metadata(
    service TEXT NOT NULL,host TEXT NOT NULL,observed_at REAL NOT NULL,
    schema_version INTEGER,metadata_state TEXT NOT NULL
        CHECK(metadata_state IN ('valid','missing','invalid')),
    last_valid_at REAL,checked_at REAL,snapshot_id TEXT,check_mode TEXT,metadata_json TEXT,
    PRIMARY KEY(service,host));
CREATE INDEX IF NOT EXISTS backup_metadata_seen ON backup_metadata(observed_at);
''')
EVENT_COLUMNS = {row[1] for row in DB.execute('PRAGMA table_info(events)').fetchall()}
if 'source' not in EVENT_COLUMNS:
    DB.execute("ALTER TABLE events ADD COLUMN source TEXT NOT NULL DEFAULT 'aggregate'")
NOTICE_COLUMNS = {row[1] for row in DB.execute('PRAGMA table_info(notices)').fetchall()}
if 'error_kind' not in NOTICE_COLUMNS:
    DB.execute('ALTER TABLE notices ADD COLUMN error_kind TEXT')
STATES = {'up','down','degraded','unknown','maintenance'}
SNAPSHOT_CACHE = {}
SNAPSHOT_CACHE_TTL = 5
SNAPSHOT_LOCK = threading.Lock()
HOUSEHOLD_LOCK = threading.Lock()
HOUSEHOLD_CACHE = None
BACKUP_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
BACKUP_ERROR_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
BACKUP_CLOCK_SKEW = 300
TRANSPORT_MAX_AGE = 900
CHANNEL_MAX_AGE = 150

def record(sid, state, detail='', ms=None, now=None, source='aggregate'):
    now = time.time() if now is None else now
    if sid not in SERVICES or state not in STATES: raise ValueError('invalid observation')
    detail = str(detail)[:160]
    with LOCK, DB:
        event_id = None
        if ms is not None: DB.execute('INSERT INTO samples VALUES(?,?,?)',(sid,now,ms))
        prev = DB.execute('SELECT id,start,end,state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()
        had_previous = prev is not None
        if prev:
            pid, start, end, old = prev
            if now < end: return None
            if now-end > 150:
                edge = end+90
                DB.execute('UPDATE spans SET end=? WHERE id=?',(edge,pid))
                DB.execute('INSERT INTO spans(service,start,end,state,detail) VALUES(?,?,?,?,?)',(sid,edge,now,'unknown','采集间断'))
                prev = None
            elif old == state:
                DB.execute('UPDATE spans SET end=?,detail=?,ms=? WHERE id=?',(now,detail,ms,pid))
                return None
            else:
                DB.execute('UPDATE spans SET end=? WHERE id=?',(now,pid))
            if old != state:
                event_id = DB.execute('INSERT INTO events(service,time,old,state,detail,source) VALUES(?,?,?,?,?,?)',
                                      (sid,now,old,state,detail,source)).lastrowid
        DB.execute('INSERT INTO spans(service,start,end,state,detail,ms) VALUES(?,?,?,?,?,?)',(sid,now,now,state,detail,ms))
        if not had_previous and state in ('down','degraded'):
            event_id = DB.execute('INSERT INTO events(service,time,old,state,detail,source) VALUES(?,?,?,?,?,?)',
                                  (sid,now,'unknown',state,detail,source)).lastrowid
        return event_id

def service_channels(service):
    """Return the required independent observation channels for one service."""
    channels=[]
    if isinstance(service.get('probe'),dict):
        channels.append('probe')
    if (bool(service.get('containers')) or bool(service.get('units')) or
            bool(service.get('timer')) or bool(service.get('process')) or
            isinstance(service.get('backup_job'),dict)):
        channels.append('host')
    job=service.get('backup_job')
    if isinstance(job,dict) and isinstance(job.get('transport_receipt'),str):
        channels.append('transport')
    return tuple(channels)

def _channel_detail(channel,state,detail):
    if detail:
        return detail
    name='入口探测' if channel=='probe' else '宿主采集'
    return name+('正常' if state=='up' else '检查异常' if state in ('down','degraded') else '未确认')

def reconcile_service_channels(service,now=None,forced_channels=None):
    """Persist one aggregate edge from current per-channel observations.

    Any fresh down/degraded channel wins over another healthy channel. Missing
    or stale evidence yields unknown unless another channel already proves a
    fault. This prevents an HTTP probe from masking a host or backup failure.
    """
    now=time.time() if now is None else now
    channels=forced_channels or service_channels(service)
    if not channels:
        return None,None
    sid=service['id']
    with LOCK:
        rows=DB.execute(
            'SELECT channel,observed_at,state,detail,ms FROM service_channels '
            'WHERE service=? AND channel IN ('+','.join('?' for _ in channels)+')',
            (sid,*channels)).fetchall()
    by_channel={row[0]:row for row in rows}
    observations=[]
    for channel in channels:
        row=by_channel.get(channel)
        if not row or now-row[1]>CHANNEL_MAX_AGE:
            observations.append((channel,'unknown',_channel_detail(channel,'unknown',None),None))
        else:
            observations.append((channel,row[2],row[3] or '',row[4]))
    priority={'down':5,'degraded':4,'unknown':3,'maintenance':2,'up':1}
    strongest=max(priority[item[1]] for item in observations)
    candidates=[item for item in observations if priority[item[1]]==strongest]
    channel,state,detail,ms=candidates[0]
    source=channel if len(candidates)==1 else 'aggregate'
    event_id=record(sid,state,detail,ms,now,source=source)
    return state,event_id

def update_service_channel(sid,channel,state,detail='',ms=None,now=None,service=None):
    """Store one fresh channel observation and record only aggregate edges."""
    now=time.time() if now is None else now
    if sid not in SERVICES or channel not in ('probe','host','transport') or state not in STATES:
        raise ValueError('invalid channel observation')
    service=service or SERVICES[sid]
    channels=service_channels(service)
    if channel not in channels:
        channels=(channel,)
    detail=str(detail)[:160]
    with LOCK,DB:
        DB.execute(
            'INSERT OR REPLACE INTO service_channels(service,channel,observed_at,state,detail,ms) '
            'VALUES(?,?,?,?,?,?)',(sid,channel,now,state,detail,ms))
    return reconcile_service_channels(service,now,channels)

def refresh_channel_aggregates(now=None):
    """Advance aggregate state when a channel ages out without new input."""
    now=time.time() if now is None else now
    for service in SERVICES.values():
        if service_channels(service):
            reconcile_service_channels(service,now)
    refresh_backup_transport_channels(now)

def refresh_backup_transport_channels(now):
    """Turn stale or invalid transport evidence into a durable state edge.

    The collector heartbeat may stop while the REST tunnel receipt ages out.
    Deriving the transport state only while rendering a snapshot would show
    the fault but would not create the matching event or notification.
    """
    for service in SERVICES.values():
        job=service.get('backup_job')
        if not isinstance(job,dict) or not isinstance(job.get('transport_receipt'),str):
            continue
        state,detail=transport_channel_observation(
            backup_metadata_for(service,now))
        update_service_channel(
            service['id'],'transport',state,detail,now=now,service=service)

def transport_channel_observation(metadata):
    """Return the independent aggregate state of a configured transport."""
    transport=(metadata or {}).get('transport')
    if metadata.get('metadata_state')!='valid':
        return 'unknown','上传通路证据缺少有效备份回执'
    if transport is None:
        return 'unknown','上传通路回执缺失'
    if metadata.get('transport_stale'):
        return 'down','上传通路回执已过期'
    state=transport.get('state')
    if state=='up':
        return 'up','上传通路正常'
    if state=='down':
        return 'down','上传通路断开'
    return 'unknown','上传通路证据无效'

def history(sid, start, end):
    with LOCK:
        rows=DB.execute('SELECT start,end,state FROM spans WHERE service=? AND end>=? AND start<=? ORDER BY start',(sid,start,end)).fetchall()
        latest=DB.execute('SELECT start,end,state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()
    segments=[]
    for a,b,state in rows:
        if latest and a==latest[0]: b=min(end,b+90)
        a,b=max(start,a),min(end,b)
        if b>a: segments.append((a,b,state))
    return segments

def statistics(segments,start,end):
    durations={s:0 for s in STATES}
    for a,b,state in segments: durations[state]+=b-a
    # A scheduled backup that is currently exporting is still being observed.
    # Maintenance counts toward coverage, but never toward availability or
    # incident duration.
    availability_known=durations['up']+durations['down']+durations['degraded']
    observed=durations['up']+durations['down']+durations['degraded']+durations['maintenance']
    return {'availability':round(100*durations['up']/availability_known,3) if availability_known else None,
            'coverage':round(100*observed/max(end-start,1),2),'known_seconds':round(observed),
            'down_seconds':round(durations['down']+durations['degraded']),
            'maintenance_seconds':round(durations['maintenance'])}

def event_page(window='24h',page=1,size=15,service='',before=None):
    if window not in ('24h','7d','30d','all') or page<1 or not 1<=size<=100:raise ValueError('Invalid pagination')
    allowed=list(SERVICES)+list(SERVERS)
    if service and service not in allowed:raise ValueError('Unknown resource')
    end=before if before is not None else time.time()
    seconds={'24h':86400,'7d':604800,'30d':2592000}.get(window)
    any_previous=("(SELECT p.state FROM events p WHERE p.service=e.service "
                  "AND p.id<e.id AND p.state IN ('down','degraded') "
                  "ORDER BY p.id DESC LIMIT 1)")
    where=['e.service IN ('+','.join('?' for _ in allowed)+')','e.time<=?',
           "(e.state IN ('down','degraded') OR "
           "(e.state='up' AND "+any_previous+" IS NOT NULL))"]
    args=allowed+[end]
    if seconds:where.append('e.time>=?');args.append(end-seconds)
    if service:where.append('e.service=?');args.append(service)
    clause=' AND '.join(where)
    with LOCK:
        total=DB.execute('SELECT COUNT(*) FROM events e WHERE '+clause,args).fetchone()[0]
        pages=max(1,math.ceil(total/size));page=min(page,pages)
        rows=DB.execute('SELECT e.id,e.service,e.time,e.old,e.state,e.detail FROM events e WHERE '+
                        clause+' ORDER BY e.time DESC,e.id DESC LIMIT ? OFFSET ?',
                        args+[size,(page-1)*size]).fetchall()
    return {'items':[dict(zip(['id','service','time','old','state','detail'],r)) for r in rows],'total':total,'page':page,'pages':pages,'size':size,'before':end}

def snapshot(window):
    if window not in ('24h','7d','30d','all'): raise ValueError('Invalid window')
    # Authentication is still checked per HTTP request, before this shared read model.
    with SNAPSHOT_LOCK:
        with LOCK: revision=DB.total_changes
        cached=SNAPSHOT_CACHE.get(window)
        if cached and time.monotonic()-cached[0] < SNAPSHOT_CACHE_TTL and cached[1]==revision:
            return cached[2]
        payload=build_snapshot(window)
        SNAPSHOT_CACHE[window]=(time.monotonic(),revision,payload)
        return payload

def public_service(service):
    """Return only fields needed by the authenticated browser read model."""
    keys = ('id', 'server', 'name', 'category', 'app', 'href',
            'containers', 'units', 'process', 'description')
    return {key: service[key] for key in keys if key in service}

def validate_backup_metadata(value, now):
    """Return a bounded, normalized backup observation or raise ValueError."""
    base={'schema_version','checked_at','snapshot_id','check_mode'}
    if not isinstance(value,dict) or set(value) not in (base,base|{'transport'}):
        raise ValueError('invalid backup metadata')
    version=value.get('schema_version')
    checked_at=value.get('checked_at')
    snapshot=value.get('snapshot_id')
    mode=value.get('check_mode')
    if isinstance(version,bool) or version!=1:
        raise ValueError('invalid backup schema')
    if (isinstance(checked_at,bool) or not isinstance(checked_at,(int,float)) or
            not math.isfinite(checked_at) or checked_at<0 or
            checked_at>now+BACKUP_CLOCK_SKEW):
        raise ValueError('invalid backup time')
    if not isinstance(snapshot,str) or not BACKUP_ID_RE.fullmatch(snapshot):
        raise ValueError('invalid snapshot id')
    if mode!='read-data':
        raise ValueError('invalid backup check mode')
    clean={'schema_version':1,'checked_at':float(checked_at),
           'snapshot_id':snapshot,'check_mode':mode}
    if 'transport' in value:
        transport=value['transport']
        keys={'checked_at','state','unauthenticated_status','config_status','error_code'}
        if not isinstance(transport,dict) or set(transport)!=keys:
            raise ValueError('invalid transport metadata')
        stamp=transport.get('checked_at')
        state=transport.get('state')
        unauth=transport.get('unauthenticated_status')
        config=transport.get('config_status')
        error=transport.get('error_code')
        if (isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or
                not math.isfinite(stamp) or stamp<0 or
                stamp>now+BACKUP_CLOCK_SKEW):
            raise ValueError('invalid transport time')
        if state not in ('up','down','unknown'):
            raise ValueError('invalid transport state')
        if unauth not in (None,401) or config not in (None,'pinned'):
            raise ValueError('invalid transport response')
        if state=='up' and (unauth!=401 or config!='pinned' or error is not None):
            raise ValueError('incomplete healthy transport')
        if error is not None and (not isinstance(error,str) or not BACKUP_ERROR_RE.fullmatch(error)):
            raise ValueError('invalid transport error')
        clean['transport']={'checked_at':float(stamp),'state':state,
                            'unauthenticated_status':unauth,'config_status':config,
                            'error_code':error}
    return clean

def backup_max_age(service):
    """Return the registry-bounded backup evidence lifetime."""
    max_age=(service.get('backup_job') or {}).get('max_age_seconds',93600)
    if (isinstance(max_age,bool) or not isinstance(max_age,(int,float)) or
            not math.isfinite(max_age) or not 60<=max_age<=604800):
        return 93600
    return float(max_age)

def _newer_transport(current, candidate):
    """Keep the newest transport receipt, preferring a failure on ties."""
    if current is None:
        return candidate
    if candidate is None:
        return current
    current_at=current.get('checked_at')
    candidate_at=candidate.get('checked_at')
    if not isinstance(current_at,(int,float)) or isinstance(current_at,bool):
        return candidate
    if not isinstance(candidate_at,(int,float)) or isinstance(candidate_at,bool):
        return current
    if candidate_at!=current_at:
        return candidate if candidate_at>current_at else current
    severity={'up':0,'unknown':1,'down':2}
    return candidate if severity.get(candidate.get('state'),1)>=severity.get(current.get('state'),1) else current

def _merge_transport(clean, previous_json, previous_checked_at, previous_snapshot):
    """Prevent an older transport receipt from regressing newer safe evidence."""
    try:
        previous=json.loads(previous_json) if previous_json else None
    except (TypeError,ValueError):
        previous=None
    if not isinstance(previous,dict):
        return clean
    previous_transport=previous.get('transport')
    if not isinstance(previous_transport,dict):
        return clean
    if (clean['checked_at'],clean['snapshot_id'])!=(previous_checked_at,previous_snapshot):
        return clean
    current_transport=clean.get('transport')
    merged=_newer_transport(current_transport,previous_transport)
    if merged is not None:
        clean['transport']=merged
    return clean

def update_backup_metadata(sid, host, value, now):
    """Persist only the newest safe metadata and retain the last valid display."""
    with LOCK:
        previous=DB.execute(
            'SELECT last_valid_at,checked_at,snapshot_id,check_mode,metadata_json '
            'FROM backup_metadata WHERE service=? AND host=?',(sid,host)).fetchone()
        if 'backup' in value:
            try:
                clean=validate_backup_metadata(value.get('backup'),now)
            except ValueError:
                state='invalid'
            else:
                old=previous or (None,None,None,None,None)
                older=(old[1] is not None and
                       (clean['checked_at']<old[1] or
                        (clean['checked_at']==old[1] and clean['snapshot_id']!=old[2])))
                if older:
                    state='invalid'
                else:
                    clean=_merge_transport(clean,old[4],old[1],old[2])
                    serialized=json.dumps(clean,separators=(',',':'),sort_keys=True)
                    with DB:
                        DB.execute('INSERT OR REPLACE INTO backup_metadata '
                                   '(service,host,observed_at,schema_version,metadata_state,'
                                   'last_valid_at,checked_at,snapshot_id,check_mode,metadata_json) '
                                   'VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   (sid,host,now,1,'valid',now,clean['checked_at'],
                                    clean['snapshot_id'],clean['check_mode'],serialized))
                    return 'valid'
        else:
            state='missing'
        old=previous or (None,None,None,None,None)
        with DB:
            DB.execute('INSERT OR REPLACE INTO backup_metadata '
                       '(service,host,observed_at,schema_version,metadata_state,'
                       'last_valid_at,checked_at,snapshot_id,check_mode,metadata_json) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (sid,host,now,None,state,old[0],old[1],old[2],old[3],old[4]))
        return state

def backup_metadata_for(service, now):
    job=service.get('backup_job')
    if not isinstance(job,dict):
        return None
    max_age=backup_max_age(service)
    with LOCK:
        row=DB.execute(
            'SELECT observed_at,schema_version,metadata_state,last_valid_at,checked_at,'
            'snapshot_id,check_mode,metadata_json FROM backup_metadata '
            'WHERE service=? AND host=?',(service['id'],service['server'])).fetchone()
    scheduler=job.get('scheduler','systemd')
    result={'scheduler':scheduler if scheduler in ('systemd','backrest','launchd') else 'unknown',
            'metadata_state':'missing','observed_at':None,'last_valid_at':None,
            'checked_at':None,'snapshot_id':None,'check_mode':None,
            'transport':None,'transport_configured':isinstance(job.get('transport_receipt'),str),
            'transport_stale':False,'fresh':False}
    if not row:
        return result
    observed,schema,metadata_state,last_valid,checked,snapshot,mode,raw=row
    result.update({'metadata_state':metadata_state,'observed_at':observed,
                   'last_valid_at':last_valid,'checked_at':checked,
                   'snapshot_id':snapshot,'check_mode':mode})
    if raw:
        try:
            payload=json.loads(raw)
            transport=payload.get('transport')
            if isinstance(transport,dict):
                transport=dict(transport)
                stamp=transport.get('checked_at')
                stale=(not isinstance(stamp,(int,float)) or isinstance(stamp,bool) or
                       now-stamp>TRANSPORT_MAX_AGE or now-stamp<-BACKUP_CLOCK_SKEW)
                result['transport_stale']=stale
                if stale:
                    transport['state']='down'
                    if transport.get('error_code') is None:
                        transport['error_code']='stale'
                result['transport']=transport
        except (TypeError,ValueError):
            result['metadata_state']='invalid'
    result['fresh']=bool(checked is not None and now-checked<=max_age and
                         now-checked>=-BACKUP_CLOCK_SKEW)
    return result

def previous_notifiable_state(sid,event_id,source=None,current_state=None):
    """Find the last state that can itself produce a notification."""
    with LOCK:
        if current_state=='up':
            row=DB.execute(
                "SELECT state FROM events WHERE service=? AND id<? "
                "AND state IN ('down','degraded') ORDER BY id DESC LIMIT 1",
                (sid,event_id)).fetchone()
        else:
            row=DB.execute(
                "SELECT state FROM events WHERE service=? AND id<? AND source=? "
                "AND state IN ('up','down','degraded') ORDER BY id DESC LIMIT 1",
                (sid,event_id,source or 'aggregate')).fetchone()
    return row[0] if row else None


def build_snapshot(window):
    now=time.time()
    seconds={'24h':86400,'7d':604800,'30d':2592000}.get(window)
    with LOCK:
        first=DB.execute('SELECT MIN(start) FROM spans').fetchone()[0]
        hosts={row[0]:{'last_seen':row[1],'metrics':json.loads(row[2])} for row in DB.execute('SELECT id,time,metrics FROM hosts')}
        events=[dict(zip(['id','service','time','old','state','detail'],row)) for row in DB.execute('SELECT id,service,time,old,state,detail FROM events ORDER BY id DESC LIMIT 200')]
    start=now-seconds if seconds else (first or now)
    result=[]
    for service in SERVICES.values():
        sid=service['id']
        with LOCK: row=DB.execute('SELECT end,state,detail,ms FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()
        state=row[1] if row and now-row[0]<=150 else 'unknown'
        detail=row[2] if state!='unknown' else ('采集已过期' if row else '尚未收到监测数据')
        with LOCK:
            began=DB.execute('SELECT MIN(start) FROM spans WHERE service=?',(sid,)).fetchone()[0]
            latency=[]
        observed_start=max(start,began) if began is not None else now
        segments=history(sid,start,now); stats=statistics(segments,start,now)
        bars=[]; count=48 if window=='24h' else 30 if window in ('30d','all') else 28
        step=max((now-start)/count,1)
        for i in range(count):
            a,b=start+i*step,min(now,start+(i+1)*step)
            clipped=[(max(a,x),min(b,y),s) for x,y,s in segments if y>a and x<b]
            sub=statistics(clipped,a,b)
            observed={s for x,y,s in clipped}
            color='down' if 'down' in observed else 'degraded' if 'degraded' in observed else 'maintenance' if 'maintenance' in observed else 'up' if sub['coverage']>=99 else 'unknown'
            bars.append({'start':a,'end':b,'state':color,**sub})
        is_backup=isinstance(service.get('backup_job'),dict)
        item={**public_service(service),'is_backup':is_backup,'is_probe':bool(service.get('probe')),
              'state':state,'detail':detail,'checked_at':row[0] if row else None,
              'ms':row[3] if row else None,'monitoring_since':began,'latency':latency,
              **stats,'bars':bars}
        if is_backup:
            item['backup']=backup_metadata_for(service,now)
        result.append(item)
    rates=[s['availability'] for s in result if s['availability'] is not None]
    servers=[]
    for host in SERVERS.values():
        obs=hosts.get(host['id']); fresh=obs and now-obs['last_seen']<=150
        servers.append({**host,'state':'up' if fresh else 'unknown','monitoring_issue':bool(obs and not fresh),'last_seen':obs['last_seen'] if obs else None,'metrics':obs['metrics'] if fresh else {}})
    events=[e for e in events if e['service'] in SERVICES or e['service'] in SERVERS]
    backups=[s for s in result if s['is_backup']]
    backup_success=[s['backup']['checked_at'] for s in backups
                    if s.get('backup',{}).get('checked_at') is not None]
    transports=[((s.get('backup') or {}).get('transport') or {}).get('state')
                for s in backups]
    payload={'generated_at':now,'window_start':start,'servers':servers,'services':result,'events':events,'documents':REGISTRY.get('documents',[]),'downloads':REGISTRY.get('downloads',[]),
            'summary':{'servers':len(servers),'services':len(result),'apps':sum(s['app'] for s in result),
              'abnormal':sum(s['state'] in ('down','degraded') for s in result),
              'unknown':sum(s['state']=='unknown' for s in result),
              'abnormal_servers':sum(h['monitoring_issue'] for h in servers),
              'availability':round(sum(rates)/len(rates),3) if rates else None,
              'availability_services':len(rates),
              'coverage':round(sum(s['coverage'] for s in result)/max(len(result),1),2)},
            'backup_summary':{
              'registered':len(backups),
              'up':sum(s['state']=='up' and
                       ((s.get('backup') or {}).get('transport') or {}).get('state')!='down'
                       for s in backups),
              'running':sum(s['state']=='maintenance' for s in backups),
              'abnormal':sum(s['state'] in ('down','degraded') or
                             ((s.get('backup') or {}).get('transport') or {}).get('state')=='down'
                             for s in backups),
              'unknown':sum(s['state']=='unknown' for s in backups),
              'metadata_valid':sum(s.get('backup',{}).get('metadata_state')=='valid'
                                   for s in backups),
              'metadata_missing':sum(s.get('backup',{}).get('metadata_state')=='missing'
                                     for s in backups),
              'metadata_invalid':sum(s.get('backup',{}).get('metadata_state')=='invalid'
                                     for s in backups),
              'transport_up':sum(state=='up' for state in transports),
              'transport_down':sum(state=='down' for state in transports),
              'transport_unknown':sum(state=='unknown' for state in transports),
              'transport_missing':sum(state is None for state in transports),
              'latest_success_at':max(backup_success) if backup_success else None}}
    return payload

def household_snapshot():
    global HOUSEHOLD_CACHE
    with HOUSEHOLD_LOCK:
        if HOUSEHOLD_CACHE and time.monotonic()-HOUSEHOLD_CACHE[0]<10:
            return HOUSEHOLD_CACHE[1]
        def read(key):
            try:
                with urllib.request.urlopen(os.environ.get('OPS_HOUSEHOLD_URL','http://127.0.0.1:18181')+'/api/'+key,timeout=2) as response:
                    value=json.loads(response.read(8192))
                    return value if isinstance(value,dict) else {}
            except Exception:return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(read,('ups','ddns')))
        payload={'generated_at':time.time(),'household':dict(zip(('ups','ddns'),values))}
        HOUSEHOLD_CACHE=(time.monotonic(),payload)
        return payload

def service_latency(service):
    if service not in SERVICES: raise ValueError('Unknown resource')
    now=time.time()
    with LOCK:
        rows=DB.execute('SELECT CAST(time/300 AS INTEGER)*300,AVG(ms) FROM samples WHERE service=? AND time>=? GROUP BY CAST(time/300 AS INTEGER) ORDER BY time LIMIT 400',(service,now-86400)).fetchall()
    return [[bucket, round(ms,2)] for bucket,ms in rows]

def notify_pending():
    filename=os.environ.get('OPS_NOTIFY')
    if not filename:return
    try:config=json.loads(Path(filename).read_text())
    except (OSError,ValueError):return
    if not isinstance(config,dict) or config.get('enabled') is False:return
    with LOCK:
        rows=DB.execute("SELECT e.id,e.service,e.time,e.old,e.state,e.detail,coalesce(n.attempts,0),e.source FROM events e LEFT JOIN notices n ON n.event=e.id WHERE (n.status IS NULL OR n.status NOT IN ('sent','skipped')) AND coalesce(n.attempts,0)<5 AND coalesce(n.last_attempt,0)<? ORDER BY e.id LIMIT 10",(time.time()-300,)).fetchall()
    for eid,sid,when,old,state,detail,attempts,source in rows:
        if source=='probe':
            with LOCK:
                eligible=DB.execute('SELECT 1 FROM probe_notifiable WHERE event=?',(eid,)).fetchone()
            if not eligible:
                # Raw transitions remain visible, but do not page on a single failed check.
                continue
            # The confirmed outage and its later recovery are the only events
            # admitted to probe_notifiable. Do not apply the generic
            # neutral-state dedupe, because the two preceding degraded rounds
            # must not suppress the confirmed down transition.
            notifiable=state in ('down','up')
        else:
            previous=previous_notifiable_state(
                sid,eid,source=source,current_state=state)
            # Neutral transitions do not end an incident. A later down/degraded
            # must reuse the original alert instead of paging again until the
            # service has actually recovered.
            notifiable=(
                state in ('down','degraded') and previous not in ('down','degraded')
            ) or (
                state=='up' and previous in ('down','degraded')
            )
        if sid not in SERVICES and sid not in SERVERS or not notifiable:
            with LOCK,DB:
                DB.execute('INSERT OR REPLACE INTO notices(event,status,attempts,last_attempt,error_kind) '
                           'VALUES(?,?,?,?,NULL)',(eid,'skipped',attempts,time.time()))
            continue
        item=SERVICES.get(sid) or SERVERS.get(sid) or {'name':sid}
        display_name=item.get('name') or sid
        if not all(isinstance(config.get(k),str) and config[k] for k in ('sender','recipient','address','username','password')):
            continue
        message=EmailMessage();message['From']=config['sender'];message['To']=config['recipient']
        message['Subject']='[NestOps] '+('恢复' if state=='up' else '异常')+' · '+display_name
        message.set_content(display_name+'\n'+detail+'\n时间：'+datetime.datetime.fromtimestamp(when,datetime.timezone.utc).isoformat()+'\n查看：'+os.environ.get('OPS_PUBLIC_URL','https://dashboard.example.com')+'\n事件编号：'+str(eid))
        status='failed';error_kind=None
        try:
            address=urlsplit(config['address']); port=address.port or 587
            if port==465:
                smtp=smtplib.SMTP_SSL(address.hostname,port,timeout=12,context=ssl.create_default_context())
            else:
                smtp=smtplib.SMTP(address.hostname,port,timeout=12);smtp.starttls(context=ssl.create_default_context())
            with smtp:
                smtp.login(config['username'],config['password'])
                refused=smtp.send_message(message)
                if not refused:status='sent'
                else:error_kind='send'
        except Exception as error:
            error_kind=smtp_error_kind(error)
        with LOCK,DB:
            DB.execute('INSERT OR REPLACE INTO notices(event,status,attempts,last_attempt,error_kind) VALUES(?,?,?,?,?)',
                       (eid,status,attempts+1,time.time(),error_kind))

def smtp_error_kind(error):
    """Return a stable public category without persisting SMTP error text."""
    if isinstance(error, socket.gaierror):
        return 'dns'
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'timeout'
    if isinstance(error, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
        return 'tls'
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'auth'
    if isinstance(error, (ConnectionError, smtplib.SMTPConnectError)):
        return 'connection'
    return 'send'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def probe_error(error):
    reason=error.reason if isinstance(error,urllib.error.URLError) else error
    if isinstance(reason,ssl.SSLCertVerificationError):return 'tls_certificate'
    if isinstance(reason,ssl.SSLError):return 'tls_handshake'
    if isinstance(reason,socket.gaierror):return 'dns'
    if isinstance(reason,(TimeoutError,socket.timeout)):return 'timeout'
    if isinstance(reason,ConnectionRefusedError):return 'connection_refused'
    if isinstance(reason,ConnectionResetError):return 'connection_reset'
    return 'connection_error'

def probe_cycle(service,success,detail='',ms=None,now=None):
    """Persist one aggregate cycle and gate notifications behind three failures."""
    sid=service['id'];now=time.time() if now is None else now
    with LOCK:
        previous=DB.execute('SELECT failures,confirmed,down_event,last_cycle FROM probe_alert_state WHERE service=?',(sid,)).fetchone()
        failures,confirmed,down_event,last=previous or (0,0,None,now)
        if not confirmed and now-last>150:failures=0;down_event=None
        if success:
            was_confirmed=bool(confirmed)
            failures=0;confirmed=0;down_event=None
            state='up'
        else:
            was_confirmed=bool(confirmed)
            failures+=1
            confirmed=1 if failures>=3 else 0
            state='down' if confirmed else 'degraded'
        aggregate_state,event_id=update_service_channel(
            sid,'probe',state,detail,ms,now,service=service)
        if event_id is not None:
            if state=='down' and confirmed and not was_confirmed:
                down_event=event_id
                DB.execute('INSERT OR IGNORE INTO probe_notifiable VALUES(?)',(event_id,))
            elif state=='up' and was_confirmed:
                DB.execute('INSERT OR IGNORE INTO probe_notifiable VALUES(?)',(event_id,))
        with DB:
            DB.execute('INSERT OR REPLACE INTO probe_alert_state VALUES(?,?,?,?,?)',
                       (sid,failures,confirmed,down_event,now))
            # Suppress only raw probe transitions. Aggregate edges may belong to
            # a mixed probe/host service and must preserve its recovery notice.
            DB.execute("INSERT OR IGNORE INTO notices(event,status,attempts,last_attempt,error_kind) "
                       "SELECT e.id,'skipped',0,?,NULL FROM events e WHERE e.service=? "
                       "AND e.source='probe' "
                       "AND e.id NOT IN (SELECT event FROM probe_notifiable)",(now,sid))
            if confirmed and down_event is not None:
                DB.execute("DELETE FROM notices WHERE event=? AND status='skipped'",(down_event,))
    return aggregate_state

def probe(service):
    p=service.get('probe')
    if not p: return
    success=False;detail='入口检查失败 · connection_error';elapsed=0
    for attempt in range(1,3):
        started=time.monotonic();kind='http'
        try:
            request=urllib.request.Request(p['url'],headers={'User-Agent':'HomeConsole/1.0'})
            try:
                with urllib.request.build_opener(NoRedirect).open(request,timeout=6) as response:code=response.status
            except urllib.error.HTTPError as error:code=error.code
            success=code in p.get('codes',[200]);detail=f'HTTP {code}'
        except Exception as error:
            kind=probe_error(error);detail='入口检查失败 · '+kind
        elapsed=round((time.monotonic()-started)*1000);now=time.time()
        # No URL, exception text, credentials, headers or private path is persisted.
        with LOCK,DB:DB.execute('INSERT INTO probe_attempts(service,time,attempt,state,kind,ms) VALUES(?,?,?,?,?,?)',(service['id'],now,attempt,'up' if success else 'down',kind,elapsed))
        if success:break
        if attempt<2:time.sleep(1)
    probe_cycle(service,success,detail+f' · attempt {attempt}/2 · {elapsed}ms',elapsed)

def loop():
    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: list(pool.map(probe,SERVICES.values()))
        with LOCK, DB:
            for host,seen in DB.execute('SELECT id,time FROM hosts').fetchall():
                state='up' if time.time()-seen<=150 else 'down'
                previous=DB.execute('SELECT state FROM host_health WHERE id=?',(host,)).fetchone()
                if previous and previous[0]!=state:
                    DB.execute('INSERT INTO events(service,time,old,state,detail) VALUES(?,?,?,?,?)',(host,time.time(),previous[0],state,'服务器采集恢复' if state=='up' else '服务器采集失联；不等同确认整机停机'))
                DB.execute('INSERT OR REPLACE INTO host_health VALUES(?,?)',(host,state))
            DB.execute('DELETE FROM spans WHERE end<?',(time.time()-366*86400,))
            DB.execute('DELETE FROM events WHERE time<?',(time.time()-366*86400,))
            DB.execute('DELETE FROM probe_notifiable WHERE event NOT IN (SELECT id FROM events)')
            DB.execute('DELETE FROM samples WHERE time<?',(time.time()-31*86400,))
            DB.execute('DELETE FROM probe_attempts WHERE time<?',(time.time()-31*86400,))
        refresh_channel_aggregates()
        notify_pending()
        time.sleep(60)

def validate_ingest_payload(payload,host,now):
    """Validate the complete collector payload before any database write."""
    if not isinstance(payload,dict):
        raise ValueError('payload must be an object')
    if set(payload)-{'services','metrics'}:
        raise ValueError('unsupported payload fields')
    if 'services' not in payload or not isinstance(payload['services'],dict):
        raise ValueError('services must be an object')
    if 'metrics' in payload and not isinstance(payload['metrics'],dict):
        raise ValueError('metrics must be an object')
    clean_metrics={}
    for key,value in payload.get('metrics',{}).items():
        if key in ('memory_percent','disk_percent','load1','uptime_seconds') and \
                isinstance(value,(int,float)) and not isinstance(value,bool) and \
                math.isfinite(value) and 0<=value<=1e10:
            clean_metrics[key]=value
    prepared=[]
    for sid,value in payload['services'].items():
        service=SERVICES.get(sid)
        if not service or service.get('server')!=host:
            raise ValueError('unknown or cross-host service')
        if service.get('probe') and 'host' not in service_channels(service):
            raise ValueError('probe-only services are center-owned')
        if not isinstance(value,dict) or not isinstance(value.get('state'),str) or \
                value['state'] not in STATES:
            raise ValueError('invalid service observation')
        is_backup=isinstance(service.get('backup_job'),dict)
        if not is_backup and value['state']=='maintenance':
            raise ValueError('maintenance is reserved for backup observations')
        expected={'state','backup'} if is_backup else {'state'}
        if set(value)-expected:
            raise ValueError('unsupported service observation fields')
        metadata_state='missing'
        metadata=None
        if 'backup' in value:
            try:
                metadata=validate_backup_metadata(value['backup'],now)
            except ValueError:
                metadata_state='invalid'
                metadata=value['backup']
            else:
                metadata_state='valid'
        prepared.append((sid,value['state'],metadata,metadata_state))
    return clean_metrics,prepared

def reconcile_backup_state(service,reported_state,metadata_result,now):
    """Require current safe evidence for reported normal/maintenance states."""
    if reported_state not in ('up','maintenance'):
        return reported_state,None
    metadata=backup_metadata_for(service,now)
    if metadata_result!='valid':
        return 'unknown','备份状态缺少有效安全快照元数据'
    if not metadata['fresh']:
        return 'down','备份快照或全仓读检回执已过期'
    transport=metadata.get('transport')
    if metadata.get('transport_configured'):
        if not isinstance(transport,dict) or transport.get('state')=='unknown':
            return 'unknown','上传通路证据缺失或无效'
    transport=transport or {}
    if transport.get('state')=='down':
        detail=('上传通路回执已过期' if metadata.get('transport_stale')
                else '上传通路断开')
        return 'down',detail
    return reported_state,None

def accepts_gzip(header):
    for value in header.lower().split(','):
        parts=[part.strip() for part in value.split(';')]
        if parts[0]!='gzip':continue
        quality=1.0
        for parameter in parts[1:]:
            if parameter.startswith('q='):
                try:quality=float(parameter[2:])
                except ValueError:return False
        return 0<quality<=1
    return False

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,code,obj,kind='application/json; charset=utf-8'):
        raw=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        compressed=len(raw)>=1024 and accepts_gzip(self.headers.get('Accept-Encoding',''))
        if compressed:raw=gzip.compress(raw,compresslevel=4,mtime=0)
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
        self.send_header('Vary','Accept-Encoding')
        if compressed:self.send_header('Content-Encoding','gzip')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def authenticated(self):
        cookie=self.headers.get('Cookie','')
        if not cookie or len(cookie)>16384:return False
        try:
            req=urllib.request.Request(os.environ.get('OPS_SESSION_URL','http://127.0.0.1:13000/api/auth/session'),headers={'Host':os.environ.get('OPS_SESSION_HOST','dashboard.example.com'),'Cookie':cookie})
            with urllib.request.urlopen(req,timeout=3) as r: value=json.loads(r.read(32768))
            user=value.get('user') if isinstance(value,dict) else None
            return isinstance(user,dict) and any(isinstance(user.get(key),str) and user[key].strip() for key in ('name','email','id'))
        except Exception:return False
    def do_GET(self):
        path=urlsplit(self.path)
        if path.path=='/health':self.send(200,{'running':True});return
        if not self.authenticated():self.send(401,{'error':'authentication_required'});return
        if path.path=='/api':
            window=parse_qs(path.query).get('window',['30d'])[0]
            if window not in ['24h','7d','30d','all']:self.send(400,{});return
            self.send(200,snapshot(window))
        elif path.path=='/service':
            q=parse_qs(path.query);sid=q.get('service',[''])[0]
            try:self.send(200,{'service':sid,'latency':service_latency(sid)})
            except ValueError:self.send(400,{'error':'invalid_service'})
        elif path.path=='/household':self.send(200,household_snapshot())
        elif path.path=='/events':
            q=parse_qs(path.query)
            try:
                before=float(q['before'][0]) if 'before' in q else None
                if before is not None and (not math.isfinite(before) or before<0 or before>time.time()+60):raise ValueError()
                self.send(200,event_page(q.get('window',['24h'])[0],int(q.get('page',['1'])[0]),15,q.get('service',[''])[0],before))
            except (ValueError,OverflowError):self.send(400,{'error':'invalid_pagination'})
        elif path.path in ['/ui.js','/ui.css']:
            self.send(200,(ROOT/path.path[1:]).read_bytes(),'application/javascript; charset=utf-8' if path.path.endswith('.js') else 'text/css; charset=utf-8')
        else:self.send(404,{})
    def do_POST(self):
        if self.path!='/ingest':self.send(404,{});return
        host=self.headers.get('X-Server-Profile',''); token=TOKENS.get(host)
        if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send(401,{});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768:raise ValueError()
            payload=json.loads(self.rfile.read(size)); now=time.time()
            clean,accepted=validate_ingest_payload(payload,host,now)
            with LOCK, DB:DB.execute('INSERT OR REPLACE INTO hosts VALUES(?,?,?)',(host,now,json.dumps(clean)))
            metadata_counts={'valid':0,'missing':0,'invalid':0}
            downgraded=0
            for sid,reported_state,metadata,metadata_state in accepted:
                service=SERVICES[sid]
                if isinstance(service.get('backup_job'),dict):
                    update_input={} if metadata_state=='missing' else {'backup':metadata}
                    result=update_backup_metadata(sid,host,update_input,now)
                    metadata_counts[result]+=1
                    state,evidence_detail=reconcile_backup_state(
                        service,reported_state,result,now)
                    downgraded+=state!=reported_state
                    if state=='maintenance':
                        detail='备份任务运行中'
                    elif state=='up':
                        detail='备份计划、真实快照和全仓读检正常'
                    elif state=='down':
                        detail=evidence_detail or '备份任务异常'
                    elif state=='degraded':
                        detail='备份检查需要核对'
                    else:
                        detail=evidence_detail or '备份状态未能确认'
                else:
                    state=reported_state
                    detail='宿主只读采集 · 运行状态' if state=='up' else '宿主只读采集 · 检查异常' if state!='unknown' else '宿主检查未能完成'
                update_service_channel(
                    sid,'host',state,detail,now=now,service=service)
                if (isinstance(service.get('backup_job'),dict) and
                        isinstance(service['backup_job'].get('transport_receipt'),str)):
                    transport_state,transport_detail=transport_channel_observation(
                        backup_metadata_for(service,now))
                    update_service_channel(
                        sid,'transport',transport_state,transport_detail,
                        now=now,service=service)
            self.send(200,{'accepted':len(accepted),
                           'accepted_backup_metadata':metadata_counts['valid'],
                           'missing_backup_metadata':metadata_counts['missing'],
                           'rejected_backup_metadata':metadata_counts['invalid'],
                           'downgraded_backup_states':downgraded})
        except (ValueError,TypeError,AttributeError):self.send(400,{'accepted':False})

if __name__=='__main__':
    threading.Thread(target=loop,daemon=True).start()
    ThreadingHTTPServer((os.environ.get('OPS_BIND','127.0.0.1'),int(os.environ.get('OPS_PORT','18182'))),Handler).serve_forever()
