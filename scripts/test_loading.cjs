const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../console/ui.js'),'utf8');
const code=source.slice(source.indexOf(' async function load(background=false)'),source.indexOf(' function renderSkeleton()'));
const requests=[],renders=[],timers=[];
const response=value=>({ok:true,status:200,json:async()=>value});
const fetch=(url,options)=>new Promise((resolve,reject)=>{requests.push({url,options,resolve,reject});});
const create=Function('fetch','AbortController','AbortSignal','setTimeout','clearTimeout','renders',`
 let busy=false,loadController,loadGeneration=0,windowKey='24h',sessionGeneration=0,householdData={},data=null,pendingRender=false;
 const document={activeElement:{}},root={contains:()=>false,setAttribute:()=>{}},notice={textContent:''},latencyCache=new Map();
 const body={replaceChildren:()=>{},append:()=>{}},summary={replaceChildren:()=>{}},toolbar={replaceChildren:()=>{},querySelector:()=>null};
 const renderSummary=()=>renders.push('summary'),pagedEvents=()=>{},renderDownloads=()=>{},renderBody=()=>renders.push('body'),renderToolbar=()=>{};
 const renderSkeleton=()=>{},el=()=>{},button=()=>{},loadHousehold=()=>renders.push('household');
 const expireSession=()=>{sessionGeneration++;loadGeneration++;busy=false;data=null;};
 ${code}
 return {load,inspect:()=>({data,busy,pendingRender,notice:notice.textContent}),window:value=>windowKey=value};
`);
const app=create(fetch,AbortController,AbortSignal,callback=>{timers.push(callback);return timers.length;},()=>{},renders);
(async()=>{
 const first=app.load();assert.equal(requests.length,1);assert.equal(renders.length,0);
 await app.load(true);assert.equal(requests.length,1,'background deduplication');
 requests[0].resolve(response({id:1}));await first;assert.equal(app.inspect().data.id,1);assert.ok(renders.includes('body'));assert.ok(renders.includes('household'),'household starts after main render without awaiting it');
 const old=app.load();app.window('7d');const latest=app.load();assert.equal(requests[1].options.signal.aborted,true);
 requests[2].resolve(response({id:3}));await latest;requests[1].resolve(response({id:2}));await old;
 assert.equal(app.inspect().data.id,3,'late previous window cannot overwrite selection');assert.equal(app.inspect().busy,false);
 const denied=app.load();requests[3].resolve({status:401});await denied;assert.equal(app.inspect().data,null,'expired session clears data');
 console.log('PASS: main-first rendering, background dedupe, cancellation, window race and session expiry');
})().catch(error=>{console.error(error);process.exitCode=1;});
