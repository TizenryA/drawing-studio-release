import {test} from 'node:test';
import assert from 'node:assert/strict';
import {h3References,moveReference,referenceThumbnail,videoPayload} from './h3Video.js';
import {workflowParams} from './taskData.js';
test('references keep duplicate order, delete/clear, legacy and image isolation',()=>{
 assert.deepEqual(h3References({image:'old.png'}),['old.png']);assert.deepEqual(h3References({image:'__reference_image__'}),[]);assert.deepEqual(h3References({image:'old.png',ref_images:[]}),[]);
 assert.deepEqual(moveReference(['a','b','a'],1,-1),['b','a','a']);
 assert.deepEqual(moveReference(['a','b'],0,1),['b','a']);
 assert.equal(referenceThumbnail('uploads/a b.png'),'/api/uploads/a%20b.png');
 assert.equal('ref_images' in videoPayload({ref_images:['a']},{workflow:{}}),false);
});
test('workflow imports native references in numeric Picture order',()=>{
 const w={id:'h3',workflow:{'8':{class_type:'MiniMaxH3ReferenceToVideo',inputs:{width:864,height:480,length:124,'ref_images.ref_image_1':['19',0],'ref_images.ref_image_0':['18',0]}},'18':{class_type:'LoadImage',inputs:{image:'a.png'}},'19':{class_type:'LoadImage',inputs:{image:'b.png'}}}};
 assert.deepEqual(workflowParams(w,[]).ref_images,['a.png','b.png']);w.workflow['18'].inputs.image='__reference_image__';assert.deepEqual(workflowParams(w,[]).ref_images,['b.png']);
});
