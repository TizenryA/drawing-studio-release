import { createInitialDemoState, makeDemoArtworkSvg } from './demoData.js';
import { UNASSIGNED_PROJECT } from './galleryPagination.js';
import { isVideoTask, taskTypeBadge } from './taskData.js';

const getDefaultBaseUrl = () => {
  try {
    return import.meta?.env?.BASE_URL || '/';
  } catch {
    return '/';
  }
};

let state = createInitialDemoState(getDefaultBaseUrl());
const timers = new Set();

function clearTimers() {
  for (const id of timers) clearTimeout(id);
  timers.clear();
}

export function resetDemoState(baseUrl = getDefaultBaseUrl()) {
  clearTimers();
  state = createInitialDemoState(baseUrl);
  return state;
}

export function getDemoState() {
  return state;
}

const clone = value => structuredClone(value);

const dispatchBrowserEvent = (name, detail) => {
  if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
    window.dispatchEvent(detail !== undefined ? new CustomEvent(name, { detail }) : new Event(name));
  }
};

function parseBody(options) {
  if (!options?.body) return {};
  if (typeof options.body === 'string') {
    try {
      return JSON.parse(options.body);
    } catch {
      return {};
    }
  }
  return options.body;
}

function projectSummaries() {
  const counts = new Map();
  let unassigned = 0;
  for (const name of state.customProjects) counts.set(name, 0);
  for (const item of state.gallery) {
    const p = (item.project || '').trim();
    if (!p) unassigned += 1;
    else counts.set(p, (counts.get(p) || 0) + 1);
  }
  const projects = Array.from(counts.entries())
    .sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
    .map(([name, count]) => ({ name, count }));
  return { projects, total: state.gallery.length, unassigned_count: unassigned };
}

function buildTasksPage(searchParams) {
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const limit = Math.max(1, Math.min(100, Number(searchParams.get('limit')) || 20));
  const statusFilter = searchParams.get('status') || '';
  const kindFilter = searchParams.get('kind') || '';
  const modelFilter = searchParams.get('model') || '';
  const dateFilter = searchParams.get('date') || '';
  const includeActive = searchParams.get('include_active') === 'true';

  const activeItems = state.tasks.filter(t => !['completed', 'failed', 'cancelled', 'interrupted', 'error'].includes(t.status));
  const filtered = state.tasks.filter(t => {
    if (statusFilter === 'active' && ['completed', 'failed', 'cancelled', 'interrupted', 'error'].includes(t.status)) return false;
    if (statusFilter && statusFilter !== 'active' && t.status !== statusFilter) return false;
    if (kindFilter) {
      const b = taskTypeBadge(t);
      if (kindFilter === 'video' && b.kind !== 'video') return false;
      if (kindFilter === 'image' && !['image', 'img2img', 'inpaint', 'hires'].includes(b.kind)) return false;
      if (kindFilter === 'refine' && !['video_refine', 'image_refine'].includes(b.kind)) return false;
    }
    if (modelFilter && t.params?.model !== modelFilter) return false;
    if (dateFilter && !(t.finished_at || t.created_at || '').startsWith(dateFilter)) return false;
    return true;
  });

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const clampedPage = Math.min(page, totalPages);
  const offset = (clampedPage - 1) * limit;
  const completedCount = state.tasks.filter(t => t.status === 'completed').length;
  const failedCount = state.tasks.filter(t => ['failed', 'error', 'interrupted'].includes(t.status)).length;
  const totalCu = state.tasks.reduce((sum, t) => sum + (Number(t.cu_cost) || 0), 0);
  const models = Array.from(new Set(state.tasks.map(t => t.params?.model).filter(Boolean)));

  return {
    items: clone(filtered.slice(offset, offset + limit)),
    ...(includeActive ? { active_items: clone(activeItems) } : {}),
    total,
    page: clampedPage,
    limit,
    total_pages: totalPages,
    has_more: clampedPage < totalPages,
    stats: {
      active: activeItems.length,
      completed: completedCount,
      failed: failedCount,
      total_cu: Number(totalCu.toFixed(2)),
      has_cu: true,
    },
    models,
  };
}

