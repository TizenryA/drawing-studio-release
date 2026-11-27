import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Cpu, PenLine, Boxes, Images, Settings, Check, LogIn } from 'lucide-react';
import { api, list, post } from './api';
import { IconButton, ErrorBox, Status, isTaskActive } from './ui';
import Machine from './pages/Machine';
import { videoPayload } from './h3Video';
import { workflowParams, formatCu, normalizeTasksResponse, upsertTaskList } from './taskData';
import Draw, { defaultParams } from './pages/Draw';
import Models from './pages/Models';
import Gallery from './pages/Gallery';
import Tasks from './pages/Tasks';
import SettingsPanel from './pages/Settings';
import { Button } from './ui';

const pages = [
  ['draw', '绘图', PenLine],
  ['tasks', '任务', Cpu],
  ['models', '模型', Boxes],
  ['gallery', '图库', Images],
];

const stored = (key, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
};

export default function App() {
  const [page, setPage] = useState('draw');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [runtime, setRuntime] = useState(null);
  const [settings, setSettings] = useState({});
  const [models, setModels] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [presets, setPresets] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [galleryProjects, setGalleryProjects] = useState({ projects: [], total: 0, unassigned_count: 0 });
  const [errors, setErrors] = useState({});
  const [theme, setTheme] = useState(() => stored('studio-theme', 'light'));
  const [params, setParams] = useState(() => ({ ...defaultParams, ...stored('studio-draft', {}) }));
  const [login, setLogin] = useState(false);
  const [notice, setNotice] = useState('');
  const [loginError, setLoginError] = useState('');
  const [password, setPassword] = useState('');
  const [logging, setLogging] = useState(false);

  const tasksRef = useRef(tasks);
  useEffect(() => { tasksRef.current = tasks; }, [tasks]);
  const modelsRef = useRef(models);
  useEffect(() => { modelsRef.current = models; }, [models]);

  const requests = useRef({});
  const inflight = useRef({});
  const lastPayload = useRef({});
  const completedIdsRef = useRef(new Set());
  const tasksBootstrappedRef = useRef(false);
  const sseConnectedRef = useRef(false);

  const runtimeAction = useCallback(() => {
    requests.current.runtime = (requests.current.runtime || 0) + 1;
    delete inflight.current.runtime;
    setErrors(old => ({ ...old, runtime: '正在确认机器状态，请稍候…' }));
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      const data = await api('/api/gallery/projects');
      if (data && Array.isArray(data.projects)) setGalleryProjects(data);
    } catch {}
  }, []);

  const refresh = useCallback(async (keys = ['runtime', 'settings', 'models', 'tasks', 'presets', 'workflows']) => {
    const setters = {
      runtime: setRuntime,
      settings: setSettings,
      models: v => setModels(list(v)),
      presets: v => setPresets(list(v)),
      workflows: v => setWorkflows(list(v)),
    };
    await Promise.all(
      keys.map(key => {
        if (inflight.current[key]) return inflight.current[key];
        const pending = (async () => {
          const sequence = (requests.current[key] = (requests.current[key] || 0) + 1);
          try {
            const url = key === 'tasks'
              ? '/api/tasks?page=1&limit=30&paginated=true&include_active=true'
              : `/api/${key}`;
            const value = await api(url);
            if (requests.current[key] !== sequence) return;
            if (key === 'tasks') {
              const norm = normalizeTasksResponse(value, 1, 30);
              const nextRows = upsertTaskList(norm.activeItems, norm.items, 40);
              setTasks(nextRows);
              if (!tasksBootstrappedRef.current) {
                for (const t of nextRows) {
                  if (t.status === 'completed') completedIdsRef.current.add(t.id);
                }
                tasksBootstrappedRef.current = true;
              } else {
                let newlyDone = false;
                for (const t of nextRows) {
                  if (t.status === 'completed' && !completedIdsRef.current.has(t.id)) {
                    completedIdsRef.current.add(t.id);
                    newlyDone = true;
                  }
                }
                if (newlyDone) {
                  window.dispatchEvent(new Event('studio:gallery-dirty'));
                  refreshProjects();
                }
              }
            } else {
              const serialized = JSON.stringify(value);
              if (lastPayload.current[key] !== serialized) {
                lastPayload.current[key] = serialized;
                setters[key](value);
                if (key === 'settings' && value.theme) setTheme(value.theme);
              }
            }
            setErrors(old => (old[key] ? { ...old, [key]: '' } : old));
          } catch (e) {
            if (requests.current[key] !== sequence) return;
            setErrors(old => (old[key] === e.message ? old : { ...old, [key]: e.message }));
          }
        })();
        inflight.current[key] = pending;
        return pending.finally(() => {
          if (inflight.current[key] === pending) delete inflight.current[key];
        });
      }),
    );
  }, [refreshProjects]);

  useEffect(() => {
    const unauthorized = () => setLogin(true);
    window.addEventListener('studio:unauthorized', unauthorized);
    refresh();
    refreshProjects();
    let timerId = null;
    const poll = () => {
      if (document.visibilityState === 'visible') {
        refresh(['runtime', 'tasks', 'models']);
      }
      const hasActiveTask = tasksRef.current?.some(t => ['queued', 'running'].includes(t.status));
      const hasActiveSync = modelsRef.current?.some(m => ['queued_sync', 'downloading', 'verifying'].includes(m.status));
      const activeNow = hasActiveTask || hasActiveSync;
      const delay = sseConnectedRef.current ? (activeNow ? 3000 : 10000) : (activeNow ? 1500 : 5000);
      timerId = setTimeout(poll, delay);
    };
    timerId = setTimeout(poll, 4000);
    return () => {
      if (timerId) clearTimeout(timerId);
      window.removeEventListener('studio:unauthorized', unauthorized);
    };
  }, [refresh, refreshProjects]);

  useEffect(() => {
    if (login || typeof EventSource === 'undefined') {
      sseConnectedRef.current = false;
      return;
    }
    const es = new EventSource('/api/events', { withCredentials: true });
    es.onopen = () => {
      sseConnectedRef.current = true;
    };
    es.onerror = () => {
      sseConnectedRef.current = false;
    };
    es.onmessage = msg => {
      sseConnectedRef.current = true;
      if (!msg?.data) return;
      let parsed;
      try {
        parsed = JSON.parse(msg.data);
      } catch {
        return;
      }
      const { event, data } = parsed || {};
      if (event === 'task' && data?.id) {
        setTasks(old => upsertTaskList(old, data, 40));
        if (data.status === 'completed' && !completedIdsRef.current.has(data.id)) {
          completedIdsRef.current.add(data.id);
          window.dispatchEvent(new Event('studio:gallery-dirty'));
          refreshProjects();
        }
        window.dispatchEvent(new CustomEvent('studio:task-event', { detail: data }));
      } else if (event === 'runtime' && data && typeof data === 'object') {
        setRuntime(old => (old ? { ...old, ...data } : data));
      } else if (event === 'models' && data) {
        if (Array.isArray(data)) {
          setModels(data);
        } else if (data.id) {
          setModels(old => {
            const idx = old.findIndex(m => m.id === data.id);
            if (idx === -1) return [data, ...old];
            const next = [...old];
            next[idx] = { ...next[idx], ...data };
            return next;
          });
        }
      }
    };
    return () => {
      sseConnectedRef.current = false;
      es.close();
    };
  }, [login, refreshProjects]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && media.matches);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#111111' : '#fafafa');
    };
    apply();
    media.addEventListener('change', apply);
    try { localStorage.setItem('studio-theme', JSON.stringify(theme)); } catch {}
    return () => media.removeEventListener('change', apply);
  }, [theme]);

  useEffect(() => {
    try { localStorage.setItem('studio-draft', JSON.stringify(params)); } catch {}
  }, [params]);

  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(id);
  }, [notice]);

  const positions = useRef({});
  const currentPage = useRef(page);
  const navigate = p => {
    positions.current[currentPage.current] = window.scrollY;
    currentPage.current = p;
    setPage(p);
    requestAnimationFrame(() => window.scrollTo({ top: positions.current[p] || 0 }));
  };

  useEffect(() => {
    const vv = window.visualViewport;
    let timerId = null;
    const isEditable = el =>
      !!el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable);
    const update = () => {
      if (timerId) {
        clearTimeout(timerId);
        timerId = null;
      }
      const open = !!vv && window.innerHeight - vv.height > 150 && isEditable(document.activeElement);
      document.documentElement.classList.toggle('keyboard-open', open);
    };
    const delayedUpdate = () => {
      if (timerId) clearTimeout(timerId);
      timerId = setTimeout(() => {
        timerId = null;
        if (!isEditable(document.activeElement)) {
          document.documentElement.classList.remove('keyboard-open');
        } else {
          update();
        }
      }, 120);
    };
    vv?.addEventListener('resize', update);
    document.addEventListener('focusin', update);
    document.addEventListener('focusout', delayedUpdate);
    return () => {
      if (timerId) clearTimeout(timerId);
      vv?.removeEventListener('resize', update);
      document.removeEventListener('focusin', update);
      document.removeEventListener('focusout', delayedUpdate);
    };
  }, []);

  useEffect(() => {
    const listener = () => navigate('gallery');
    window.addEventListener('studio:image', listener);
    return () => window.removeEventListener('studio:image', listener);
  }, []);

  const reuse = p => {
    window.dispatchEvent(new Event('studio:params-replaced'));
    setParams({
      ...defaultParams,
      ...p,
      project: p.project || p.album || '',
      workflow: p.workflow_id || p.workflow || '',
      loras: p.loras || [],
      upscale: { ...defaultParams.upscale, ...p.upscale },
    });
    navigate('draw');
    setNotice('参数已载入，可编辑后生成');
  };

  const pickH3Workflow = (prefix, preferInt8) => {
    const exact = workflows.find(w => w.id === `${prefix}-8step-${preferInt8 ? 'int8' : 'bf16'}`);
    return (
      exact ||
      workflows.find(w => w.id.startsWith(prefix) && (preferInt8 ? w.id.endsWith('-int8') : w.id.endsWith('-bf16'))) ||
      workflows.find(w => w.id.startsWith(prefix))
    );
  };

  const continueVideo = async item => {
    const srcWorkflowId = String(item?.params?.workflow_id || item?.params?.workflow || '');
    const preferInt8 = srcWorkflowId.includes('int8') || String(item?.params?.model || '').toLowerCase().includes('int8');
    const target = pickH3Workflow('h3-fl2va-continue', preferInt8);
    const base = target ? workflowParams(target, models) : { ...defaultParams };
    window.dispatchEvent(new Event('studio:params-replaced'));
    setParams({
      ...base,
      project: item?.project || item?.album || '',
      prompt: item?.params?.prompt || base.prompt,
      width: item?.width && item.width % 32 === 0 ? item.width : base.width,
      height: item?.height && item.height % 32 === 0 ? item.height : base.height,
      source_video: item.filename || '',
      source_video_id: item.id,
      continue_frames: base.continue_frames || 5,
      keyframes: [],
      keyframe_frames: undefined,
      seed: -1,
    });
    navigate('draw');
    setNotice(target ? `已切到 ${target.name} 并绑定源视频` : '已绑定续写源视频，请选择路线B续写工作流');
  };

  const keyframeFromVideo = async (item, framePath) => {
    const srcWorkflowId = String(item?.params?.workflow_id || item?.params?.workflow || '');
    const preferInt8 = srcWorkflowId.includes('int8') || String(item?.params?.model || '').toLowerCase().includes('int8');
    const target = pickH3Workflow('h3-fl2va-keyframes', preferInt8);
    const base = target ? workflowParams(target, models) : { ...defaultParams };
    window.dispatchEvent(new Event('studio:params-replaced'));
    setParams({
      ...base,
      project: item?.project || item?.album || '',
      prompt: item?.params?.prompt || base.prompt,
      width: item?.width && item.width % 32 === 0 ? item.width : base.width,
      height: item?.height && item.height % 32 === 0 ? item.height : base.height,
      keyframes: [framePath],
      keyframe_frames: undefined,
      seed: -1,
    });
    navigate('draw');
    setNotice(target ? `已提取尾帧作为首帧，并切到 ${target.name}` : '已提取尾帧作为首帧，请选择路线A关键帧工作流');
  };

  const useModel = m => {
    setParams(p =>
      m.kind === 'lora' || m.kind === 'loras'
        ? { ...p, loras: [...p.loras.filter(l => l.id !== m.id), { id: m.id, name: m.name, strength: m.recommended_weight ?? 0.8 }] }
        : m.kind === 'vae'
          ? { ...p, vae: m.id }
          : m.kind === 'controlnet'
            ? { ...p, controlnet: m.id }
            : ['upscale', 'upscale_models'].includes(m.kind)
              ? { ...p, upscale: { ...p.upscale, enabled: true, mode: 'fast', model: m.id } }
              : { ...p, ...m.defaults, model: m.id },
    );
    navigate('draw');
  };

  const submit = async p => {
    const payload = { ...videoPayload(p, workflows.find(w => w.id === (p.workflow || p.workflow_id))), upscale: { ...p.upscale } };
    for (const key of ['width', 'height', 'target_width', 'target_height']) {
      if (payload.upscale[key] === '') delete payload.upscale[key];
    }
    delete payload.model_snapshot;
    delete payload.api_workflow;
    delete payload.workflow_hash;
    if ('workflow' in payload) payload.workflow_id = payload.workflow;
    const task = await post('/api/tasks', payload);
    if (task?.id) {
      setTasks(old => upsertTaskList(old, task, 40));
      window.dispatchEvent(new CustomEvent('studio:task-event', { detail: task }));
    } else {
      await refresh(['tasks']);
    }
    window.dispatchEvent(new Event('studio:gallery-dirty'));
    setNotice(`任务已提交 · ${task.id.slice(0, 8)}`);
    return task;
  };

  const common = {
    runtime,
    runtimeError: errors.runtime,
    runtimeAction,
    settings,
    models,
    tasks,
    presets,
    workflows,
    galleryProjects,
    refreshProjects,
    refresh,
    setNotice,
    submit,
    reuse,
    continueVideo,
    keyframeFromVideo,
    navigate,
  };

  useEffect(() => {
    const f = () => {
      refresh(['tasks']);
      refreshProjects();
    };
    window.addEventListener('studio:gallery-changed', f);
    return () => window.removeEventListener('studio:gallery-changed', f);
  }, [refresh, refreshProjects]);

  const activeCount = tasks.filter(isTaskActive).length;
  const cuBalance = runtime?.quota?.balance;
  const cuRate = runtime?.quota?.consumption_rate;

  return (
    <div className="app">
      <main className={`page page-${page}`}>
        <header className="page-title">
          <div className="row" style={{ gap: '18px' }}>
            <h1>{page === 'machine' ? '机器与额度' : pages.find(p => p[0] === page)?.[1]}</h1>
            <nav className="top-nav" aria-label="桌面导航">
              {pages.map(([id, label, Icon]) => (
                <button
                  key={id}
                  type="button"
                  aria-current={page === id ? 'page' : undefined}
                  className={page === id ? 'active' : ''}
                  onClick={() => navigate(id)}
                >
                  <Icon size={15} />
                  <span>{label}</span>
                  {id === 'tasks' && activeCount > 0 && <em className="nav-badge">{activeCount}</em>}
                </button>
              ))}
            </nav>
          </div>

          <div className="title-right">
            <button className={`gpu-entry ${page === 'machine' ? 'active' : ''}`} aria-label="GPU 机器与额度管理" onClick={() => navigate('machine')}>
              <Cpu size={15} />
              <span>{runtime?.gpu || 'GPU'}</span>
              <Status state={runtime?.state} />
              {cuBalance != null && (
                <span className="cu-top-pill">
                  {formatCu(cuBalance)}
                  {cuRate > 0 ? ` · ${Number(cuRate).toFixed(1)}/h` : ''}
                </span>
              )}
            </button>
            <IconButton icon={Settings} label="设置" onClick={() => setSettingsOpen(true)} />
          </div>
        </header>

        <ErrorBox error={errors.runtime} retry={() => refresh(['runtime'])} />
        <div hidden={page !== 'machine'}><Machine {...common} /></div>
        <div hidden={page !== 'tasks'}><Tasks {...common} active={page === 'tasks'} error={errors.tasks} /></div>
        <div hidden={page !== 'draw'}><Draw {...common} params={params} setParams={setParams} errors={errors} /></div>
        <div hidden={page !== 'models'}><Models {...common} onUse={useModel} error={errors.models} /></div>
        <div hidden={page !== 'gallery'}><Gallery {...common} active={page === 'gallery'} /></div>
      </main>

      <nav className="bottom-nav" aria-label="主导航">
        {pages.map(([id, label, Icon]) => (
          <button key={id} aria-current={page === id ? 'page' : undefined} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
            <Icon size={21} strokeWidth={page === id ? 2 : 1.5} />
            <span>{label}{id === 'tasks' && activeCount > 0 ? ` (${activeCount})` : ''}</span>
          </button>
        ))}
      </nav>

      {notice && <div className="toast" role="status"><Check size={16} />{notice}</div>}
      {settingsOpen && (
        <SettingsPanel settings={settings} theme={theme} setTheme={setTheme} onClose={() => setSettingsOpen(false)} refresh={refresh} setNotice={setNotice} initialError={errors.settings} />
      )}
      {login && (
        <div className="login-backdrop">
          <form
            className="login-card"
            onSubmit={async e => {
              e.preventDefault();
              setLogging(true);
              setLoginError('');
              try {
                await post('/api/auth/login', { password });
                setPassword('');
                setLogin(false);
                await refresh();
                await refreshProjects();
                window.dispatchEvent(new Event('studio:refresh'));
              } catch (e) {
                setLoginError(e.message);
              } finally {
                setLogging(false);
              }
            }}
          >
            <span className="brand-mark"><PenLine size={22} /></span>
            <h2>欢迎回到 Drawing Studio</h2>
            <p className="muted">输入管理员密码，打开你的私人工作台。</p>
            <label className="field">
              <span>管理员密码</span>
              <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required autoFocus />
            </label>
            <ErrorBox error={loginError} />
            <Button primary type="submit" disabled={logging}>
              <LogIn size={17} />
              {logging ? '正在登录…' : '进入工作台'}
            </Button>
          </form>
        </div>
      )}
    </div>
  );
}
