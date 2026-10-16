"""Local-only synthetic UI preview. No real hosts, cookies or production history."""
import json,time,math
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1]
NOW=time.time()
def services():
    result=[]
    for i in range(36):
        rate=[100,99.2,97.5,100][i%4];coverage=[100,92,75,100][i%4]
        result.append(dict(id='demo:'+str(i),server='demo',name=['网站入口','数据服务','自动化任务','文件服务'][i%4]+' '+str(i+1).zfill(2),category='开发与运维',app=True,href='https://example.com/',state='degraded' if i==2 else 'up',availability=rate,coverage=coverage,detail='示例运行状态',ms=42+i,bars=[dict(start=NOW-86400+j*1800,end=NOW-86400+(j+1)*1800,state='degraded' if j==28 and i%4==2 else 'up',availability=rate,coverage=coverage) for j in range(48)],monitoring_since=NOW-86400,checked_at=NOW,down_seconds=120,latency=[]))
    return result
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        p=urlsplit(self.path);q=parse_qs(p.query)
        if p.path=='/_ops/api':
            payload=dict(services=services(),servers=[dict(id='demo',name='演示服务器',address='server.example.com',state='up',metrics={},last_seen=NOW,monitoring_issue=False)],summary=dict(servers=1,services=36,apps=36,abnormal=1,unknown=0,abnormal_servers=0,availability=99.175,availability_services=36,coverage=91.75),events=[],household={},documents=[],downloads=[])
        elif p.path=='/_ops/events':
            page=min(16,max(1,int(q.get('page',['1'])[0])));sid=q.get('service',[''])[0]
            rows=[dict(id=i,service=sid or 'demo:'+str(i%36),time=NOW-i*300,old='down' if i%2 else 'up',state='up' if i%2 else 'down',detail='示例检查异常') for i in range(240)]
            payload=dict(items=rows[(page-1)*15:page*15],total=240,page=page,pages=16,before=NOW)
        elif p.path in ['/ui.js','/ui.css']:
            raw=(ROOT/'console'/p.path[1:]).read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript' if p.path.endswith('.js') else 'text/css');self.end_headers();self.wfile.write(raw);return
        else:
            raw=b'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/ui.css"><style>body{margin:0;background:#173d43;color:#edf5f3;font-family:system-ui}#footer{padding:24px;text-align:center}</style><body><footer id="footer">NestOps synthetic demo - no production data</footer><script src="/ui.js"></script></body></html>'
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw);return
        raw=json.dumps(payload,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
if __name__=='__main__':
    print('Synthetic preview: http://127.0.0.1:18765',flush=True)
    HTTPServer(('127.0.0.1',18765),Handler).serve_forever()
