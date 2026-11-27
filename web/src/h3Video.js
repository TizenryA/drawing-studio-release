// ComfyUI nodes_minimax_h3.py: temporal_shape uses 17k+5 frames at 24 fps.
export const H3_VIDEO_NODES=['MiniMaxH3ReferenceToVideo','MiniMaxH3ImageToVideo'];
export function h3Mode(w){
 const nodes=Object.values(w?.workflow||{});
 const hasI2V=nodes.some(n=>n.class_type==='MiniMaxH3ImageToVideo');
 if(hasI2V)return nodes.some(n=>n.class_type==='LoadVideo')?'continue':'keyframes';
 if(nodes.some(n=>n.class_type==='MiniMaxH3ReferenceToVideo'))return 'ref';
 return null;
}
export const h3Video=w=>!!h3Mode(w);
export const videoResolutions=[['480p',864,480],['约 720p',1280,736],['768p',1344,768]];
export const videoDurations=[5,8,10,12,15];
export const continueFrameOptions=[[5,'5 帧（约 0.21s · 推荐）'],[22,'22 帧（约 0.92s · 强惯性）']];
export function videoOrientation(width,height,portrait=false){
 const short=Math.min(Number(width),Number(height));
 const long=width===height?Math.round(short*16/9/32)*32:Math.max(Number(width),Number(height));
 return portrait?[short,long]:[long,short];
}
export const frameInterpOptions=[['off','关'],['48','48fps'],['60','60fps']];
export const videoCrfOptions=[
 [12,'CRF 12 · 近无损母版（文件较大）'],
 [15,'CRF 15 · 极高画质（推荐）'],
 [18,'CRF 18 · 高画质均衡'],
 [22,'CRF 22 · 轻量压缩'],
];
export function parseVideoCrf(value,fallback=15){
 if(value==null||value==='')return fallback;
 const n=Number(value);
 if(!Number.isFinite(n))return fallback;
 return Math.max(0,Math.min(30,Math.round(n)));
}
export const seedvr2ResolutionOptions=[
 [720,'720p（快速 · 低显存）'],
 [1080,'1080p（高清推荐）'],
 [1440,'1440p / 2K（超清慢速）'],
];
export const seedvr2BatchOptions=[
 [5,'5 帧（均衡推荐）'],
 [9,'9 帧（时序更稳 · 需高显存）'],
 [13,'13 帧（长段稳态 · 仅 G4/A100）'],
];
export const seedvr2ColorOptions=[
 ['lab','LAB 色彩对齐（推荐）'],
 ['wavelet','Wavelet 小波保细节'],
 ['wavelet_adaptive','Wavelet 自适应饱和度'],
 ['none','关闭色彩校正'],
];
export function parseFrameInterp(value){
 if(value==48||value==='48'||value==='48fps'||value===2||value==='2x'||value==='2×')return 48;
 if(value==60||value==='60'||value==='60fps')return 60;
 return null;
}
export function videoTiming(seconds=5,fps=24){
 const length=5+17*Math.max(0,Math.ceil((Number(seconds)*fps-5)/17-1e-9));
 return {fps,length,actual_duration_seconds:length/fps};
}
export function computeKeyframeIndices(count,length,{continueMode=false,continueFrames=5,customFrames=null}={}){
 if(count<=0)return [];
 let auto;
 if(continueMode){
  if(count===1){auto=[length-1];}
  else{
   auto=Array.from({length:count},(_,i)=>Math.round((i+1)*(length-1)/count));
   const minIdx=Math.min(length-1,Number(continueFrames)||5);
   for(let i=0;i<count;i++){
    const floor=i===0?minIdx:Math.min(length-1,auto[i-1]+1);
    if(auto[i]<floor)auto[i]=floor;
   }
   auto[count-1]=length-1;
  }
 }else{
  auto=count===1?[0]:Array.from({length:count},(_,i)=>Math.round(i*(length-1)/(count-1)));
 }
 if(!Array.isArray(customFrames)||customFrames.length!==count)return auto;
 const minAllowed=continueMode?(Number(continueFrames)||5):0;
 return customFrames.map((raw,i)=>{
  if(raw==null||raw==='')return auto[i];
  let idx=Number(raw);
  if(!Number.isFinite(idx))return auto[i];
  idx=Math.round(idx);
  if(idx<0)idx=length+idx;
  return Math.max(minAllowed,Math.min(length-1,idx));
 });
}
export function keyframeRole(frameIdx,length,continueMode=false){
 if(!continueMode&&frameIdx===0)return '首帧';
 if(frameIdx===length-1)return '尾帧';
 return '关键帧';
}
export function videoPayload(params,workflow){
 const out={...params};
 const mode=h3Mode(workflow);
 if(mode){
  const original=Object.values(workflow.workflow).find(n=>H3_VIDEO_NODES.includes(n.class_type)).inputs;
  const timing=out.duration_seconds!=null?videoTiming(out.duration_seconds):{fps:24,length:5+17*Math.max(0,Math.ceil(((out.length??original.length??124)-5)/17-1e-9))};
  const frame_interp=parseFrameInterp(out.frame_interp);
  const next={...out,...timing,fps:24,actual_duration_seconds:timing.length/24};
  if(frame_interp){next.frame_interp=frame_interp;next.output_fps=frame_interp;}
  else{delete next.frame_interp;delete next.output_fps;}
  if(out.video_crf!=null&&out.video_crf!==''){next.video_crf=parseVideoCrf(out.video_crf,15);}
  else{delete next.video_crf;}
  if(mode==='ref'){
   for(const key of ['keyframes','keyframe_frames','source_video','source_video_id','continue_frames'])delete next[key];
  }else{
   delete next.ref_images;
   if(mode==='continue'){
    next.continue_frames=Number(next.continue_frames)||5;
   }else{
    for(const key of ['source_video','source_video_id','continue_frames'])delete next[key];
   }
   if(Array.isArray(next.keyframes)){
    next.keyframe_frames=computeKeyframeIndices(next.keyframes.length,timing.length,{
     continueMode:mode==='continue',
     continueFrames:next.continue_frames||5,
     customFrames:Array.isArray(next.keyframe_frames)&&next.keyframe_frames.length===next.keyframes.length?next.keyframe_frames:null,
    });
   }else{
    delete next.keyframe_frames;
   }
  }
  return next;
 }
 const isVideoSeedvr2=workflow?.mode==='video_seedvr2'||out.mode==='video_seedvr2'||(out.workflow||out.workflow_id)==='video-seedvr2';
 if(isVideoSeedvr2){
  const next={...out,mode:'video_seedvr2',workflow:'video-seedvr2',workflow_id:'video-seedvr2'};
  for(const key of ['duration_seconds','fps','length','actual_duration_seconds','ref_images','keyframes','keyframe_frames','continue_frames','frame_interp','output_fps'])delete next[key];
  next.video_crf=parseVideoCrf(next.video_crf,15);
  next.free_gpu_before_run=next.free_gpu_before_run!==false;
  return next;
 }
 // Leave unrelated video workflows alone; strip stale H3 state from image jobs.
 if(!Object.values(workflow?.workflow||{}).some(n=>['CreateVideo','SaveVideo','VHS_VideoCombine'].includes(n.class_type))){
  for(const key of ['duration_seconds','fps','length','actual_duration_seconds','ref_images','keyframes','keyframe_frames','source_video','source_video_id','continue_frames','frame_interp','output_fps','video_crf'])delete out[key];
 }
 return out;
}

export function h3References(params){
 if(Array.isArray(params.ref_images))return [...params.ref_images];
 const image=params.image||params.input_image;
 return image&&!/^__.*__$/.test(image)?[image]:[];
}
export function h3Keyframes(params,continueMode=false){
 if(Array.isArray(params.keyframes))return [...params.keyframes];
 if(continueMode)return [];
 const out=[];
 for(const v of [params.image||params.input_image,params.last_image]){
  if(v&&!/^__.*__$/.test(v)&&!['first_frame.png','last_frame.png'].includes(v))out.push(v);
 }
 return out;
}
export function moveReference(paths,index,delta){
 const next=[...paths],target=index+delta;
 if(target<0||target>=next.length)return next;
 [next[index],next[target]]=[next[target],next[index]];return next;
}
export const referenceThumbnail=path=>{const s=String(path??'');return /^(data:|blob:|https?:\/\/)/i.test(s)?s:'/api/uploads/'+encodeURIComponent(s.replace(/\\/g,'/').split('/').pop());};