function buildGalleryPage(searchParams) {
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const limit = Math.max(1, Math.min(200, Number(searchParams.get('limit')) || 36));
  const q = (searchParams.get('q') || '').trim().toLowerCase();
  const favorite = searchParams.get('favorite') === 'true';
  const project = searchParams.get('project') || '';
  const mediaType = searchParams.get('media_type') || '';
  const model = (searchParams.get('model') || '').trim().toLowerCase();
  const lora = (searchParams.get('lora') || '').trim().toLowerCase();
  const date = searchParams.get('date') || '';
  const tag = (searchParams.get('tag') || '').trim().toLowerCase();
  const order = searchParams.get('order') === 'asc' ? 'asc' : 'desc';

  const filtered = state.gallery.filter(item => {
    if (favorite && !item.favorite) return false;
    if (project === UNASSIGNED_PROJECT && (item.project || '').trim() !== '') return false;
    if (project && project !== UNASSIGNED_PROJECT && item.project !== project) return false;
    if (mediaType && item.media_type !== mediaType) return false;
    if (model && !String(item.params?.model || '').toLowerCase().includes(model)) return false;
    if (lora && !(item.params?.loras || []).some(l => JSON.stringify(l).toLowerCase().includes(lora))) return false;
    if (date && !String(item.created_at || '').startsWith(date)) return false;
    if (tag && !(item.tags || []).some(t => String(t).toLowerCase().includes(tag))) return false;
    if (q && !JSON.stringify(item).toLowerCase().includes(q)) return false;
    return true;
  });

  filtered.sort((a, b) => {
    const cmp = String(a.created_at || '').localeCompare(String(b.created_at || ''));
    return order === 'asc' ? cmp : -cmp;
  });

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const clampedPage = Math.min(page, totalPages);
  const offset = (clampedPage - 1) * limit;

  return {
    items: clone(filtered.slice(offset, offset + limit)),
    total,
    page: clampedPage,
    limit,
    total_pages: totalPages,
    has_more: clampedPage < totalPages,
  };
}

function scheduleTaskProgress(taskId) {
  const stepMs = [450, 950, 1550, 2200];
  const schedule = (delay, fn) => {
    const id = setTimeout(() => {
      timers.delete(id);
      fn();
    }, delay);
    timers.add(id);
  };

  schedule(stepMs[0], () => {
    const t = state.tasks.find(x => x.id === taskId);
    if (!t || t.status === 'cancelled') return;
    t.status = 'running';
    t.stage = 'running';
    const maxSteps = Number(t.params?.steps) || 28;
    t.progress = { value: Math.max(1, Math.round(maxSteps * 0.25)), max: maxSteps };
    dispatchBrowserEvent('studio:task-event', clone(t));
  });

  schedule(stepMs[1], () => {
    const t = state.tasks.find(x => x.id === taskId);
    if (!t || t.status === 'cancelled') return;
    const maxSteps = Number(t.params?.steps) || 28;
    t.progress = { value: Math.max(1, Math.round(maxSteps * 0.68)), max: maxSteps };
    dispatchBrowserEvent('studio:task-event', clone(t));
  });

  schedule(stepMs[2], () => {
    const t = state.tasks.find(x => x.id === taskId);
    if (!t || t.status === 'cancelled') return;
    t.stage = 'archiving';
    const maxSteps = Number(t.params?.steps) || 28;
    t.progress = { value: maxSteps, max: maxSteps };
    dispatchBrowserEvent('studio:task-event', clone(t));
  });

  schedule(stepMs[3], () => {
    const t = state.tasks.find(x => x.id === taskId);
    if (!t || t.status === 'cancelled') return;
    const n = ++state.seq;
    const isVid = isVideoTask(t);
    const w = Number(t.params?.width) || (isVid ? 1280 : 896);
    const h = Number(t.params?.height) || (isVid ? 736 : 1280);
    const proj = (t.params?.project || '').trim();
    if (proj && !state.customProjects.includes(proj)) state.customProjects.push(proj);

    const gid = `demo-gal-${n}`;
    const thumb = makeDemoArtworkSvg({
      title: String(t.params?.prompt || 'Demo 生成作品').slice(0, 26) || 'Demo 生成作品',
      subtitle: `${t.params?.workflow_id || t.params?.model || 'Drawing Studio'} · ${w}×${h}`,
      badge: isVid ? 'DEMO VIDEO' : 'DEMO OUTPUT',
      width: w,
      height: h,
      palette: isVid ? 'arctic' : n % 2 === 0 ? 'emerald' : 'courtyard',
      style: isVid ? 'video' : 'poster',
    });

    const newItem = {
      id: gid,
      filename: `demo_${n}.${isVid ? 'mp4' : 'png'}`,
      project: proj,
      album: proj,
      media_type: isVid ? 'video' : 'image',
      width: w,
      height: h,
      duration: isVid ? 3.0 : null,
      favorite: false,
      parent_id: null,
      tags: ['Demo生成', isVid ? '视频' : '图片'],
      archive_status: 'saved',
      local_cached: true,
      created_at: new Date().toISOString(),
      url: isVid ? `${state.baseUrl}demo/h3-sample.mp4` : thumb,
      preview_url: thumb,
      thumbnail_url: thumb,
      ...(isVid ? { media_url: `${state.baseUrl}demo/h3-sample.mp4` } : {}),
      params: clone(t.params),
    };

    state.gallery.unshift(newItem);
    state.runtime.queue_count = Math.max(0, state.runtime.queue_count - 1);
    t.status = 'completed';
    t.stage = 'completed';
    t.finished_at = new Date().toISOString();
    t.sync_seconds = 0;
    t.gen_seconds = isVid ? 18.6 : 12.4;
    t.duration_seconds = t.gen_seconds;
    t.cu_cost = isVid ? 0.05 : 0.02;
    state.runtime.quota.balance = Number(Math.max(0, state.runtime.quota.balance - t.cu_cost).toFixed(2));
    t.cu_balance_end = state.runtime.quota.balance;
    t.results = [clone(newItem)];

    dispatchBrowserEvent('studio:task-event', clone(t));
    dispatchBrowserEvent('studio:gallery-dirty');
    dispatchBrowserEvent('studio:gallery-changed');
  });
}

