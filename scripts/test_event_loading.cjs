const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../console/ui.js'),'utf8');
class Element{
 constructor(tag,cls,text){this.tag=tag;this.cls=cls;this.textContent=text;this.children=[];this.attrs={};}
 append(...xs){this.children.push(...xs);}
 replaceChildren(...xs){this.children=xs;}
 setAttribute(k,v){this.attrs[k]=v;}
 querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)]);}
 querySelector(){return null;}
}
const el=(...args)=>new Element(...args),button=(text,action)=>Object.assign(el('button','',text),{onclick:action});
const requests=[];
const fetch=()=>new Promise((resolve,reject)=>requests.push({resolve,reject}));
const eventList=items=>{const n=el('div','events');n.items=items;return n;};
const data={window_start:0,events:[{id:1,time:1,service:'demo',old:'up',state:'down'}]};
const code=source.slice(source.indexOf(' const eventPages='),source.indexOf(' function renderSummary()'));
const {pagedEvents,eventPages}=Function('el','button','eventList','data','windowKey','fetch','AbortController',code+';return {pagedEvents,eventPages};')(el,button,eventList,data,'24h',fetch,AbortController);
const tick=()=>new Promise(r=>setImmediate(r));
const result=(page,id)=>({ok:true,json:async()=>({items:[{id}],page,pages:3,total:31,before:100})});
(async()=>{
 const first=pagedEvents();assert.equal(first.children[0].items[0].id,1,'snapshot seeds first frame');
 assert.strictEqual(pagedEvents(),first,'tab reuses same DOM');assert.equal(requests.length,1,'pending request is shared');
 requests[0].resolve(result(1,10));await tick();const state=eventPages.get('24h|');assert.equal(state.page,1);
 const children=first.children;state.loadedAt=0;pagedEvents();assert.strictEqual(first.children,children,'background refresh retains visible rows');
 requests[1].resolve(result(1,11));await tick();
 const older=state.show(2),newer=state.show(3);requests[3].resolve(result(3,30));await newer;requests[2].resolve(result(2,20));await older;
 assert.equal(state.page,3,'late response cannot overwrite newer page');assert.equal(first.children[0].items[0].id,30);
 const failed=state.show(2);requests[4].reject(Error('offline'));await failed;
 assert.equal(first.children[0].items[0].id,30,'failure retains loaded records');
 console.log('PASS: seeded first frame, DOM reuse, request dedupe, background retention, stale-response guard and failure retention');
})().catch(e=>{console.error(e);process.exitCode=1;});
