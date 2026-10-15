"""A small, portable read-only status relay. No service credentials are stored here."""
import hmac,json,os,time,threading,socket,ipaddress
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
STATE=Path(os.environ.get('STATE_FILE','/data/status.json'))
TOKEN=Path(os.environ.get('TOKEN_FILE','/run/secrets/status-token')).read_text().strip()
MAX_AGE=int(os.environ.get('MAX_AGE','60'))
lock=threading.Lock()
DNS_DOMAIN=os.environ.get('DNS_DOMAIN','home.example.com')
DNS_INTERVAL=60
dns_lock=threading.Lock()
dns_state={'addresses':[],'checked':0,'ok':False}

def resolve_dns(resolver=socket.getaddrinfo):
    """Runs off the HTTP path; resolver failures never block dashboard requests."""
    try:
        answers=resolver(DNS_DOMAIN,None,socket.AF_INET,socket.SOCK_STREAM)
        addresses=sorted({str(ipaddress.ip_address(row[4][0])) for row in answers
                          if ipaddress.ip_address(row[4][0]).is_global})
        result={'addresses':addresses,'checked':time.time(),'ok':bool(addresses)}
    except (OSError,ValueError):
        result={'addresses':[],'checked':time.time(),'ok':False}
    with dns_lock:
        dns_state.update(result)

def dns_loop():
    while True:
        resolve_dns()
        time.sleep(DNS_INTERVAL)

def dns_view():
    with dns_lock:
        state=dict(dns_state)
    fresh=0<=time.time()-state['checked']<=DNS_INTERVAL*2
    ok=state['ok'] and fresh
    return {'ipv4':', '.join(state['addresses']) if ok else '—',
            'resolved':bool(ok),'checked_at':state['checked'],
            'state':'已解析' if ok else ('查询中' if not state['checked'] else '解析暂不可用')}
def sanitize(value):
    if not isinstance(value,dict):raise ValueError('Invalid payload')
    ups=value.get('ups',{});out={}
    if not isinstance(ups,dict):raise ValueError('Invalid UPS data')
    status=ups.get('status','UNKNOWN')
    if status not in ['ONLINE','ON_BATTERY','LOW_BATTERY','OFFLINE','UNKNOWN']:status='UNKNOWN'
    out['status']=status
    for key in ['battery_charge','battery_runtime','load_percent']:
        v=ups.get(key)
        out[key]=v if isinstance(v,(int,float)) and not isinstance(v,bool) and 0<=v<=864000 else None
    return {'ups':out,'ddns':value.get('ddns') is True,'received':time.time()}
def read_state():
    try:state=json.loads(STATE.read_text())
    except (OSError,ValueError):state={}
    fresh=0<=time.time()-state.get('received',0)<=MAX_AGE
    return state,fresh
def ups_view(state,fresh):
    ups=state.get('ups',{}) if fresh else {}
    return {'status':ups.get('status','HOME_OFFLINE'),
      'battery_charge':str(ups['battery_charge'])+'%' if ups.get('battery_charge') is not None else '—',
      'battery_runtime':str(ups['battery_runtime']) if ups.get('battery_runtime') is not None else '—',
      'load_percent':str(ups['load_percent'])+'%' if ups.get('load_percent') is not None else '—'}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,obj):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        if self.path!='/ingest':self.send(404,{});return
        if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN):self.send(401,{});return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=8192:raise ValueError()
            result=sanitize(json.loads(self.rfile.read(n)))
            with lock:
                temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(result));temp.replace(STATE)
            self.send(200,{'accepted':True})
        except (ValueError,OSError,TypeError):self.send(400,{'accepted':False})
    def do_GET(self):
        state,fresh=read_state()
        if self.path=='/api/ups':self.send(200,ups_view(state,fresh))
        elif self.path=='/api/ddns':self.send(200,dns_view())
        elif self.path=='/health/ups':self.send(200 if fresh and state.get('ups',{}).get('status') not in ['UNKNOWN','OFFLINE'] else 503,{'online':fresh})
        elif self.path=='/health/ddns':
            online=fresh and state.get('ddns',False) and dns_view()['resolved']
            self.send(200 if online else 503,{'online':bool(online)})
        elif self.path=='/health':self.send(200,{'running':True})
        else:self.send(404,{})
if __name__=='__main__':
    threading.Thread(target=dns_loop,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',18181),Handler).serve_forever()
