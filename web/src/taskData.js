import { defaultParams } from './defaultParams.js';
import { h3Mode, H3_VIDEO_NODES } from './h3Video.js';

export const validTasks = tasks =>
  tasks
    .filter(t => t.status !== 'completed' || t.results?.length)
    .sort((a, b) =>
      String(b.updated_at || b.finished_at || b.created_at || '').localeCompare(
        String(a.updated_at || a.finished_at || a.created_at || ''),
      ),
    );

const TERMINAL_TASK_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'error']);

export const isRawTaskActive = t => !!t && !TERMINAL_TASK_STATUSES.has(t.status);

export function normalizeTasksResponse(payload, fallbackPage = 1, fallbackLimit = 20) {
  if (Array.isArray(payload)) {
    const total = payload.length;
    const limit = Math.max(1, Number(fallbackLimit) || 20);
    const totalPages = Math.max(1, Math.ceil(total / limit));
    const page = Math.min(totalPages, Math.max(1, Number(fallbackPage) || 1));
    return {
      items: payload,
      activeItems: payload.filter(isRawTaskActive),
      total,
      page,
      limit,
      totalPages,
      hasMore: page < totalPages,
      stats: null,
      models: null,
    };
  }
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const activeItems = Array.isArray(payload?.active_items) ? payload.active_items : [];
  const limit = Math.max(1, Number(payload?.limit ?? fallbackLimit) || 20);
  const total = Math.max(items.length, Number(payload?.total ?? items.length) || 0);
  const totalPages = Math.max(1, Number(payload?.total_pages ?? payload?.totalPages ?? Math.ceil(total / limit)) || 1);
  const page = Math.min(totalPages, Math.max(1, Number(payload?.page ?? fallbackPage) || 1));
  return {
    items,
    activeItems,
    total,
    page,
    limit,
    totalPages,
    hasMore: Boolean(payload?.has_more ?? payload?.hasMore ?? page < totalPages),
    stats: payload?.stats || null,
    models: Array.isArray(payload?.models) ? payload.models : null,
  };
}

export function upsertTaskList(existing = [], incoming, maxItems = 40) {
  const additions = Array.isArray(incoming) ? incoming : incoming ? [incoming] : [];
  const byId = new Map();
  for (const item of existing || []) {
    if (item?.id) byId.set(item.id, item);
  }
  for (const item of additions) {
    if (!item?.id) continue;
    const prev = byId.get(item.id);
    byId.set(item.id, prev ? { ...prev, ...item } : item);
  }
  const sorted = Array.from(byId.values()).sort((a, b) => {
    const actDiff = Number(isRawTaskActive(b)) - Number(isRawTaskActive(a));
    if (actDiff !== 0) return actDiff;
    return String(b?.updated_at || b?.finished_at || b?.created_at || '').localeCompare(
      String(a?.updated_at || a?.finished_at || a?.created_at || ''),
    );
  });
  const active = sorted.filter(isRawTaskActive);
  const inactive = sorted.filter(t => !isRawTaskActive(t));
  const keepInactive = Math.max(0, maxItems - active.length);
  return [...active, ...inactive.slice(0, keepInactive)];
}

