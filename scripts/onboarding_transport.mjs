// Isolated exercise only: all external adapters are in-memory fakes.
import assert from 'node:assert/strict';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {mkdirSync,cpSync,copyFileSync,writeFileSync} from 'node:fs';
const [bridgeDir,work]=process.argv.slice(2);
const {Store}=await import(pathToFileURL(resolve(bridgeDir,'src/store.js')));
const {Bridge}=await import(pathToFileURL(resolve(bridgeDir,'src/bridge.js')));
mkdirSync(work,{recursive:true});
const state=join(work,'state'),backup=join(work,'backup'),restored=join(work,'restored');
let store=new Store(state);const posted=[],sent=[];
const config={wecomBotId:'demo-bot',ownerUser:'demo-owner',allowedChats:new Set(['demo-group']),slackChannel:'CDEMO',replyBot:'BRESPONDER',ttlMs:3600000,maxBytes:32000};
const deps={config,now:()=>100000,wait:async()=>{},slack:{post:async p=>{posted.push(p);return{ok:true,ts:'1.1'}}},wecom:{ack:async()=>({errcode:0}),send:async(target,text)=>{sent.push({target,text});return{errcode:0}}}};
let bridge=new Bridge({...deps,store});bridge.wecomReady=true;
assert(bridge.receiveWecom({body:{aibotid:'demo-bot',msgid:'example-1',msgtype:'text',chattype:'group',chatid:'demo-group',from:{userid:'demo-member'},text:{content:'导出只到第一页，预期全部结果。'}}}));await bridge.drain();assert.equal(posted.length,1);
// A coordinator fixture creates a local draft; the bridge does not call GitHub.
const draft={id:'EXAMPLE-1',title:'导出只到第一页',decision:'待评估',issue_state:'open',implementation_authorized:false,writeback:'local_json_only'};
writeFileSync(join(work,'issue-draft.json'),JSON.stringify(draft,null,2));
assert(bridge.receiveSlack({type:'message',channel:'CDEMO',bot_id:'BRESPONDER',ts:'1.2',thread_ts:'1.1',text:'[wecom:group]已登记为蜂巢 EXAMPLE-1：导出问题，待负责人评估。'}));await bridge.drain();assert.equal(sent.length,1);assert.equal(sent[0].target,'demo-group');
const request=store.get('SELECT id FROM requests').id;
store.addJob(request,'wecom',{text:'synthetic unfinished delivery'},'group','demo-group');store.run("UPDATE jobs SET status='sending' WHERE status='pending'");
await bridge.stop();store.close();cpSync(state,backup,{recursive:true});
store=new Store(state);assert.equal(store.get("SELECT count(*) n FROM jobs WHERE status='unknown'").n,1);
store.db.exec('CREATE TABLE exercise_notes(note TEXT)');store.run('INSERT INTO exercise_notes VALUES (?)','created-after-backup');store.close();
// Load the same storage code from two release directories; preserve current data on return.
for(const name of ['release-a','release-b']){
  mkdirSync(join(work,name));
  copyFileSync(join(bridgeDir,'src/store.js'),join(work,name,'store.mjs'));
}
for(const name of ['release-a','release-b','release-a']){
  const {Store:ReleaseStore}=await import(pathToFileURL(resolve(work,name,'store.mjs')));
  store=new ReleaseStore(state);
  assert.equal(store.get('SELECT note FROM exercise_notes').note,'created-after-backup');
  store.close();
}
store=new Store(state);
bridge=new Bridge({...deps,store});bridge.wecomReady=true;await bridge.drain();assert.equal(sent.length,1);await bridge.stop();store.close();
cpSync(backup,restored,{recursive:true});const copy=new Store(restored);assert.equal(copy.get('PRAGMA quick_check').quick_check,'ok');assert.equal(copy.get("SELECT count(*) n FROM jobs WHERE status='unknown'").n,1);copy.close();
console.log(JSON.stringify({feedback_transport:'passed_with_fakes',issue_registration:'local_json_simulation_only',sending_recovery:'unknown_not_replayed',backup_copy:'integrity_ok',same_version_store_code_switch:'current_new_data_preserved',real_network_calls:0,cross_version_rollback:'not_tested'}));