function submitTask(body) {
  const n = ++state.seq;
  const id = `demo-task-${n}`;
  const isVid = isVideoTask({ params: body });
  const task = {
    id,
    status: 'queued',
    stage: 'queued',
    gpu: state.runtime.gpu || 'G4',
    actual_gpu: state.runtime.actual_gpu || 'NVIDIA G4 (96 GB)',
    created_at: new Date().toISOString(),
    finished_at: null,
    sync_seconds: 0,
    gen_seconds: null,
    duration_seconds: null,
    cu_cost: isVid ? 0.05 : 0.02,
    progress: { value: 0, max: Number(body.steps) || 28 },
    params: clone({ ...body, seed: body.seed === -1 || body.seed == null ? 100000 + n : body.seed }),
    results: [],
  };
  state.tasks.unshift(task);
  state.runtime.queue_count += 1;
  scheduleTaskProgress(id);
  return clone(task);
}

export async function demoApi(rawPath, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const parsedUrl = new URL(rawPath, 'https://demo.local');
  const path = parsedUrl.pathname;
  const search = parsedUrl.searchParams;
  const body = parseBody(options);

  if (path === '/api/auth/login') return { ok: true };
  if (path === '/api/auth/status') return { authenticated: true };

  if (path === '/api/runtime' && method === 'GET') {
    if (state.runtime.state === 'ready') {
      state.runtime.uptime = (state.runtime.uptime || 0) + 1;
    }
    return clone(state.runtime);
  }
  if (path === '/api/runtime/gpus') return clone(state.gpus);
  if (path === '/api/runtime/logs') return clone(state.runtime.logs);
  if (path.startsWith('/api/runtime/') && method === 'POST') {
    const action = path.split('/').pop();
    const nowSec = Math.floor(Date.now() / 1000);
    if (action === 'stop') {
      state.runtime = {
        ...state.runtime,
        state: 'off',
        memory_used: 0,
        queue_count: 0,
        logs: [...state.runtime.logs, { time: nowSec, message: '[Demo] Runtime stopped cleanly' }],
      };
    } else if (action === 'start' || action === 'restart' || action === 'reset') {
      const nextGpu = body.gpu || state.runtime.gpu || 'G4';
      const gpuMeta = state.gpus.gpus.find(g => g.id === nextGpu);
      const memGb = gpuMeta?.memory || 96;
      state.runtime = {
        ...state.runtime,
        state: 'ready',
        gpu: nextGpu,
        actual_gpu: gpuMeta?.name || `NVIDIA ${nextGpu}`,
        mode: body.mode || state.runtime.mode || 'temporary',
        memory_used: Math.round(memGb * 0.28 * 1073741824),
        memory_total: memGb * 1073741824,
        uptime: action === 'start' ? 12 : state.runtime.uptime || 120,
        error: '',
        logs: [...state.runtime.logs, { time: nowSec, message: `[Demo] Runtime ${action} -> ${gpuMeta?.name || nextGpu} ready` }],
      };
    }
    return clone(state.runtime);
  }

  if (path === '/api/settings') {
    if (method === 'PATCH') {
      const next = { ...body };
      delete next.password;
      state.settings = { ...state.settings, ...next };
    }
    return clone(state.settings);
  }

  if (path === '/api/keys') {
    if (method === 'POST') {
      const id = `key-demo-${++state.seq}`;
      const token = `ds-demo_${Math.random().toString(36).slice(2, 14)}`;
      const row = { id, name: body.name || '未命名密钥', prefix: token.slice(0, 10), revoked: false };
      state.keys.unshift(row);
      return { ...row, key: token };
    }
    return clone(state.keys);
  }
  if (path.startsWith('/api/keys/') && method === 'DELETE') {
    const id = decodeURIComponent(path.split('/').pop());
    state.keys = state.keys.filter(k => k.id !== id);
    return { ok: true };
  }
  if (path === '/api/requests') return clone(state.requests);

  if (path === '/api/models') return clone(state.models);
  if (path === '/api/models/scan' && method === 'POST') return { ok: true, added: 0, total: state.models.length };
  if (path === '/api/models/sync-batch' && method === 'POST') {
    const ids = Array.isArray(body.ids) ? body.ids : [];
    for (const m of state.models) {
      if (ids.includes(m.id)) {
        m.status = 'ready';
        m.installed_in_comfy = true;
      }
    }
    return { ok: true, started: ids };
  }
  if (path.startsWith('/api/models/')) {
    const parts = path.split('/').filter(Boolean);
    const id = decodeURIComponent(parts[2] || '');
    const model = state.models.find(m => m.id === id);
    if (!model) throw new Error(`找不到模型 ${id}`);
    if (parts[3] === 'sync' && parts[4] === 'cancel' && method === 'POST') {
      model.status = model.installed_in_comfy ? 'ready' : 'drive';
      return clone(model);
    }
    if (parts[3] === 'sync' && method === 'POST') {
      model.status = 'ready';
      model.installed_in_comfy = true;
      return clone(model);
    }
    if (parts[3] === 'cache' && method === 'DELETE') {
      model.status = 'drive';
      model.installed_in_comfy = false;
      return { ok: true };
    }
    if (parts.length === 3 && method === 'PATCH') {
      Object.assign(model, body);
      return clone(model);
    }
    if (parts.length === 3 && method === 'DELETE') {
      state.models = state.models.filter(m => m.id !== id);
      return { ok: true };
    }
  }

  if (path === '/api/workflows') {
    if (method === 'POST') {
      const next = { ...body, id: body.id || `custom-${++state.seq}`, builtin: false, version: (body.version || 0) + 1 };
      const idx = state.workflows.findIndex(w => w.id === next.id);
      if (idx === -1) state.workflows.push(next);
      else state.workflows[idx] = next;
      return clone(next);
    }
    return clone(state.workflows);
  }
  if (path.startsWith('/api/workflows/')) {
    const id = decodeURIComponent(path.split('/').pop());
    const found = state.workflows.find(w => w.id === id);
    if (!found) throw new Error(`找不到工作流 ${id}`);
    return clone(found);
  }

  if (path === '/api/presets') {
    if (method === 'POST') {
      const next = { id: `preset-${++state.seq}`, name: body.name || '自定义预设', params: clone(body.params || {}) };
      state.presets.unshift(next);
      return clone(next);
    }
    return clone(state.presets);
  }

  if (path === '/api/tasks') {
    if (method === 'POST') return submitTask(body);
    return buildTasksPage(search);
  }
  if (path.startsWith('/api/tasks/')) {
    const parts = path.split('/').filter(Boolean);
    const id = decodeURIComponent(parts[2] || '');
    const task = state.tasks.find(t => t.id === id);
    if (!task) throw new Error(`找不到任务 ${id}`);
    if (parts[3] === 'cancel' && method === 'POST') {
      task.status = 'cancelled';
      task.stage = 'cancelled';
      task.finished_at = new Date().toISOString();
      state.runtime.queue_count = Math.max(0, state.runtime.queue_count - 1);
      dispatchBrowserEvent('studio:task-event', clone(task));
      return clone(task);
    }
    return clone(task);
  }

  if (path === '/api/gallery/projects') {
    if (method === 'POST') {
      const clean = String(body.name || '').trim();
      if (clean && !state.customProjects.includes(clean)) state.customProjects.push(clean);
      return projectSummaries();
    }
    return projectSummaries();
  }
  if (path.startsWith('/api/gallery/projects/')) {
    const proj = decodeURIComponent(path.split('/').pop());
    if (method === 'PATCH') {
      const nextName = String(body.name || '').trim();
      if (!nextName) throw new Error('项目名称不能为空');
      state.customProjects = state.customProjects.map(p => (p === proj ? nextName : p));
      if (!state.customProjects.includes(nextName)) state.customProjects.push(nextName);
      for (const item of state.gallery) {
        if (item.project === proj) {
          item.project = nextName;
          item.album = nextName;
        }
      }
      return projectSummaries();
    }
    if (method === 'DELETE') {
      state.customProjects = state.customProjects.filter(p => p !== proj);
      for (const item of state.gallery) {
        if (item.project === proj) {
          item.project = '';
          item.album = '';
        }
      }
      return { ok: true };
    }
  }

  if (path === '/api/gallery') return buildGalleryPage(search);
  if (path === '/api/gallery/prune-local' && method === 'POST') return { ok: true, pruned: 2, freed_bytes: 18432000 };
  if (path === '/api/gallery/sync' && method === 'POST') return { ok: true, projects_found: state.customProjects.length, added: 0, moved: 0 };
  if (path === '/api/gallery/batch' && method === 'POST') {
    const ids = Array.isArray(body.ids) ? body.ids : [];
    if (body.action === 'delete') {
      state.gallery = state.gallery.filter(i => !ids.includes(i.id));
    } else if (body.action === 'favorite') {
      for (const i of state.gallery) if (ids.includes(i.id)) i.favorite = Boolean(body.value);
    } else if (body.action === 'project' || body.action === 'album') {
      const p = String(body.value || '').trim();
      if (p && !state.customProjects.includes(p)) state.customProjects.push(p);
      for (const i of state.gallery) if (ids.includes(i.id)) { i.project = p; i.album = p; }
    } else if (body.action === 'tags') {
      const tags = Array.isArray(body.value) ? body.value : [];
      for (const i of state.gallery) if (ids.includes(i.id)) i.tags = Array.from(new Set([...(i.tags || []), ...tags]));
    }
    return { ok: true, updated: ids.length };
  }
  if (path === '/api/gallery/concat' && method === 'POST') {
    const proj = String(body.project || '视频镜头').trim();
    const created = makeGalleryItem({
      id: `demo-gal-${++state.seq}`,
      filename: `h3_concat_${state.seq}.mp4`,
      project: proj,
      media_type: 'video',
      width: 720,
      height: 400,
      duration: 3.0,
      media_url: `${state.baseUrl}demo/h3-sample.mp4`,
      tags: ['MiniMax-H3', '无痕拼接'],
      svgOpts: { title: 'MiniMax H3 无痕拼接成片', subtitle: 'Demo 视频合成样片 · 720×400', badge: 'CONCAT MP4', palette: 'arctic', style: 'video' },
      params: { workflow_id: 'h3-fl2va-continue-8step-int8', width: 720, height: 400, steps: 8 },
      offsetMinutes: 0,
    });
    state.gallery.unshift(created);
    return clone(created);
  }
  if (path === '/api/gallery/import' && method === 'POST') {
    const id = `demo-gal-${++state.seq}`;
    const imported = makeGalleryItem({
      id,
      filename: options?.body?.get?.('file')?.name || `imported_${state.seq}.png`,
      project: String(options?.body?.get?.('project') || ''),
      tags: ['导入作品'],
      svgOpts: { title: '用户导入参考作品', subtitle: 'Demo 本地上传模拟归档', badge: 'IMPORTED', palette: 'emerald', style: 'poster' },
      offsetMinutes: 0,
    });
    state.gallery.unshift(imported);
    return clone(imported);
  }

  if (path.startsWith('/api/gallery/')) {
    const parts = path.split('/').filter(Boolean);
    const id = decodeURIComponent(parts[2] || '');
    const item = state.gallery.find(i => i.id === id);
    if (!item) throw new Error(`找不到作品 ${id}`);
    if (parts[3] === 'archive' && method === 'POST') {
      item.archive_status = 'saved';
      return clone(item);
    }
    if (parts[3] === 'extract-frame' && method === 'POST') {
      const thumb = makeDemoArtworkSvg({ title: '提取尾帧 · 路线 A 首帧', subtitle: `源视频 ${item.filename}`, badge: 'LAST FRAME', width: item.width, height: item.height, palette: 'twilight', style: 'courtyard' });
      return { path: thumb, url: thumb };
    }
    if (parts.length === 3 && method === 'PATCH') {
      Object.assign(item, body);
      if ('project' in body) {
        const clean = String(body.project || '').trim();
        item.project = clean;
        item.album = clean;
        if (clean && !state.customProjects.includes(clean)) state.customProjects.push(clean);
      }
      return clone(item);
    }
    if (parts.length === 3 && method === 'DELETE') {
      state.gallery = state.gallery.filter(i => i.id !== id);
      return { ok: true };
    }
    return {
      ...clone(item),
      related: clone(state.gallery.filter(r => r.id !== item.id && (r.parent_id === item.id || r.id === item.parent_id || (item.parent_id && r.parent_id === item.parent_id)))),
    };
  }

  if (path === '/api/uploads' && method === 'POST') {
    const name = options?.body?.get?.('file')?.name || `upload_${++state.seq}.png`;
    const thumb = makeDemoArtworkSvg({ title: name, subtitle: 'Demo 参考图 / 蒙版输入', badge: 'UPLOAD', width: 896, height: 1152, palette: 'amber', style: 'poster' });
    return { path: thumb, filename: name, url: thumb };
  }

  if (path === '/api/oauth/status' && method === 'GET') {
    return clone(state.oauth);
  }
  if (path.startsWith('/api/oauth/')) {
    const parts = path.split('/').filter(Boolean);
    const purpose = decodeURIComponent(parts[2] || '');
    if (!['colab', 'drive'].includes(purpose)) throw new Error(`不支持的授权目标：${purpose}`);
    const action = parts[3] || '';
    const record = state.oauth[purpose];

    if (method === 'DELETE' && !action) {
      record.configured = false;
      record.has_refresh_token = false;
      record.token_valid = false;
      record.account_email = '';
      state.settings[`${purpose}_configured`] = false;
      state.settings[`${purpose}_account_email`] = '';
      return { ok: true, purpose, status: clone(record) };
    }

    if (action === 'start' && method === 'POST') {
      const cid = String(body.client_id || '').trim() || (record.has_client_id ? 'demo-client-existing.apps.googleusercontent.com' : '');
      if (!cid) throw new Error(`请先填写 ${purpose.toUpperCase()} Client ID`);
      const flowId = `demo-flow-${++state.seq}`;
      const flowState = `demo-state-${state.seq}`;
      const redirectUri = body.redirect_uri || record.default_manual_redirect_uri;
      state.oauthFlows[flowId] = { purpose, flowMode: body.flow_mode || 'manual', clientId: cid, redirectUri, state: flowState };
      const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${encodeURIComponent(cid)}&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=code&scope=${encodeURIComponent(record.scopes.join(' '))}&code_challenge_method=S256&state=${encodeURIComponent(flowState)}`;
      return {
        flow_id: flowId,
        purpose,
        flow_mode: body.flow_mode || 'manual',
        auth_url: authUrl,
        redirect_uri: redirectUri,
        state: flowState,
        scopes: clone(record.scopes),
        expires_in: 900,
      };
    }

    if (action === 'exchange' && method === 'POST') {
      const flow = state.oauthFlows[body.flow_id];
      if (!flow || flow.purpose !== purpose) throw new Error('Demo 授权会话不存在或已过期');
      if (!String(body.code_or_url || '').trim()) throw new Error('请粘贴授权码或回调 URL');
      delete state.oauthFlows[body.flow_id];
      record.configured = true;
      record.has_client_id = true;
      record.has_refresh_token = true;
      record.token_valid = true;
      record.account_email = purpose === 'colab' ? 'colab-demo@example.com' : 'drive-demo@example.com';
      state.settings[`${purpose}_configured`] = true;
      state.settings[`${purpose}_account_email`] = record.account_email;
      return { ok: true, purpose, account_email: record.account_email, status: clone(record) };
    }

    if (action === 'import' && method === 'POST') {
      const cred = typeof body.credential_json === 'string' ? JSON.parse(body.credential_json) : (body.credential_json || body);
      if (!cred?.client_id || !cred?.refresh_token) throw new Error('凭据 JSON 必须包含 client_id 与 refresh_token');
      record.configured = true;
      record.has_client_id = true;
      record.has_refresh_token = true;
      record.token_valid = true;
      record.account_email = cred.account_email || (purpose === 'colab' ? 'colab-imported@example.com' : 'drive-imported@example.com');
      record.client_id_preview = `${String(cred.client_id).slice(0, 8)}...`;
      state.settings[`${purpose}_configured`] = true;
      state.settings[`${purpose}_account_email`] = record.account_email;
      return { ok: true, purpose, verified: body.verify !== false, account_email: record.account_email, status: clone(record) };
    }

    if (action === 'test' && method === 'POST') {
      if (!record.configured) throw new Error(`${purpose} 尚未配置凭据`);
      return purpose === 'colab'
        ? {
            ok: true,
            purpose: 'colab',
            account_email: record.account_email,
            balance: state.runtime.quota.balance,
            gpus: state.gpus.gpus.map(g => g.id),
            message: `[Demo] Colab 连通正常（可用 GPU：G4 / H100 / A100 / L4 / T4 · 余额 ${state.runtime.quota.balance} CU）`,
            status: clone(record),
          }
        : {
            ok: true,
            purpose: 'drive',
            account_email: record.account_email,
            message: `[Demo] Drive 连通正常（${record.account_email}）`,
            status: clone(record),
          };
    }
  }

  throw new Error(`Demo 模式暂未模拟接口：${method} ${path}`);
}

export async function demoUpload(file) {
  let url = '';
  if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function' && typeof Blob !== 'undefined' && file instanceof Blob) {
    url = URL.createObjectURL(file);
  }
  if (!url) {
    url = makeDemoArtworkSvg({
      title: file?.name || 'uploaded.png',
      subtitle: 'Demo 本地参考素材',
      badge: 'UPLOAD',
      width: 896,
      height: 1152,
      palette: 'amber',
      style: 'poster',
    });
  }
  return { path: url, filename: file?.name || 'upload.png', url };
}

export async function demoDownload(path, name) {
  const m = String(path).match(/^\/api\/gallery\/([^/]+)\/download/);
  const item = m ? state.gallery.find(i => i.id === decodeURIComponent(m[1])) : null;
  const href = item?.media_url || item?.url || makeDemoArtworkSvg({ title: name || 'drawing-studio-demo' });
  if (typeof document === 'undefined') return;
  const a = document.createElement('a');
  a.href = href;
  a.download = name || item?.filename || 'drawing-studio-demo.svg';
  a.click();
}
