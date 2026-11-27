export const IS_DEMO = __DEMO_MODE__;
export const resetDemo = async () => {
  if (__DEMO_MODE__) {
    const m = await import('./demoApi.js');
    m.resetDemoState();
  }
};
export const list = value => Array.isArray(value) ? value : (value?.items || value?.data || []);
export const enc = value => encodeURIComponent(value);
export async function api(path, options = {}) {
  if (__DEMO_MODE__) {
    const m = await import('./demoApi.js');
    return m.demoApi(path, options);
  }
  const controller=path==='/api/runtime'?new AbortController():null;
  const timer=controller?setTimeout(()=>controller.abort(),20000):null;
  try {
  let response;
  try { response = await fetch(path, {credentials:'include',...(controller?{signal:controller.signal}:{}),...options,headers:{...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}),...options.headers}}); }
  catch { throw new Error('无法连接后端，请检查网络与服务状态。'); }
  const isJson = response.headers.get('content-type')?.includes('application/json');
  const data = response.status === 204 ? null : isJson ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('studio:unauthorized'));
    const detail = data?.detail ?? data?.error?.message ?? data?.message;
    throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `请求失败（HTTP ${response.status}）${typeof data==='string' && data.length<200 ? '：'+data : ''}`);
  }
  if (!isJson && response.status!==204) throw new Error('后端返回了非 JSON 内容，请检查 API 代理配置。');
  return data;
  } finally { if(timer)clearTimeout(timer); }
}
export const post = (path,body={}) => api(path,{method:'POST',body:JSON.stringify(body)});
export const patch = (path,body) => api(path,{method:'PATCH',body:JSON.stringify(body)});
export const remove = path => api(path,{method:'DELETE'});
export async function upload(file) {
  if (__DEMO_MODE__) {
    const m = await import('./demoApi.js');
    return m.demoUpload(file);
  }
  const body=new FormData();body.append('file',file);return api('/api/uploads',{method:'POST',body});
}
export async function download(path,name) {
  if (__DEMO_MODE__) {
    const m = await import('./demoApi.js');
    return m.demoDownload(path,name);
  }
  const response=await fetch(path,{credentials:'include'});
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(detail||`下载失败（HTTP ${response.status}）`);}
  const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=name||'drawing-studio.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
}
