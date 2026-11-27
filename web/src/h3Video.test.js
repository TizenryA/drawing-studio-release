import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowParams} from './taskData.js';
import {h3Video,h3Mode,videoTiming,videoPayload,videoResolutions,videoOrientation,computeKeyframeIndices,keyframeRole,parseVideoCrf} from './h3Video.js';
const w={id:'arbitrary-id',mode:'custom',mapping:{},workflow:{'8':{class_type:'MiniMaxH3ReferenceToVideo',inputs:{width:864,height:480,length:124,prompt:'original'}},'9':{class_type:'KSampler',inputs:{steps:8}},'10':{class_type:'CreateVideo',inputs:{fps:24}}}};
test('square can return to horizontal or portrait video',()=>{
 assert.deepEqual(videoOrientation(480,480),[864,480]);
 assert.deepEqual(videoOrientation(480,480,true),[480,864]);
 assert.deepEqual(videoOrientation(640,384,true),[384,640]);
});
test('original H3 graph provides controls and defaults without manifest mappings',()=>{
 const p=workflowParams(w,[]);
 assert.ok(h3Video(w));assert.equal(p.steps,8);assert.equal(p.width,864);assert.equal(p.height,480);assert.equal(p.length,124);assert.equal(p.fps,24);assert.equal(p.duration_seconds,5);
 const image=workflowParams({id:'image',workflow:{}},[]);
 assert.equal(h3Video({workflow:{}}),false);for(const key of ['duration_seconds','length','fps'])assert.equal(image[key],undefined);
});
test('duration presets follow official upward alignment and keep requested duration',()=>{
 for(const [seconds,length] of [[5,124],[8,192],[10,243],[12,294],[15,362]]){
  const p=videoPayload({workflow:w.id,duration_seconds:seconds,fps:24,width:864,height:480},w);
  assert.equal(p.length,length);assert.equal(p.duration_seconds,seconds);assert.equal(p.actual_duration_seconds,length/24);assert.equal(videoTiming(seconds).length,length);
 }
});
test('image submission strips stale video fields; H3 snapshot reuse is preserved',()=>{
 const p={duration_seconds:12,fps:24,length:294,actual_duration_seconds:294/24,width:640,height:960};
 assert.deepEqual(videoPayload(p,w),p);
 assert.deepEqual(videoPayload(p,{workflow:{}}),{width:640,height:960});
 assert.deepEqual(videoResolutions.map(x=>x.slice(1)),[[864,480],[1280,736],[1344,768]]);
});

test('nonpreset imported length and BasicScheduler survive unchanged submission',()=>{
 const custom=structuredClone(w);custom.workflow['8'].inputs.length=175;custom.workflow['9']={class_type:'BasicScheduler',inputs:{steps:11}};
 const p=workflowParams(custom,[]);assert.equal(p.steps,11);assert.equal(p.length,175);assert.equal(videoPayload(p,custom).length,175);
 custom.workflow['8'].inputs.length=120;assert.equal(workflowParams(custom,[]).length,124);
});

test('frame interpolation is optional and does not change H3 length math',()=>{
 const off=videoPayload({workflow:w.id,duration_seconds:5,width:864,height:480},w);
 assert.equal(off.length,124);assert.equal(off.fps,24);assert.equal(off.frame_interp,undefined);
 const on=videoPayload({workflow:w.id,duration_seconds:15,frame_interp:60,width:640,height:960},w);
 assert.equal(on.length,362);assert.equal(on.fps,24);assert.equal(on.frame_interp,60);assert.equal(on.output_fps,60);assert.equal(on.actual_duration_seconds,362/24);
 const stripped=videoPayload({duration_seconds:15,frame_interp:48,output_fps:48,width:640,height:960},{workflow:{}});
 assert.deepEqual(stripped,{width:640,height:960});
});