export function workflowParams(w, models) {
  const graph = w.workflow || {};
  const out = {
    ...defaultParams,
    loras: [],
    upscale: { enabled: false, mode: 'off' },
    workflow: w.id,
    workflow_id: w.id,
    mode: w.mode || 'custom',
  };
  const resolve = v =>
    models.find(m => [m.id, m.name, m.filename, m.comfy_name].includes(v))?.id || v;

  for (const [path, target] of Object.entries(w.mapping || {})) {
    const t = Array.isArray(target) ? target[0] : target;
    const [node, input] = typeof t === 'string' ? t.split('.') : [t.node, t.input];
    const value = graph[node]?.inputs?.[input];
    if (value === undefined || Array.isArray(value)) continue;
    const keys = path.split('.');
    let obj = out;
    for (const k of keys.slice(0, -1)) obj = obj[k] ??= {};
    obj[keys.at(-1)] = value;
  }

  for (const [nodeId, node] of Object.entries(graph)) {
    const i = node.inputs || {};
    if (['CheckpointLoaderSimple', 'UNETLoader'].includes(node.class_type)) {
      out.model = resolve(i.ckpt_name || i.unet_name);
    }
    if (node.class_type === 'VAELoader') out.vae = resolve(i.vae_name);
    if (node.class_type === 'ModelSamplingAuraFlow' && out.shift === undefined && typeof i.shift === 'number') {
      out.shift = i.shift;
    }
    if (['LoraLoader', 'LoraLoaderModelOnly'].includes(node.class_type)) {
      out.loras.push({
        node_id: nodeId,
        id: resolve(i.lora_name),
        name: i.lora_name,
        strength: i.strength_model ?? i.strength ?? 0.8,
        strength_model: i.strength_model ?? 0.8,
        strength_clip: i.strength_clip ?? 0.8,
      });
    }
  }

  const mode = h3Mode(w);
  if (mode) {
    const i = Object.values(graph).find(n => H3_VIDEO_NODES.includes(n.class_type)).inputs;
    if (mode === 'ref') {
      out.ref_images = Object.entries(i)
        .filter(([key]) => /^ref_images\.ref_image_\d+$/.test(key))
        .sort(([a], [b]) => Number(a.split('_').pop()) - Number(b.split('_').pop()))
        .map(([, link]) => graph[link?.[0]]?.inputs?.image)
        .filter(v => v && !/^__.*__$/.test(v));
    } else if (mode === 'keyframes') {
      out.keyframes = [];
      out.keyframe_frames = undefined;
      const firstImg = graph[i.first_frame?.[0]]?.inputs?.image;
      const lastImg = graph[i.last_frame?.[0]]?.inputs?.image;
      for (const v of [firstImg, lastImg]) {
        if (v && !/^__.*__$/.test(v) && !['first_frame.png', 'last_frame.png'].includes(v)) {
          out.keyframes.push(v);
        }
      }
    } else if (mode === 'continue') {
      out.keyframes = [];
      out.keyframe_frames = undefined;
      const lv = Object.values(graph).find(n => n.class_type === 'LoadVideo');
      const src = lv?.inputs?.file;
      out.source_video = src && src !== 'source_clip.mp4' ? src : '';
      out.source_video_id = '';
      const batch = Object.values(graph).find(
        n => n.class_type === 'ImageFromBatch' && graph[n.inputs?.image?.[0]]?.class_type === 'GetVideoComponents',
      );
      out.continue_frames = batch?.inputs?.length ?? 5;
    }
    out.width = i.width;
    out.height = i.height;
    out.length = 5 + 17 * Math.max(0, Math.ceil(((i.length ?? 124) - 5) / 17 - 1e-9));
    out.fps = 24;
    out.actual_duration_seconds = out.length / 24;
    const presets = [5, 8, 10, 12, 15];
    out.duration_seconds =
      presets.find(s => 5 + 17 * Math.max(0, Math.ceil((s * 24 - 5) / 17)) === out.length) ??
      out.actual_duration_seconds;
    const sampler = Object.values(graph).find(
      n => ['KSampler', 'KSamplerAdvanced', 'BasicScheduler'].includes(n.class_type) && n.inputs?.steps != null,
    );
    out.steps = sampler?.inputs.steps ?? 8;
  }
  out.n = out.batch_size ?? 1;
  return out;
}

export const cleanModelName = name =>
  String(name || '')
    .replace(/\.(safetensors|ckpt|pt|pth|bin|gguf|onnx)$/i, '')
    .replace(/_/g, ' ');

