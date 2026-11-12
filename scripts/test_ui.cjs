const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'console/ui.js'),'utf8');
const css=fs.readFileSync(path.join(root,'console/ui.css'),'utf8');

const sortBody=source.split('const sortedServices=')[1].split('const eventPages=')[0].trim().replace(/;$/,'');
const sort=Function('return ('+sortBody+')')();

const backupBody=source.split('const backupUi=')[1].split('/* backup-ui-end */')[0].trim().replace(/;$/,'');
const backupUi=Function('return ('+backupBody+')')();

const rows=[
  {id:'none',name:'无数据',availability:null,coverage:0},
  {id:'up',name:'正常',availability:100,coverage:100},
  {id:'a',name:'Alpha',availability:90,coverage:20},
  {id:'b',name:'Beta',availability:90,coverage:20},
  {id:'low',name:'故障',availability:30,coverage:100},
  {id:'covered',name:'覆盖',availability:90,coverage:80},
];
assert.deepEqual(sort(rows).map(x=>x.id),['low','a','b','covered','up','none']);
assert.equal(rows[0].id,'none');
assert(source.indexOf('items=sortedServices(items)')<source.indexOf('items.slice((listPage-1)*size'));

assert.equal(backupUi.isAbnormal({state:'up',backup:{transport:{state:'down'}}}),true);
assert.equal(backupUi.effectiveState({state:'up',backup:{transport:{state:'down'}}}),'down');
assert.equal(backupUi.isAbnormal({state:'up',backup:{transport:{state:'up'}}}),false);
assert.equal(backupUi.isAbnormal({state:'maintenance',backup:{transport:{state:'down'}}}),true);
assert.equal(backupUi.isAbnormal({state:'degraded'}),true);

assert.equal(backupUi.snapshotHeading({
  state:'up',backup:{snapshot_id:'current',metadata_state:'valid'}}),'当前已检查快照');
assert.equal(backupUi.snapshotHeading({
  state:'down',backup:{snapshot_id:'last',metadata_state:'valid'}}),'上次有效快照');
assert.equal(backupUi.snapshotHeading({
  state:'unknown',backup:{snapshot_id:'last',metadata_state:'missing'}}),'上次有效快照');
assert.equal(backupUi.snapshotHeading({
  state:'unknown',backup:{metadata_state:'missing'}}),'尚无有效快照');

assert.match(backupUi.metadataNotice({metadata_state:'valid'}),/有效/);
assert.match(backupUi.metadataNotice({metadata_state:'missing',last_valid_at:1}),/上次有效快照/);
assert.match(backupUi.metadataNotice({metadata_state:'invalid',last_valid_at:1}),/无效.*上次有效快照/);
assert.match(backupUi.metadataNotice({metadata_state:'missing'}),/没有可展示的有效快照/);

const emptyMessages=[
  backupUi.emptyMessage(0,0,false),
  backupUi.emptyMessage(1,0,false),
  backupUi.emptyMessage(1,1,true),
];
assert.equal(new Set(emptyMessages).size,3);
assert.match(emptyMessages[0],/尚未登记/);
assert.match(emptyMessages[1],/筛选条件/);
assert.match(emptyMessages[2],/尚未收到任何安全元数据/);

assert(source.includes("fetch('/_ops/events?'"));
assert(source.includes("params.set('before',state.before)"));
assert(source.includes("['backups','备份']"));
assert(source.includes("for(const name of ['服务入口','服务器','备份','告警'])"));
assert(source.includes("backupUi.isAbnormal(s)"));
assert(source.includes("backupUi.emptyMessage("));
assert(source.includes("if(!s.is_backup)"));
assert(source.includes("上传通路断开"));
assert(source.includes("回执已过期"));
assert(source.includes("本次未收到安全元数据"));
assert(source.includes("本次安全元数据无效"));
for(const forbidden of ['立即备份','恢复快照','删除快照','恢复备份'])assert(!source.includes(forbidden));

assert(!css.includes("calc((100% - 8px)/3)"));
assert(css.includes(".ops-tab{flex:1;padding:10px 13px}"));
console.log('PASS: UI ordering, backup abnormalities, metadata states, empty states and read-only controls');