test('Route A keyframes mode computes timeline anchors and strips stale ref fields',()=>{
 const kfWf={id:'h3-fl2va-keyframes-8step-bf16',mode:'img2img',mapping:{},workflow:{
  '1':{class_type:'LoadImage',inputs:{image:'first_frame.png'}},
  '2':{class_type:'LoadImage',inputs:{image:'last_frame.png'}},
  '20':{class_type:'MiniMaxH3ImageToVideo',inputs:{width:544,height:960,length:124,prompt:'go',first_frame:['1',0],last_frame:['2',0]}},
  '22':{class_type:'BasicScheduler',inputs:{steps:8}},
  '28':{class_type:'CreateVideo',inputs:{fps:24}},
 }};
 assert.equal(h3Mode(kfWf),'keyframes');
 assert.ok(h3Video(kfWf));
 const init=workflowParams(kfWf,[]);
 assert.deepEqual(init.keyframes,[]);
 assert.deepEqual(computeKeyframeIndices(4,362),[0,120,241,361]);
 assert.equal(keyframeRole(0,362,false),'首帧');
 assert.equal(keyframeRole(120,362,false),'关键帧');
 assert.equal(keyframeRole(361,362,false),'尾帧');
 const payload=videoPayload({workflow:kfWf.id,duration_seconds:15,keyframes:['a.png','b.png','c.png','d.png'],ref_images:['stale.png']},kfWf);
 assert.deepEqual(payload.keyframe_frames,[0,120,241,361]);
 assert.equal(payload.ref_images,undefined);
 assert.equal(payload.source_video,undefined);
});

test('Route B continue mode keeps overlap frames and schedules target keyframes after prefix',()=>{
 const contWf={id:'h3-fl2va-continue-8step-int8',mode:'img2img',mapping:{},workflow:{
  '1':{class_type:'LoadVideo',inputs:{file:'source_clip.mp4'}},
  '2':{class_type:'GetVideoComponents',inputs:{video:['1',0]}},
  '3':{class_type:'ImageFromBatch',inputs:{image:['2',0],batch_index:-5,length:5}},
  '20':{class_type:'MiniMaxH3ImageToVideo',inputs:{width:544,height:960,length:124,prompt:'continue'}},
  '30':{class_type:'MiniMaxH3AddGuide',inputs:{positive:['20',0],latent:['20',1],image:['3',0],frame_idx:0}},
  '22':{class_type:'BasicScheduler',inputs:{steps:8}},
  '28':{class_type:'CreateVideo',inputs:{fps:24}},
 }};
 assert.equal(h3Mode(contWf),'continue');
 const init=workflowParams(contWf,[]);
 assert.equal(init.continue_frames,5);
 assert.equal(init.source_video,'');
 const payload=videoPayload({workflow:contWf.id,duration_seconds:5,source_video:'part1.mp4',continue_frames:22,keyframes:['mid.png','end.png']},contWf);
 assert.equal(payload.continue_frames,22);
 assert.deepEqual(payload.keyframe_frames,[62,123]);
 assert.equal(keyframeRole(62,124,true),'关键帧');
 assert.equal(keyframeRole(123,124,true),'尾帧');
 const stripped=videoPayload(payload,{workflow:{}});
 assert.equal(stripped.keyframes,undefined);
 assert.equal(stripped.source_video,undefined);
 assert.equal(stripped.continue_frames,undefined);
});

test('parseVideoCrf clamps bounds and videoPayload keeps video-seedvr2 settings',()=>{
 assert.equal(parseVideoCrf(undefined),15);
 assert.equal(parseVideoCrf('12'),12);
 assert.equal(parseVideoCrf(99),30);
 assert.equal(parseVideoCrf(-5),0);
 const h3WithCrf=videoPayload({workflow:w.id,duration_seconds:5,width:864,height:480,video_crf:12},w);
 assert.equal(h3WithCrf.video_crf,12);
 const seedvr2Payload=videoPayload({
  mode:'video_seedvr2',
  workflow:'video-seedvr2',
  source_video:'clip.mp4',
  source_video_id:'vid123',
  resolution:1080,
  frame_batch_size:5,
  temporal_overlap:2,
  color_correction:'lab',
  blocks_to_swap:0,
  video_crf:15,
  duration_seconds:8,
  length:192,
  frame_interp:60,
  ref_images:['a.png'],
 },{id:'video-seedvr2',mode:'video_seedvr2',builtin:true});
 assert.equal(seedvr2Payload.mode,'video_seedvr2');
 assert.equal(seedvr2Payload.source_video,'clip.mp4');
 assert.equal(seedvr2Payload.source_video_id,'vid123');
 assert.equal(seedvr2Payload.video_crf,15);
 assert.equal(seedvr2Payload.free_gpu_before_run,true);
 assert.equal(seedvr2Payload.duration_seconds,undefined);
 assert.equal(seedvr2Payload.frame_interp,undefined);
 assert.equal(seedvr2Payload.ref_images,undefined);
});
