"""Private operations read model. No SSH keys, Docker socket or control API."""
import concurrent.futures, datetime, hmac, json, math, os, sqlite3, threading, time
import urllib.request, urllib.error, smtplib, ssl
from email.message import EmailMessage
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('OPS_DATA', '/var/lib/home-console'))
DATA.mkdir(parents=True, exist_ok=True)
REGISTRY = json.loads(Path(os.environ.get('OPS_REGISTRY', str(ROOT / 'registry.json'))).read_text(encoding='utf-8-sig'))
SERVICES = {s['id']: s for s in REGISTRY['services']}
SERVERS = {s['id']: s for s in REGISTRY['servers']}

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
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,service TEXT,time REAL,old TEXT,state TEXT,detail TEXT);
CREATE INDEX IF NOT EXISTS events_time ON events(time DESC,id DESC);
CREATE INDEX IF NOT EXISTS events_service_time ON events(service,time DESC,id DESC);
CREATE TABLE IF NOT EXISTS hosts(id TEXT PRIMARY KEY,time REAL,metrics TEXT);
CREATE TABLE IF NOT EXISTS notices(event INTEGER PRIMARY KEY,status TEXT,attempts INTEGER,last_attempt REAL);
CREATE TABLE IF NOT EXISTS host_health(id TEXT PRIMARY KEY,state TEXT);
CREATE TABLE IF NOT EXISTS samples(service TEXT,time REAL,ms REAL);
CREATE INDEX IF NOT EXISTS samples_lookup ON samples(service,time);
''')
STATES = {'up','down','degraded','unknown','maintenance'}

def record(sid, state, detail='', ms=None, now=None):
    now = time.time() if now is None else now
    if sid not in SERVICES or state not in STATES: raise ValueError('invalid observation')
    detail = str(detail)[:160]
    with LOCK, DB:
        if ms is not None: DB.execute('INSERT INTO samples VALUES(?,?,?)',(sid,now,ms))
        prev = DB.execute('SELECT id,start,end,state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()
        had_previous = prev is not None
        if prev:
            pid, start, end, old = prev
            if now < end: return
            if now-end > 150:
                edge = end+90
                DB.execute('UPDATE spans SET end=? WHERE id=?',(edge,pid))
                DB.execute('INSERT INTO spans(service,start,end,state,detail) VALUES(?,?,?,?,?)',(sid,edge,now,'unknown','采集间断'))
                prev = None
            elif old == state:
                DB.execute('UPDATE spans SET end=?,detail=?,ms=? WHERE id=?',(now,detail,ms,pid))
                return
            else:
                DB.execute('UPDATE spans SET end=? WHERE id=?',(now,pid))
            if old != state:
                DB.execute('INSERT INTO events(service,time,old,state,detail) VALUES(?,?,?,?,?)',(sid,now,old,state,detail))
        DB.execute('INSERT INTO spans(service,start,end,state,detail,ms) VALUES(?,?,?,?,?,?)',(sid,now,now,state,detail,ms))
        if not had_previous and state in ('down','degraded'):
            DB.execute('INSERT INTO events(service,time,old,state,detail) VALUES(?,?,?,?,?)',(sid,now,'unknown',state,detail))

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
    known=durations['up']+durations['down']+durations['degraded']
    return {'availability':round(100*durations['up']/known,3) if known else None,
            'coverage':round(100*known/max(end-start,1),2),'known_seconds':round(known),
            'down_seconds':round(durations['down']+durations['degraded']),
            'maintenance_seconds':round(durations['maintenance'])}

def event_page(window='24h',page=1,size=15,service='',before=None):
    if window not in ('24h','7d','30d','all') or page<1 or not 1<=size<=100:raise ValueError('Invalid pagination')
    allowed=list(SERVICES)+list(SERVERS)
    if service and service not in allowed:raise ValueError('Unknown resource')
    end=before if before is not None else time.time()
    seconds={'24h':86400,'7d':604800,'30d':2592000}.get(window)
    where=['service IN ('+','.join('?' for _ in allowed)+')','time<=?',"(state IN ('down','degraded') OR (state='up' AND old IN ('down','degraded')))" ]
    args=allowed+[end]
    if seconds:where.append('time>=?');args.append(end-seconds)
    if service:where.append('service=?');args.append(service)
    clause=' AND '.join(where)
    with LOCK:
        total=DB.execute('SELECT COUNT(*) FROM events WHERE '+clause,args).fetchone()[0]
        pages=max(1,math.ceil(total/size));page=min(page,pages)
        rows=DB.execute('SELECT id,service,time,old,state,detail FROM events WHERE '+clause+' ORDER BY time DESC,id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]).fetchall()
    return {'items':[dict(zip(['id','service','time','old','state','detail'],r)) for r in rows],'total':total,'page':page,'pages':pages,'size':size,'before':end}

def snapshot(window):
    now=time.time(); seconds={'24h':86400,'7d':604800,'30d':2592000}.get(window)
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
            latency=DB.execute('SELECT CAST(time/300 AS INTEGER)*300,AVG(ms) FROM samples WHERE service=? AND time>=? GROUP BY CAST(time/300 AS INTEGER) ORDER BY time LIMIT 400',(sid,max(start,now-86400))).fetchall()
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
        result.append({**service,'state':state,'detail':detail,'checked_at':row[0] if row else None,'ms':row[3] if row else None,'monitoring_since':began,'latency':latency,**stats,'bars':bars})
    rates=[s['availability'] for s in result if s['availability'] is not None]
    servers=[]
    for host in SERVERS.values():
        obs=hosts.get(host['id']); fresh=obs and now-obs['last_seen']<=150
        servers.append({**host,'state':'up' if fresh else 'unknown','monitoring_issue':bool(obs and not fresh),'last_seen':obs['last_seen'] if obs else None,'metrics':obs['metrics'] if fresh else {}})
    household={}
    for key in ['ups','ddns']:
        try:
            with urllib.request.urlopen(os.environ.get('OPS_HOUSEHOLD_URL','http://127.0.0.1:18181')+'/api/'+key,timeout=2) as r: household[key]=json.loads(r.read(8192))
        except Exception: household[key]={}
    events=[e for e in events if e['service'] in SERVICES or e['service'] in SERVERS]
    return {'generated_at':now,'window_start':start,'servers':servers,'services':result,'events':events,'household':household,'documents':REGISTRY.get('documents',[]),'downloads':REGISTRY.get('downloads',[]),
            'summary':{'servers':len(servers),'services':len(result),'apps':sum(s['app'] for s in result),
              'abnormal':sum(s['state'] in ('down','degraded') for s in result),
              'unknown':sum(s['state']=='unknown' for s in result),
              'abnormal_servers':sum(h['monitoring_issue'] for h in servers),
              'availability':round(sum(rates)/len(rates),3) if rates else None,
              'availability_services':len(rates),
              'coverage':round(sum(s['coverage'] for s in result)/max(len(result),1),2)}}

def notify_pending():
    filename=os.environ.get('OPS_NOTIFY')
    if not filename:return
    try:config=json.loads(Path(filename).read_text())
    except (OSError,ValueError):return
    if not isinstance(config,dict) or config.get('enabled') is False:return
    with LOCK:
        rows=DB.execute("SELECT e.id,e.service,e.time,e.old,e.state,e.detail,coalesce(n.attempts,0) FROM events e LEFT JOIN notices n ON n.event=e.id WHERE (n.status IS NULL OR n.status NOT IN ('sent','skipped')) AND coalesce(n.attempts,0)<5 AND coalesce(n.last_attempt,0)<? ORDER BY e.id LIMIT 10",(time.time()-300,)).fetchall()
    for eid,sid,when,old,state,detail,attempts in rows:
        if sid not in SERVICES and sid not in SERVERS or state not in ('up','down','degraded') or (state=='up' and old not in ('down','degraded')):
            with LOCK,DB:DB.execute('INSERT OR REPLACE INTO notices VALUES(?,?,?,?)',(eid,'skipped',attempts,time.time()))
            continue
        item=SERVICES.get(sid) or SERVERS.get(sid,{'name':sid})
        if not all(isinstance(config.get(k),str) and config[k] for k in ('sender','recipient','address','username','password')):
            continue
        message=EmailMessage();message['From']=config['sender'];message['To']=config['recipient']
        message['Subject']='[NestOps] '+('恢复' if state=='up' else '异常')+' · '+item['name']
        message.set_content(item['name']+'\n'+detail+'\n时间：'+datetime.datetime.fromtimestamp(when,datetime.timezone.utc).isoformat()+'\n查看：'+os.environ.get('OPS_PUBLIC_URL','https://dashboard.example.com')+'\n事件编号：'+str(eid))
        status='failed'
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
        except Exception:pass
        with LOCK,DB:DB.execute('INSERT OR REPLACE INTO notices VALUES(?,?,?,?)',(eid,status,attempts+1,time.time()))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def probe(service):
    p=service.get('probe')
    if not p: return
    started=time.monotonic()
    try:
        request=urllib.request.Request(p['url'],headers={'User-Agent':'HomeConsole/1.0'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request,timeout=6) as response: code=response.status
        except urllib.error.HTTPError as e: code=e.code
        ok=code in p.get('codes',[200])
        record(service['id'],'up' if ok else 'down',f'HTTP {code} · {p.get("label","入口检查")}',round((time.monotonic()-started)*1000))
    except Exception:
        record(service['id'],'down','入口连接或 TLS 检查失败')

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
            DB.execute('DELETE FROM samples WHERE time<?',(time.time()-31*86400,))
        notify_pending()
        time.sleep(60)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,code,obj,kind='application/json; charset=utf-8'):
        raw=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
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
            observations=payload.get('services',{})
            if not isinstance(observations,dict):raise ValueError()
            clean={}
            for key,value in payload.get('metrics',{}).items():
                if key in ['memory_percent','disk_percent','load1','uptime_seconds'] and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=1e10:clean[key]=value
            accepted=[]
            for sid,value in observations.items():
                if sid in SERVICES and SERVICES[sid]['server']==host and not SERVICES[sid].get('probe') and isinstance(value,dict) and value.get('state') in STATES:
                    accepted.append((sid,value['state']))
            with LOCK, DB:DB.execute('INSERT OR REPLACE INTO hosts VALUES(?,?,?)',(host,now,json.dumps(clean)))
            for sid,state in accepted:record(sid,state,'宿主只读采集 · 运行状态' if state=='up' else '宿主只读采集 · 检查异常' if state!='unknown' else '宿主检查未能完成',now=now)
            self.send(200,{'accepted':len(accepted)})
        except (ValueError,TypeError,AttributeError):self.send(400,{'accepted':False})

if __name__=='__main__':
    threading.Thread(target=loop,daemon=True).start()
    ThreadingHTTPServer((os.environ.get('OPS_BIND','127.0.0.1'),int(os.environ.get('OPS_PORT','18182'))),Handler).serve_forever()
