/* Authenticated, read-only extension to the existing Homepage. */
(()=>{
 if(window.__homeConsole)return;window.__homeConsole=true;
 const labels={up:'正常',down:'不可用',degraded:'异常',unknown:'未知',maintenance:'维护'};
 let data,tab='services',selectedHost='',filterHost='',listPage=1,selectedService='',query='',unknownExpanded=false,windowKey='24h',only='all',busy=false;
 const latencyCache=new Map(),latencyRequests=new Map();
 let ready=false,staging=false,loadController,loadGeneration=0,householdData={},householdPending=false,sessionGeneration=0;
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 const button=(text,action,cls='ops-button')=>{const n=el('button',cls,text);n.type='button';n.onclick=action;return n;};
 let selectNumber=0;
 function glassSelect(label,options,value,change){
  const wrap=el('div','ops-dropdown'),menu=el('div','ops-select-menu');menu.id='ops-options-'+(++selectNumber);menu.hidden=true;menu.setAttribute('role','listbox');menu.setAttribute('aria-label',label);
  const trigger=button(options.find(o=>o[0]===value)?.[1]||label,()=>toggle(!menu.hidden),'ops-select');trigger.setAttribute('role','combobox');trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-controls',menu.id);trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
  let index=Math.max(0,options.findIndex(o=>o[0]===value));
  function toggle(close){if(!close)for(const other of root.querySelectorAll('.ops-dropdown'))if(other!==wrap){other.querySelector('.ops-select-menu').hidden=true;other.querySelector('[role=combobox]').setAttribute('aria-expanded','false');}menu.hidden=close;trigger.setAttribute('aria-expanded',String(!close));if(!close)menu.children[index]?.focus();}
  options.forEach(([v,name],i)=>{const option=button(name,()=>{value=v;index=i;trigger.textContent=name;toggle(true);trigger.focus();change(v);},'ops-select-option');option.setAttribute('role','option');option.setAttribute('aria-selected',String(v===value));option.onkeydown=e=>{if(['ArrowDown','ArrowUp','Home','End','Escape','Tab'].includes(e.key)){if(e.key==='Escape'||e.key==='Tab'){toggle(true);if(e.key==='Escape'){e.preventDefault();trigger.focus();}return;}e.preventDefault();index=e.key==='Home'?0:e.key==='End'?options.length-1:(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;menu.children[index].focus();}};menu.append(option);});
  trigger.onkeydown=e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();toggle(false);}};
  wrap.onfocusout=()=>queueMicrotask(()=>{if(!wrap.contains(document.activeElement))toggle(true);});wrap.append(trigger,menu);return wrap;
 }
 const pct=n=>n===null||n===undefined?'—':n.toFixed(2)+'%';
 const dateFormatter=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}),stampCache=new Map();
 const stamp=t=>{if(!t)return '尚未采集';if(stampCache.has(t))return stampCache.get(t);if(stampCache.size>=2048)stampCache.clear();const text=dateFormatter.format(new Date(t*1000));stampCache.set(t,text);return text;};
 const duration=s=>s>=3600?(s/3600).toFixed(1)+' 小时':Math.round(s/60)+' 分钟';
 const mark=(state)=>{const n=el('span','ops-state ops-'+state,labels[state]);return n;};
 const hostName=id=>data.servers.find(h=>h.id===id)?.name||id;
 const resetScroll=()=>{window.scrollTo(0,0);for(let n=root.parentElement;n;n=n.parentElement)if(n.scrollTop)n.scrollTop=0;};
 const goService=sid=>{selectedService=sid;render();resetScroll();};
 const goHost=id=>{tab='servers';selectedHost=id;selectedService='';render();resetScroll();};
 let root,body,summary,toolbar,notice,downloads;
 const bars=s=>{
  const row=el('div','ops-bars');row.tabIndex=0;row.setAttribute('role','group');row.setAttribute('aria-label',s.name+'可用性历史，点击色块或左右方向键查看时间');
  let active=s.bars.length-1;const info=el('span','ops-bar-info');info.hidden=true;info.setAttribute('role','status');
  const show=i=>{active=Math.max(0,Math.min(s.bars.length-1,i));info.textContent=row.children[active].getAttribute('aria-label');info.hidden=false;};
  for(const [i,bar] of s.bars.entries()){const n=el('span','ops-bar ops-'+bar.state);n.setAttribute('role','img');n.onclick=()=>show(i);n.onmouseenter=()=>show(i);n.onmouseleave=()=>{info.hidden=true;};
   const label=stamp(bar.start)+' 至 '+stamp(bar.end)+' · '+labels[bar.state]+' · 可用率 '+pct(bar.availability)+' · 覆盖 '+pct(bar.coverage);
   n.setAttribute('aria-label',label);row.append(n);}
  row.append(info);row.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Enter',' '].includes(e.key)){e.preventDefault();show(active+(e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0));}else if(e.key==='Escape')info.hidden=true;};row.onblur=()=>{info.hidden=true;};return row;
 };
 const footerStats=s=>{const n=el('div','ops-inline-stats');n.append(el('span','',pct(s.availability)+' 可用率'),el('span','',pct(s.coverage)+' 监测覆盖'));return n;};
 function safeHref(value,protocols=['https:','http:','hometunnel:']){
  if(typeof value!=='string'||!value)return null;
  try{const url=new URL(value,location.origin);if(!protocols.includes(url.protocol))return null;
   if(url.protocol==='hometunnel:'&&/^Win/.test(navigator.platform)){
    const agent=navigator.userAgent;const browser=/Firefox\//.test(agent)?'firefox':/Edg\//.test(agent)?'edge':/Chrome\//.test(agent)?'chrome':/Safari\//.test(agent)?'safari':'default';url.searchParams.set('browser',browser);
   }return url.href;
  }catch{return null;}
 }
 const entry=s=>{
  const href=safeHref(s.href);if(!href)return null;
  const a=el('a','ops-open','打开');a.href=href;a.target=href.startsWith('hometunnel:')?'_self':'_blank';a.rel='noopener noreferrer';a.setAttribute('aria-label','打开 '+s.name);return a;
 };
 const filtered=services=>services.filter(s=>(!(selectedHost||filterHost)||s.server===(selectedHost||filterHost))&&(!query||(s.name+' '+hostName(s.server)).toLowerCase().includes(query.toLowerCase()))&&(only==='all'||only==='abnormal'&&['down','degraded'].includes(s.state)||only==='unknown'&&s.state==='unknown'));
 const sortedServices=items=>[...items].sort((a,b)=>{
  const av=Number.isFinite(a.availability)?a.availability:Infinity,bv=Number.isFinite(b.availability)?b.availability:Infinity;
  return (av===bv?0:av-bv)||(a.coverage??0)-(b.coverage??0)||a.name.localeCompare(b.name,'zh-CN')||a.id.localeCompare(b.id);
 });
 const eventPages=new Map();
 function eventPanel(service=''){
  const eventWindow=windowKey,key=eventWindow+'|'+service, state=eventPages.get(key)||{page:1,before:null};eventPages.set(key,state);
  if(state.wrap){if(!state.pending&&Date.now()-(state.loadedAt||0)>30000)state.show(state.page,true);return state.wrap;}
  const wrap=el('section','ops-paged-events');wrap.setAttribute('aria-label','故障与恢复记录分页');
  state.wrap=wrap;state.request=0;
  async function show(page,background=false){
   const request=++state.request;state.controller?.abort();const controller=new AbortController();state.controller=controller;state.pending=true;
   const timeout=setTimeout(()=>controller.abort(),15000);
   wrap.setAttribute('aria-busy','true');if(!background)for(const b of wrap.querySelectorAll('button'))b.disabled=true;
   try{
    const params=new URLSearchParams({window:eventWindow,page:String(page),service});if(state.before)params.set('before',state.before);
    const response=await fetch('/_ops/events?'+params,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(response.status===401)throw Error('session');if(!response.ok)throw Error('request');const result=await response.json();
    if(request!==state.request)return;
    state.page=result.page;state.before=result.before;state.loadedAt=Date.now();state.result=result;wrap.replaceChildren(eventList(result.items));
    const nav=el('nav','ops-pagination');nav.setAttribute('aria-label','事件分页');
    const previous=button('上一页',()=>show(state.page-1));previous.disabled=state.page<=1;
    const next=button('下一页',()=>show(state.page+1));next.disabled=state.page>=result.pages;
    nav.append(el('span','',result.total+' 条记录 · 第 '+state.page+' / '+result.pages+' 页'),previous,next,button('查看最新',()=>{state.before=null;show(1);}));wrap.append(nav);
   }catch(error){
    if(request!==state.request)return;
    if(error.message==='session'){state.result=null;wrap.replaceChildren(el('p','ops-empty','登录会话已过期，请刷新主页重新登录。'));}
    else {const message=el('div','ops-event-error');message.setAttribute('role','status');message.append(el('span','',state.result?'更新失败，仍显示上次记录。':'记录加载失败，请重试。'),button('重试',()=>show(page)));wrap.querySelector('.ops-event-error')?.remove();if(!state.result)wrap.replaceChildren();wrap.append(message);for(const b of wrap.querySelectorAll('button'))b.disabled=b.textContent==='上一页'?state.page<=1:b.textContent==='下一页'?state.page>=(state.result?.pages??1):false;}
   }
   finally{clearTimeout(timeout);if(request===state.request){state.pending=false;wrap.setAttribute('aria-busy','false');}}
  }
  state.show=show;
  const start=data.window_start??0,seed=(data.events||[]).filter(e=>(!service||e.service===service)&&e.time>=start&&(['down','degraded'].includes(e.state)||(e.state==='up'&&['down','degraded'].includes(e.old)))).sort((a,b)=>b.time-a.time||b.id-a.id).slice(0,15);
  if(seed.length)wrap.append(eventList(seed));
  else {const skeleton=el('div','ops-glass ops-events ops-event-skeleton');skeleton.setAttribute('aria-label','正在加载记录');for(let i=0;i<5;i++){const row=el('div','ops-event');row.setAttribute('aria-hidden','true');row.append(el('span','ops-skeleton ops-skeleton-title'));skeleton.append(row);}wrap.append(skeleton);}
  show(state.page);return wrap;
 }
 function pagedEvents(service=''){
  const panel=eventPanel(service);
  if(typeof staging!=='undefined'&&staging){const placeholder=el('section');placeholder.opsRetained=panel;return placeholder;}
  return panel;
 }
 function renderSummary(){
  const live=summary;summary=el('div');paintSummary();const fresh=summary;summary=live;reconcile(summary,fresh);
 }
 function paintSummary(){
  summary.replaceChildren();const s=data.summary;
  for(const [name,value,hint,action] of [
   ['服务器',s.servers,'按服务器查看',()=>{tab='servers';selectedHost='';selectedService='';render();}],
   ['全部服务',s.services,'含 '+s.apps+' 个业务应用',()=>{tab='servers';selectedHost='';selectedService='';render();}],
   ['当前异常',s.abnormal,s.unknown+' 项未知 · '+s.abnormal_servers+' 台采集失联',()=>{tab='alerts';selectedService='';render();}],
   ['平均可用率',pct(s.availability),s.availability_services+' 项有数据 · 等权平均',null],
   ['监测覆盖',pct(s.coverage),'所选时间范围内的覆盖',null]]){
   const n=action?button('',action,'ops-stat'):el('div','ops-stat');if(name==='当前异常'&&s.abnormal>0)n.classList.add(data.services.some(item=>item.state==='down')?'ops-stat-critical':'ops-stat-warning');n.append(el('span','ops-stat-label',name),el('strong','',String(value)),el('small','',hint));summary.append(n);
  }
 }
 function renderToolbar(){
  toolbar.replaceChildren();const nav=el('nav','ops-tabs');nav.setAttribute('aria-label','主页视图');const indicator=el('span','ops-tab-indicator');nav.append(indicator);
  const align=()=>{const selected=nav.querySelector('[aria-current=page]');if(selected){indicator.style.width=selected.offsetWidth+'px';indicator.style.transform='translateX('+selected.offsetLeft+'px)';}};
  for(const [id,name] of [['services','服务入口'],['servers','服务器'],['alerts','告警']]){const b=button(name,()=>{tab=id;selectedHost='';filterHost='';listPage=1;selectedService='';only='all';for(const item of nav.querySelectorAll('button'))item.setAttribute('aria-current',item===b?'page':'false');align();renderBody();},'ops-tab');b.setAttribute('aria-current',tab===id?'page':'false');nav.append(b);}toolbar.append(nav);align();
  const controls=el('div','ops-controls');controls.append(glassSelect('统计时间范围',[['24h','近24小时'],['7d','近7天'],['30d','近30天'],['all','累计']],windowKey,v=>{windowKey=v;load();}));
  const search=el('input','ops-search');search.type='search';search.placeholder='搜索服务或服务器';search.setAttribute('aria-label','搜索服务或服务器');search.value=query;search.oninput=()=>{query=search.value;listPage=1;renderBody();};controls.append(search);
  const refresh=button('刷新',()=>load());refresh.setAttribute('data-refresh','');refresh.disabled=busy;controls.append(refresh);toolbar.append(controls);
 }
 function filters(){
  const row=el('div','ops-filter-row');row.append(glassSelect('按服务器筛选',[['','全部服务器'],...data.servers.map(h=>[h.id,h.name])],filterHost,v=>{filterHost=v;listPage=1;renderBody();}),glassSelect('按状态筛选',[['all','全部状态'],['abnormal','仅异常'],['unknown','状态未知']],only,v=>{only=v;listPage=1;renderBody();}));return row;
 }
 const descriptions={growth:'飞书登录与成长管理',website:'公司与服务介绍','registry-ui':'镜像浏览与管理',skin:'角色与皮肤管理',mcsm:'游戏实例与节点管理',blog:'文章与知识分享',miaomiao:'苗苗的生活与文字',images:'图片上传与管理',talent:'人才与岗位管理',homepage:'统一访问与设备入口',authelia:'账号与双因素认证',tos:'NAS 管理与存储',allinssl:'证书申请与自动续期',ups:'供电状态与保护策略',ddns:'家庭动态域名更新',webhook:'GitHub 事件与飞书通知',mihomo:'轻量通用服务 · 代理与连接管理'};
 function serviceIcon(s){
  const wrapper=el('span','ops-entry-icon');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.7');
  const path=document.createElementNS(svg.namespaceURI,'path');const key=s.id.split(':')[1];
  const shapes={images:'M4 4h16v16H4z M4 17l5-5 4 4 3-3 4 4 M8 8h.01',blog:'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5',miaomiao:'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h5',growth:'M3 18l6-6 4 3 8-11 M16 4h5v5',mcsm:'M3 8h18v12H3z M7 8V4h10v4 M8 13h8 M12 10v6',registry:'M4 5h16v14H4z M8 9h8 M8 13h5','registry-ui':'M4 5h16v14H4z M8 9h8 M8 13h5',skin:'M5 4h14v16H5z M8 8h8 M8 12h8',homepage:'M4 5h16v14H4z M8 9h8 M8 13h5',authelia:'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z M9 12l2 2 4-4',ups:'M13 2L5 13h6l-1 9 8-11h-6z',ddns:'M4 7h16 M4 12h16 M4 17h16 M8 4v16 M16 4v16',allinssl:'M5 4h14v16H5z M8 8h8 M8 12h5',talent:'M4 19v-2a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v2 M12 11a4 4 0 1 0-0.1-8 4 4 0 0 0.1 8'};
  path.setAttribute('d',shapes[key]||({'tos':'M3 5h18v14H3z M6 9h12 M6 14h2 M11 14h2','webhook':'M8 8h8v8H8z M2 12h6 M16 12h6 M12 2v6 M12 16v6','mihomo':'M3 6h18 M3 12h18 M3 18h18 M7 3v6 M16 9v6 M10 15v6'}[key])||'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M2 12h20 M12 2c-5 6-5 14 0 20 M12 2c5 6 5 14 0 20');svg.append(path);wrapper.append(svg);return wrapper;
 }
 function servicesView(){
  body.append(filters());const services=filtered(data.services).filter(s=>entry(s));
  if(!services.length){body.append(el('p','ops-empty','没有匹配的服务入口。可以在服务器页查看全部已登记服务。'));return;}
  const category=s=>{const key=s.id.split(':')[1];if(['growth','talent'].includes(key))return '业务系统';if(['blog','miaomiao','website','images'].includes(key))return '网站与内容';if(['mcsm','skin','palworld'].includes(key))return '游戏相关';if(['ups','tos','ddns'].includes(key))return '家庭服务';return '开发与运维';};
  for(const name of ['家庭服务','业务系统','网站与内容','游戏相关','开发与运维',...new Set(services.map(category))].filter((v,i,a)=>a.indexOf(v)===i)){
   const items=services.filter(s=>category(s)===name);if(!items.length)continue;
   const section=el('section','ops-category');section.append(el('h2','',name));const grid=el('div','ops-entry-grid');
   for(const s of items){
    const card=el('article','ops-glass ops-entry');const link=entry(s);link.className='ops-entry-link';link.replaceChildren(serviceIcon(s));
    const copy=el('span','ops-entry-copy');copy.append(el('strong','',s.name),el('small','',descriptions[s.id.split(':')[1]]||s.category));link.append(copy);card.append(link);
    const dot=mark(s.state);dot.classList.add('ops-entry-status');card.append(dot,button('查看状态',()=>goService(s.id),'ops-entry-detail'));
    if(s.id==='home-compute-1:ups'){const u=data.household?.ups||{};const metrics=el('div','ops-home-metrics');const seconds=Number(u.battery_runtime);for(const [label,value] of [['供电状态',u.status==='ONLINE'?'市电供电':u.status==='ON_BATTERY'?'电池供电':'未知'],['电池电量',u.battery_charge||'—'],['估算续航',Number.isFinite(seconds)?Math.floor(seconds/60)+'分 '+Math.floor(seconds%60)+'秒':'—'],['当前负载',u.load_percent||'—']]){const item=el('div');item.append(el('small','',label),el('strong','',value));metrics.append(item);}card.append(metrics);}
    if(s.id==='home-compute-1:ddns'){const d=data.household?.ddns||{};const metrics=el('div','ops-home-metrics');const item=el('div');item.append(el('small','','家庭域名 · 公网 IPv4'),el('strong','',d.ipv4||'—'));metrics.append(item);card.append(metrics);}
    grid.append(card);
   }section.append(grid);body.append(section);
  }
  const documents=(data.documents||[]).filter(d=>safeHref(d.href,['https:','http:']));
  if(documents.length){const docs=el('section','ops-category');docs.append(el('h2','','文档与工具'));const links=el('div','ops-doc-links');for(const document of documents){const a=el('a','ops-glass ops-doc-card');a.href=safeHref(document.href,['https:','http:']);a.target='_blank';a.rel='noopener noreferrer';a.append(serviceIcon({name:document.name,id:'docs:blog'}));const copy=el('span');copy.append(el('strong','',document.name),el('small','',document.description||''));a.append(copy);links.append(a);}docs.append(links);body.append(docs);}

 }
 function serviceRows(services){
  const list=el('div','ops-glass ops-list');
  for(const s of services){const row=el('article','ops-service-row');const identity=el('div','ops-identity');identity.append(button(s.name,()=>goService(s.id),'ops-name'),el('small','',s.app?'业务应用 · '+s.category:s.category));row.append(identity,mark(s.state),bars(s));
   const stats=el('div','ops-row-stats');stats.append(el('span','',pct(s.availability)),el('small','',s.ms===null?'暂无响应时间':Math.round(s.ms)+' ms'));row.append(stats);
   const open=entry(s);if(open)row.append(open);else row.append(el('span','ops-no-entry','内部服务'));list.append(row);
  }return list;
 }
 function pagedRows(items){
  items=sortedServices(items);
  if(!items.length)return el('p','ops-empty','没有匹配服务。');
  const size=15,pages=Math.ceil(items.length/size);listPage=Math.max(1,Math.min(listPage,pages));
  const wrap=el('div','ops-paged-list');wrap.append(serviceRows(items.slice((listPage-1)*size,listPage*size)));
  const nav=el('nav','ops-pagination');nav.setAttribute('aria-label','全部服务分页');
  const change=n=>{listPage=n;renderBody();body.querySelector('.ops-paged-list')?.scrollIntoView({block:'start'});};
  const prev=button('上一页',()=>change(listPage-1));prev.disabled=listPage===1;
  const next=button('下一页',()=>change(listPage+1));next.disabled=listPage===pages;
  nav.append(el('span','',items.length+' 项服务 · 第 '+listPage+' / '+pages+' 页'),prev,next);wrap.append(nav);return wrap;
 }
 function serversView(){
  if(selectedHost){const h=data.servers.find(h=>h.id===selectedHost);body.append(button('返回全部服务器',()=>{selectedHost='';render();},'ops-text-button'));
   const header=el('div','ops-detail-heading');header.append(el('h2','',h.name),mark(h.state));body.append(header,el('p','ops-host-label',h.address+' · 最后采集 '+stamp(h.last_seen)));
   const metrics=el('div','ops-server-metrics');for(const [key,label,suffix] of [['load1','负载',''],['memory_percent','内存使用','%'],['disk_percent','磁盘使用','%']])metrics.append(el('span','',label+' '+(h.metrics[key]===undefined?'—':h.metrics[key]+suffix)));body.append(metrics);
   const items=filtered(data.services);body.append(pagedRows(items));return;
  }
  const grid=el('div','ops-server-grid');for(const h of data.servers.filter(h=>!query||(h.name+' '+h.address).toLowerCase().includes(query.toLowerCase()))){
   const services=data.services.filter(s=>s.server===h.id);const abnormal=services.filter(s=>['down','degraded'].includes(s.state)).length;
   const card=button('',()=>goHost(h.id),'ops-glass ops-server');const top=el('div','ops-row-head');top.append(el('span','ops-server-symbol',''),mark(h.state));card.append(top,el('h2','',h.name),el('p','ops-host-label',h.address));
   const counts=el('div','ops-server-counts');counts.append(el('strong','',services.length+' 个服务'),el('span',abnormal?'ops-alert-ink':'',abnormal+' 项异常'));card.append(counts,el('small','',services.filter(s=>s.state==='unknown').length+' 项未知 · 查看全部服务'));grid.append(card);
  }body.append(grid,el('h2','ops-section-title','全部已登记服务'),filters());const items=filtered(data.services);body.append(pagedRows(items));
 }
 function eventList(events){
  const relevant=events.filter(e=>['down','degraded'].includes(e.state)||(e.state==='up'&&['down','degraded'].includes(e.old)));const list=el('div','ops-glass ops-events');if(!relevant.length){list.append(el('p','ops-empty','暂无故障与恢复记录。'));return list;}for(const e of relevant){const s=data.services.find(s=>s.id===e.service);const row=el('div','ops-event');row.append(el('time','',stamp(e.time)),button(s?.name||hostName(e.service),()=>s?goService(e.service):goHost(e.service),'ops-name'),mark(e.state),el('span','ops-event-detail',e.state==='up'?'已恢复正常':e.detail.includes('宿主只读采集')?'运行健康检查异常':e.detail));list.append(row);}return list;
 }
 function alertsView(){
  const lost=data.servers.filter(h=>h.monitoring_issue);if(lost.length){body.append(el('h2','ops-section-title','服务器采集失联'));const list=el('div','ops-glass ops-list');for(const h of lost){const row=el('div','ops-server-metrics');row.append(button(h.name,()=>goHost(h.id),'ops-name'),el('span','','最后采集 '+stamp(h.last_seen)+'；需要核对连接，尚未确认整机停机'));list.append(row);}body.append(list);}
  body.append(el('h2','ops-section-title','当前异常'));const abnormal=data.services.filter(s=>['down','degraded'].includes(s.state));body.append(abnormal.length?serviceRows(abnormal):el('p','ops-empty','当前没有已确认的服务异常。'));
  const unknown=data.services.filter(s=>s.state==='unknown');if(unknown.length){const details=el('details','ops-unknown-details');details.open=unknownExpanded;details.ontoggle=()=>{unknownExpanded=details.open;};details.append(el('summary','',unknown.length+' 项状态未知 · 需要核对监测覆盖'),serviceRows(unknown));body.append(details);}
  body.append(el('h2','ops-section-title','故障与恢复记录'),pagedEvents());
 }
 function latencyChart(s,latency){
  const chart=el('section','ops-glass ops-detail');chart.append(el('h3','','响应时间 · 近24小时'));if(!latency?.length){chart.append(el('p','ops-empty','暂无响应时间数据。'));return chart;}
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 800 160');svg.setAttribute('role','img');svg.setAttribute('aria-label','响应时间曲线，单位毫秒');const max=Math.max(...latency.map(p=>p[1]),1);const poly=document.createElementNS(svg.namespaceURI,'polyline');poly.setAttribute('points',latency.map((p,i)=>[10+i*780/Math.max(latency.length-1,1),145-p[1]/max*125].join(',')).join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#7ce5bb');poly.setAttribute('stroke-width','2');svg.append(poly);chart.append(svg,el('p','ops-host-label','5分钟平均响应 · 最高 '+Math.round(max)+' ms'));return chart;
 }
 function loadLatency(s,holder){
  const key=s.id,cached=latencyCache.get(key),generation=sessionGeneration;
  const update=rows=>{if(holder.isConnected&&generation===sessionGeneration&&selectedService===s.id)holder.replaceChildren(...(rows?latencyChart(s,rows):el('p','ops-empty','响应曲线暂时无法加载。')).childNodes);};
  if(cached&&Date.now()-cached.time<30000){update(cached.rows);return;}
  if(!latencyRequests.has(key)){
   const request=fetch('/_ops/service?service='+encodeURIComponent(s.id),{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)}).then(r=>{if(r.status===401){expireSession();throw Error('session');}if(!r.ok)throw Error();return r.json();}).then(result=>{if(generation!==sessionGeneration)return null;const rows=result.latency||[];latencyCache.set(key,{time:Date.now(),rows});return rows;}).catch(()=>null).finally(()=>latencyRequests.delete(key));latencyRequests.set(key,request);
  }
  latencyRequests.get(key).then(update);
 }
 function detailView(){
  const s=data.services.find(s=>s.id===selectedService);if(!s){selectedService='';return renderBody();}
  body.append(button('返回列表',()=>{selectedService='';render();},'ops-text-button'));const head=el('div','ops-detail-heading');head.append(el('h2','',s.name),mark(s.state));body.append(head,button(hostName(s.server),()=>goHost(s.server),'ops-text-button'));
  const panel=el('section','ops-glass ops-detail');panel.append(el('p','',s.detail),bars(s),footerStats(s));const legends=el('div','ops-legend');for(const state of Object.keys(labels))legends.append(mark(state));panel.append(legends,el('p','ops-host-label','最后检查 '+stamp(s.checked_at)+' · 当前响应 '+(s.ms===null?'—':Math.round(s.ms)+' ms')));
  const open=entry(s);if(open)panel.append(open);body.append(panel);
  const latencyHolder=el('section','ops-glass ops-detail');latencyHolder.setAttribute('data-latency',s.id);latencyHolder.style.minHeight='210px';latencyHolder.append(el('h3','','响应时间 · 近24小时'),el('p','ops-empty','正在加载响应曲线…'));body.append(latencyHolder);if(!staging)loadLatency(s,latencyHolder);
  const technical=el('dl','ops-glass ops-technical');for(const [label,value] of [['类别',s.app?'业务应用（计入服务总数）':s.category],['运行组件',(s.containers||[]).join('、')||(s.units||[]).join('、')||s.process||'按任务专用检查'],['开始监测',stamp(s.monitoring_since)],['统计中的异常时长',duration(s.down_seconds)],['监测口径',s.probe?'HTTP/TLS 入口检查；不代表登录后业务全链可用':'宿主运行状态；不代表业务操作已验收']]){technical.append(el('dt','',label),el('dd','',value));}body.append(technical,el('h2','ops-section-title','相关事件'),pagedEvents(s.id));
 }
 function reconcile(parent,fresh){
  // Keep unchanged subtrees and controls; replace closure-bound interactive regions as a unit.
  const children=[...fresh.childNodes];let cursor=parent.firstChild;
  for(const candidate of children){
   const wanted=candidate.opsRetained||candidate;
   if(wanted===cursor){cursor=cursor.nextSibling;continue;}
   if(!cursor){parent.append(wanted);continue;}
   if(cursor.nodeType!==wanted.nodeType||cursor.nodeName!==wanted.nodeName){const next=cursor.nextSibling;cursor.replaceWith(wanted);cursor=next;continue;}
   if(cursor.nodeType===3){if(cursor.nodeValue!==wanted.nodeValue)cursor.nodeValue=wanted.nodeValue;cursor=cursor.nextSibling;continue;}
   if(candidate.opsRetained){parent.insertBefore(wanted,cursor);continue;}
   if(cursor.hasAttribute('data-latency')&&cursor.getAttribute('data-latency')===wanted.getAttribute('data-latency')){cursor=cursor.nextSibling;continue;}
   if(cursor.outerHTML!==wanted.outerHTML){
    if(cursor.matches('button,a,svg,.ops-bars,.ops-dropdown,input,select')||cursor.onclick||cursor.onkeydown){const next=cursor.nextSibling;cursor.replaceWith(wanted);cursor=next;continue;}
    for(const attr of [...cursor.attributes])if(!wanted.hasAttribute(attr.name))cursor.removeAttribute(attr.name);
    for(const attr of [...wanted.attributes])if(cursor.getAttribute(attr.name)!==attr.value)cursor.setAttribute(attr.name,attr.value);
    reconcile(cursor,wanted);
   }
   cursor=cursor.nextSibling;
  }
  while(cursor){const next=cursor.nextSibling;cursor.remove();cursor=next;}
 }
 function renderBody(incremental=false){
  if(!data)return;const live=body;if(incremental){body=el('div');staging=true;}else body.replaceChildren();
  try{if(selectedService)detailView();else if(tab==='servers')serversView();else if(tab==='alerts')alertsView();else servicesView();}
  finally{if(incremental){const fresh=body;body=live;staging=false;reconcile(body,fresh);}}
  if(selectedService){const holder=body.querySelector('[data-latency]'),s=data.services.find(s=>s.id===selectedService);if(holder&&s)loadLatency(s,holder);}
 }
 function renderDownloads(){
  downloads.replaceChildren();const items=(data.downloads||[]).filter(d=>safeHref(d.href,['https:','http:']));
  if(!items.length)return;downloads.append(el('span','','隧道管理器'));
  for(const item of items){const link=el('a','',item.name);link.href=safeHref(item.href,['https:','http:']);link.rel='noopener noreferrer';downloads.append(link);}
 }
 function render(){if(!data)return;pagedEvents();renderSummary();renderToolbar();renderBody();renderDownloads();notice.textContent='';}
 function expireSession(){sessionGeneration++;loadGeneration++;loadController?.abort();busy=false;data=null;householdData={};latencyCache.clear();eventPages.clear();pendingRender=false;notice.textContent='登录会话已过期，请刷新主页重新登录。';for(const region of [body,summary,toolbar,downloads])region.replaceChildren();}
 async function loadHousehold(){
  if(householdPending)return;householdPending=true;const generation=sessionGeneration;
  try{const response=await fetch('/_ops/household',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(6000)});if(response.status===401){expireSession();return;}if(!response.ok)throw Error();const result=await response.json();if(generation!==sessionGeneration||!data)return;householdData=result.household||{};data.household=householdData;}
  catch{if(generation!==sessionGeneration||!data)return;householdData={};data.household={};}
  finally{householdPending=false;if(generation===sessionGeneration&&data&&tab==='services'&&!selectedService)renderBody(true);}
 }
 let pendingRender=false;
 async function load(background=false){
  if(busy&&background)return;
  loadController?.abort();const controller=new AbortController(),generation=++loadGeneration;loadController=controller;busy=true;const requestedWindow=windowKey;
  const active=document.activeElement,timeout=setTimeout(()=>controller.abort(),15000);
  try{
   const response=await fetch('/_ops/api?window='+requestedWindow,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
   if(generation!==loadGeneration)return;
   if(response.status===401){expireSession();return;}
   if(!response.ok)throw Error('request');
   const next=await response.json();if(generation!==loadGeneration||requestedWindow!==windowKey)return;
   next.household=householdData;data=next;root.setAttribute('aria-busy','false');latencyCache.clear();
   const focused=background&&root.contains(active)&&active.matches('input,select,[role=combobox],[role=option],.ops-bars');
   renderSummary();pagedEvents();renderDownloads();notice.textContent='';
   if(focused){pendingRender=true;}else{pendingRender=false;renderBody(true);}
   if(!background)renderToolbar();
   loadHousehold();
  }catch(error){
   if(generation!==loadGeneration)return;
   root.setAttribute('aria-busy','false');notice.textContent='监测数据暂时无法加载，请稍后刷新。已有记录不代表当前状态。';
   if(!data){summary.replaceChildren();toolbar.replaceChildren();body.replaceChildren();body.append(el('h2','ops-section-title','暂时无法加载服务目录'),el('p','ops-empty','请检查连接后重试。'),button('重新加载',()=>{renderSkeleton();load();}));}
   else{householdData={};data.household={};data.services.forEach(s=>{s.state='unknown';s.detail='无法取得最新数据';});data.servers.forEach(s=>{s.state='unknown';s.metrics={};});data.summary.abnormal=0;data.summary.unknown=data.services.length;data.summary.availability=null;renderSummary();renderBody(true);}
  }finally{clearTimeout(timeout);if(generation===loadGeneration){busy=false;const refresh=toolbar.querySelector('[data-refresh]');if(refresh)refresh.disabled=false;}}
 }
 function renderSkeleton(){
  root.setAttribute('aria-busy','true');summary.replaceChildren();toolbar.replaceChildren();body.replaceChildren();notice.textContent='正在加载服务目录…';
  for(const name of ['服务器','全部服务','当前异常','平均可用率','监测覆盖']){const stat=el('div','ops-stat');stat.append(el('span','ops-stat-label',name),el('span','ops-skeleton ops-skeleton-value'));summary.append(stat);}
  const nav=el('div','ops-tabs');for(const name of ['服务入口','服务器','告警']){const b=button(name,()=>{},'ops-tab');b.disabled=true;nav.append(b);}toolbar.append(nav,el('span','ops-skeleton ops-skeleton-control'));
  for(const [name,count] of [['家庭服务',3],['业务系统',2],['网站与内容',5],['游戏相关',2],['开发与运维',6]]){const section=el('section','ops-category');section.append(el('h2','',name));const grid=el('div','ops-entry-grid');for(let i=0;i<count;i++){const card=el('div','ops-glass ops-entry ops-skeleton-card');card.setAttribute('aria-hidden','true');card.append(el('span','ops-skeleton ops-skeleton-title'),el('span','ops-skeleton ops-skeleton-description'));grid.append(card);}section.append(grid);body.append(section);}
 }
 function start(){
  if(ready)return true;const footer=document.querySelector('#footer');if(!footer)return false;ready=true;
  root=el('section','home-console');root.id='home-console';root.setAttribute('aria-label','服务器与服务控制台');summary=el('div','ops-glass ops-summary');toolbar=el('div','ops-toolbar');body=el('div','ops-body');notice=el('p','ops-notice','正在获取监测状态…');notice.setAttribute('role','status');downloads=el('div','home-launcher-downloads');root.append(summary,toolbar,body,notice,downloads);footer.before(root);renderSkeleton();
  load();
  document.addEventListener('pointerdown',e=>{for(const wrap of root.querySelectorAll('.ops-dropdown'))if(!wrap.contains(e.target)){wrap.querySelector('.ops-select-menu').hidden=true;wrap.querySelector('[role=combobox]').setAttribute('aria-expanded','false');}},true);
  root.addEventListener('focusout',()=>queueMicrotask(()=>{if(pendingRender&&!root.contains(document.activeElement)){pendingRender=false;render();}}));
  setInterval(()=>{if(!document.hidden)load(true);},30000);return true;
 }
 window.addEventListener('resize',()=>{const nav=root?.querySelector('.ops-tabs'),b=nav?.querySelector('[aria-current=page]'),i=nav?.querySelector('.ops-tab-indicator');if(b&&i){i.style.width=b.offsetWidth+'px';i.style.transform='translateX('+b.offsetLeft+'px)';}});
 function waitForShell(){
  if(start())return;
  // Homepage owns its shell. Observe only until its stable footer is available.
  const watcher=new MutationObserver(()=>{if(start()){watcher.disconnect();clearTimeout(expiry);}});
  const expiry=setTimeout(()=>watcher.disconnect(),30000);watcher.observe(document.body,{childList:true,subtree:true});
 }
 if(document.readyState!=='loading')waitForShell();else document.addEventListener('DOMContentLoaded',waitForShell,{once:true});
})();