export const taskModel = (t, models) =>
  models.find(m => m.id === t.params?.model)?.name ||
  t.params?.model ||
  t.required_models?.find(m => ['checkpoints', 'checkpoint', 'diffusion_models', 'seedvr2'].includes(m.kind))?.name ||
  '模型未记录';

export function isVideoTask(task) {
  if (!task || typeof task !== 'object') return false;
  if (task.media_kind === 'video') return true;
  const first = task.results?.[0];
  if (first && typeof first === 'object') {
    if (first.media_type === 'video') return true;
    if (/\.(mp4|webm|mov|mkv)$/i.test(first.filename || '')) return true;
  }
  const p = task.params || {};
  const wf = String(p.workflow_id || p.workflow || '').toLowerCase();
  if (p.mode === 'video_seedvr2' || wf === 'video-seedvr2' || wf.startsWith('h3-') || wf.includes('video')) {
    return true;
  }
  if (p.duration_seconds != null || (p.length != null && Number(p.fps) === 24)) return true;
  const promptNodes = Object.values(task.submitted_prompt || task.prompt || {});
  if (
    promptNodes.some(n =>
      ['MiniMaxH3ReferenceToVideo', 'MiniMaxH3ImageToVideo', 'CreateVideo', 'SaveVideo', 'VHS_VideoCombine'].includes(
        n?.class_type,
      ),
    )
  ) {
    return true;
  }
  return false;
}

export function taskTypeBadge(task) {
  const p = task?.params || {};
  const wf = String(p.workflow_id || p.workflow || '').toLowerCase();
  if (p.mode === 'video_seedvr2' || wf === 'video-seedvr2') {
    return { kind: 'video_refine', label: '视频精修', unit: '段', tone: 'info' };
  }
  if (isVideoTask(task)) {
    return { kind: 'video', label: '视频生成', unit: '段', tone: 'info' };
  }
  if (p.mode === 'seedvr2' || wf === 'seedvr2' || p.mode === 'supir' || p.mode === 'upscale') {
    return { kind: 'image_refine', label: '图像精修', unit: '张', tone: 'warn' };
  }
  if (p.mode === 'inpaint') return { kind: 'inpaint', label: '局部修复', unit: '张', tone: 'muted' };
  if (p.mode === 'img2img') return { kind: 'img2img', label: '图生图', unit: '张', tone: 'muted' };
  if (p.mode === 'hires') return { kind: 'hires', label: '高清重绘', unit: '张', tone: 'muted' };
  return { kind: 'image', label: '图片生成', unit: '张', tone: 'muted' };
}

export function taskTitle(task, workflows = [], models = []) {
  const p = task?.params || {};
  const wfId = p.workflow_id || p.workflow;
  const wf = workflows.find(w => w.id === wfId);
  const badge = taskTypeBadge(task);
  if (badge.kind === 'video_refine') {
    const res = p.resolution ? `→ ${p.resolution}p` : '';
    const src = String(p.source_video || '').split('/').pop();
    return `SeedVR2 视频精修 ${res}${src ? ` · ${src.slice(0, 18)}` : ''}`.trim();
  }
  const rawPrompt = String(p.prompt || '').replace(/\s+/g, ' ').trim();
  if (rawPrompt) {
    return rawPrompt.length > 44 ? rawPrompt.slice(0, 44) + '…' : rawPrompt;
  }
  if (wf?.name) return wf.name;
  return cleanModelName(taskModel(task, models)) || task?.id?.slice(0, 8) || '未命名任务';
}

