import assert from 'node:assert/strict';
import {test} from 'node:test';
import {setLoraStrength,removeLora,toggleLora,loraPickerRows,matchesLora} from './loraSelection.js';

const imported={node_id:'0',id:'same',name:'same.safetensors',strength:.8,strength_model:.8,strength_clip:.3};
test('weight edits synchronize zero and negative strengths without mutating source',()=>{
 for(const strength of [0,-.4,.7]){
  const next=setLoraStrength(imported,strength);
  assert.deepEqual([next.strength,next.strength_model,next.strength_clip],[strength,strength,strength]);
  assert.equal(next.node_id,'0');
 }
 assert.equal(imported.strength_clip,.3);
});
test('removing an imported duplicate disables only its node and preserves sibling',()=>{
 const sibling={...imported,node_id:'1'};
 const next=removeLora([imported,sibling],imported);
 assert.equal(next.length,2);
 assert.deepEqual(next[0],{...imported,enabled:false,strength:0,strength_model:0,strength_clip:0});
 assert.strictEqual(next[1],sibling);
 assert.equal(imported.enabled,undefined);
});
test('ordinary LoRA is removed from selection',()=>{
 const plain={id:'plain',strength:.8};
 assert.deepEqual(removeLora([imported,plain],plain),[imported]);
});
test('picker disables and restores one same-model slot while its sibling stays active',()=>{
 const sibling={...imported,node_id:'1',strength:.3,strength_model:.3};
 const target={id:'same',node_id:'1',recommended_weight:.65};
 let next=toggleLora([imported,sibling],target);
 assert.strictEqual(next[0],imported);
 assert.equal(next[1].enabled,false);
 next=toggleLora(next,target);
 assert.strictEqual(next[0],imported);
 assert.equal(next.length,2);
 assert.deepEqual([next[1].node_id,next[1].enabled,next[1].strength_model,next[1].strength_clip],['1',true,.65,.65]);
});
test('picker disable and re-enable keep imported identity without accumulating records',()=>{
 let next=[imported];
 for(let i=0;i<3;i++){
  next=toggleLora(next,{id:'same',node_id:'0',recommended_weight:0});
  assert.equal(next[0].enabled,false);
  next=toggleLora(next,{id:'same',node_id:'0',recommended_weight:0});
  assert.equal(next.length,1);
  assert.equal(next[0].node_id,'0');
  assert.equal(next[0].enabled,true);
  assert.deepEqual([next[0].strength,next[0].strength_model,next[0].strength_clip],[0,0,0]);
 }
});
test('ordinary add and remove do not alter imported slots of the same model',()=>{
 const sibling={...imported,node_id:'1'},plain={id:'same',name:'Plain',recommended_weight:.6};
 const next=toggleLora([imported,sibling],plain);
 assert.strictEqual(next[0],imported);
 assert.strictEqual(next[1],sibling);
 assert.equal(next[2].strength_model,.6);
 assert.equal(next[2].node_id,undefined);
 assert.deepEqual(toggleLora(next,plain),[imported,sibling]);
});
test('picker rows retain each imported node including disabled and unindexed slots',()=>{
 const models=[{id:'same',name:'Indexed'},{id:'plain',name:'Plain'}];
 const sibling={...imported,node_id:'1',enabled:false};
 const missing={id:'missing',node_id:'2',name:'Missing'};
 const rows=loraPickerRows(models,[imported,sibling,missing]);
 assert.deepEqual(rows.map(r=>[r.id,r.node_id]),[['same','0'],['same','1'],['plain',undefined],['missing','2']]);
 assert.equal(matchesLora(imported,rows[0]),true);
 assert.equal(matchesLora(imported,rows[1]),false);
 assert.equal(matchesLora({...imported,node_id:0},rows[0]),true);
 assert.equal(matchesLora(imported,models[0]),false);
});
