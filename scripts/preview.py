"""Local-only synthetic UI preview. No real hosts, cookies or production history."""
import json,time,math
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[1]
NOW=time.time()
HOSTS=[('home','家庭计算节点','192.0.2.10'),('cloud','云端应用节点','198.51.100.20'),('storage','存储与备份节点','203.0.113.30')]
def services():
    result=[]
    for i in range(36):
        rate=[100,99.2,97.5,100][i%4];coverage=[100,92,75,100][i%4]
        result.append(dict(id='demo:'+str(i),server='demo',name=['网站入口','数据服务','自动化任务','文件服务'][i%4]+' '+str(i+1).zfill(2),category='开发与运维',app=True,href='https://example.com/',state='degraded' if i==2 else 'up',availability=rate,coverage=coverage,detail='示例运行状态',ms=42+i,bars=[dict(start=NOW-86400+j*1800,end=NOW-86400+(j+1)*1800,state='degraded' if j==28 and i%4==2 else 'up',availability=rate,coverage=coverage) for j in range(48)],monitoring_since=NOW-86400,checked_at=NOW,down_seconds=120,latency=[]))
    names=[('ups','UPS Guard','家庭服务'),('ddns','DDNS 管理','家庭服务'),('tos','家庭存储中心','家庭服务'),('growth','协作工作台','业务系统'),('talent','任务管理','业务系统'),('blog','技术博客','网站与内容'),('images','图床','网站与内容'),('website','项目网站','网站与内容'),('mcsm','游戏服务管理','游戏相关')]
    for i,item in enumerate(result):
        item['server']=HOSTS[i%3][0]
        if i<len(names):
            key,name,category=names[i];item.update(id='demo:'+key,name=name,category=category)
        else:
            item['href']=''
    return result
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        p=urlsplit(self.path);q=parse_qs(p.query)
        if p.path=='/test-reconcile':
            raw=(ROOT/'scripts/test_reconcile.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw);return
        if p.path=='/_ops/api':
            payload=dict(services=services(),servers=[dict(id=key,name=name,address=address,state='up',metrics={},last_seen=NOW,monitoring_issue=False) for key,name,address in HOSTS],summary=dict(servers=3,services=36,apps=36,abnormal=1,unknown=0,abnormal_servers=0,availability=99.175,availability_services=36,coverage=91.75),events=[],household={},documents=[],downloads=[])
        elif p.path=='/_ops/household':
            payload=dict(generated_at=NOW,household={})
        elif p.path=='/_ops/service':
            payload=dict(service=q.get('service',[''])[0],latency=[[NOW-300*j,45+8*math.sin(j/4)] for j in reversed(range(48))])
        elif p.path=='/_ops/events':
            page=min(16,max(1,int(q.get('page',['1'])[0])));sid=q.get('service',[''])[0]
            items=services()
            rows=[dict(id=i,service=sid or items[i%36]['id'],time=NOW-i*300,old='down' if i%2 else 'up',state='up' if i%2 else 'down',detail='示例入口检查失败' if i%2==0 else '已恢复正常') for i in range(240)]
            payload=dict(items=rows[(page-1)*15:page*15],total=240,page=page,pages=16,before=NOW)
        elif p.path in ['/ui.js','/ui.css']:
            raw=(ROOT/'console'/p.path[1:]).read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript' if p.path.endswith('.js') else 'text/css');self.end_headers();self.wfile.write(raw);return
        else:
            raw='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NestOps · 合成数据演示</title><link rel="stylesheet" href="/ui.css"><style>body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 12% 90%,#8a6955 0,transparent 55%),radial-gradient(ellipse at 90% 10%,#255b62 0,transparent 60%),#122c3b;color:#edf5f3;font-family:system-ui;background-attachment:fixed}.demo-head{max-width:1108px;margin:32px auto 20px;padding:0 36px;display:flex;justify-content:space-between;align-items:center;gap:20px}.demo-head h1{font-size:27px;margin:0}.demo-head p{font-size:12px;color:#c8dbe7;margin:8px 0}.demo-badge{border:1px solid #b8dfe955;border-radius:20px;padding:8px 14px;font-size:12px}#footer{padding:24px;text-align:center;font-size:12px;color:#c8dbe7}@media(max-width:650px){.demo-head{padding:0 24px;align-items:flex-start}.demo-head h1{font-size:23px}.demo-badge{max-width:110px}}</style><body><header class="demo-head"><div><h1>栖云 NestOps</h1><p>你的服务入口与运行全景。</p></div><span class="demo-badge">公开演示 · 全部为合成数据</span></header><footer id="footer">合成数据演示 · 示例地址遵循 RFC 5737 · 不连接任何真实服务</footer><script src="/ui.js"></script></body></html>'''.encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw);return
        raw=json.dumps(payload,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
if __name__=='__main__':
    print('Synthetic preview: http://127.0.0.1:18765',flush=True)
    HTTPServer(('127.0.0.1',18765),Handler).serve_forever()