export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return '—';
  const total = Math.round(Number(seconds));
  if (total < 60) return `${total} 秒`;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}小时 ${String(m).padStart(2, '0')}分`;
  return `${m}分 ${String(s).padStart(2, '0')}秒`;
}

export const elapsedSeconds = (t, nowMs = null) => {
  const direct = Number(t?.total_seconds);
  if (Number.isFinite(direct) && direct >= 0) return direct;
  const a = Date.parse(t?.started_at);
  const b = t?.finished_at ? Date.parse(t.finished_at) : nowMs;
  if (Number.isFinite(a) && Number.isFinite(b)) return Math.max(0, (b - a) / 1000);
  return null;
};

export const elapsed = t => {
  const sec = elapsedSeconds(t);
  return sec != null ? formatDuration(sec) : '耗时未记录';
};

export function formatCu(value) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '—';
  const num = Number(value);
  if (Math.abs(num) >= 100) return `${num.toFixed(1)} CU`;
  if (Math.abs(num) >= 10) return `${num.toFixed(2)} CU`;
  return `${num.toFixed(3).replace(/0$/, '')} CU`;
}

export function taskCostDisplay(task, runtime = null) {
  const actual = Number(task?.cu_actual_delta);
  const estimated = Number(task?.cu_estimated);
  const rate = Number(task?.cu_rate_hourly ?? runtime?.quota?.consumption_rate);
  if (Number.isFinite(actual) && actual > 0) {
    return { cu: actual, isActual: true, label: formatCu(actual), detail: '账户余额实扣' };
  }
  if (Number.isFinite(estimated) && estimated > 0) {
    return { cu: estimated, isActual: false, label: `≈ ${formatCu(estimated)}`, detail: Number.isFinite(rate) ? `按 ${rate.toFixed(2)} CU/h 折算` : '按运行费率估算' };
  }
  if (Number.isFinite(rate) && rate > 0) {
    const sec = elapsedSeconds(task);
    if (sec != null && sec > 0) {
      const calc = (sec / 3600) * rate;
      return { cu: calc, isActual: false, label: `≈ ${formatCu(calc)}`, detail: `实时按 ${rate.toFixed(2)} CU/h 估算` };
    }
    return { cu: null, isActual: false, label: `${rate.toFixed(2)} CU/h`, detail: '当前机器耗电速率' };
  }
  return { cu: null, isActual: false, label: '—', detail: '未采集额度数据' };
}

export function taskSpecLine(task) {
  const p = task?.params || {};
  const badge = taskTypeBadge(task);
  const count = Number(p.n ?? p.batch_size ?? 1) || 1;
  const first = task?.results?.[0];
  const parts = [];
  if (badge.kind === 'video_refine') {
    if (first?.width && first?.height) parts.push(`${first.width}×${first.height}`);
    else if (p.resolution) parts.push(`→ ${p.resolution}p`);
    if (first?.duration) parts.push(`${Number(first.duration).toFixed(1)}s`);
    if (p.video_crf != null) parts.push(`CRF ${p.video_crf}`);
    if (p.color_correction) parts.push(p.color_correction.toUpperCase());
    parts.push(`${count} ${badge.unit}视频`);
    return parts.join(' · ');
  }
  if (badge.kind === 'video') {
    const w = first?.width || p.width;
    const h = first?.height || p.height;
    if (w && h) parts.push(`${w}×${h}`);
    const dur = first?.duration ?? p.actual_duration_seconds ?? p.duration_seconds;
    if (dur) parts.push(`${Number(dur).toFixed(1)}s`);
    const fps = p.output_fps && p.output_fps !== 'off' ? p.output_fps : (p.fps || 24);
    parts.push(`${fps}fps`);
    if (Array.isArray(p.keyframes) && p.keyframes.length) parts.push(`${p.keyframes.length} 关键帧`);
    else if (Array.isArray(p.ref_images) && p.ref_images.length) parts.push(`${p.ref_images.length} 参考图`);
    if (p.video_crf != null) parts.push(`CRF ${p.video_crf}`);
    parts.push(`${count} ${badge.unit}视频`);
    return parts.join(' · ');
  }
  const w = first?.width || p.width;
  const h = first?.height || p.height;
  if (w && h) parts.push(`${w}×${h}`);
  if (p.steps != null) parts.push(`${p.steps} 步`);
  if (p.upscale?.enabled) parts.push(`${p.upscale.mode === 'ai' ? 'AI 精修' : `${p.upscale.scale || 2}×超分`}`);
  parts.push(`${count} ${badge.unit}`);
  return parts.join(' · ');
}

export function classifyWorkflow(w) {
  const id = String(w?.id || '').toLowerCase();
  const mode = h3Mode(w);
  if (mode || id.startsWith('h3-')) {
    const isBf16 = id.includes('bf16');
    const isInt8 = id.includes('int8');
    const isHq20 = id.includes('hq20');
    const badges = [];
    if (mode === 'keyframes') badges.push('路线A·多关键帧');
    else if (mode === 'continue') badges.push('路线B·尾段续写');
    else if (mode === 'ref') badges.push('参考图 Ref2V');
    if (isBf16) badges.push('BF16');
    else if (isInt8) badges.push('INT8');
    badges.push(isHq20 ? '20步无Turbo' : '8步Turbo');
    return { category: 'video', categoryLabel: '视频 H3', route: mode || 'ref', badges };
  }
  if (w?.mode === 'video_seedvr2' || id === 'video-seedvr2' || id === 'seedvr2' || id === 'supir' || id === 'esrgan') {
    return {
      category: 'refine',
      categoryLabel: 'AI 精修',
      route: w?.mode || id,
      badges: [w?.mode === 'video_seedvr2' ? '视频精修' : '图像精修'],
    };
  }
  if (id.includes('moss') || JSON.stringify(w?.workflow || {}).includes('moss_image')) {
    const graphText = JSON.stringify(w?.workflow || {}).toLowerCase();
    const isSft = graphText.includes('moss_image_sft') || (id.includes('moss') && id.includes('sft'));
    const isBase = !isSft && (graphText.includes('moss_image_base') || id === 'moss-base' || id.startsWith('moss-') || id.includes('krea2-moss'));
    const badges = [];
    if (id.includes('krea2') || graphText.includes('krea2')) badges.push('Krea2双采');
    if (isSft) {
      badges.push('MOSS SFT', 'INT8');
    } else if (isBase) {
      badges.push('MOSS Base', 'BF16');
    }
    if (!badges.length) badges.push('MOSS Image');
    return { category: 'moss', categoryLabel: 'MOSS', route: 'moss', mossVariant: isSft ? 'sft' : isBase ? 'base' : null, badges };
  }
  if (w?.builtin || id.startsWith('sdxl-') || id.startsWith('pack-')) {
    return {
      category: w?.builtin ? 'sdxl' : 'custom',
      categoryLabel: w?.builtin ? 'SDXL 内置' : '特色组合',
      route: w?.mode || 'txt2img',
      badges: [w?.builtin ? '内置' : '自定义'],
    };
  }
  return { category: 'custom', categoryLabel: '自定义', route: w?.mode || 'custom', badges: ['自定义'] };
}

export function workflowQuickSpec(w) {
  if (!w?.workflow) return '';
  const meta = classifyWorkflow(w);
  if (meta.category !== 'moss') return '';
  const nodes = Object.values(w.workflow);
  const aura = nodes.find(n => n?.class_type === 'ModelSamplingAuraFlow')?.inputs;
  const sampler = nodes.filter(n => n?.class_type === 'KSampler').at(-1)?.inputs;
  if (!sampler) return '';
  const latent = nodes.find(n => ['EmptyFlux2LatentImage', 'EmptyLatentImage'].includes(n?.class_type))?.inputs;
  const parts = [];
  if (aura?.shift != null) parts.push(`Shift ${aura.shift}`);
  if (sampler.steps != null) parts.push(`${sampler.steps}步`);
  if (sampler.cfg != null) parts.push(`CFG ${sampler.cfg}`);
  if (sampler.sampler_name && sampler.scheduler) parts.push(`${sampler.sampler_name}/${sampler.scheduler}`);
  if (latent?.width && latent?.height) parts.push(`${latent.width}×${latent.height}`);
  return parts.join(' · ');
}
