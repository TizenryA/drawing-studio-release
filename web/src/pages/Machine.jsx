import React, { useState, useEffect } from 'react';
import { Cpu, Power, ArrowUpRight, RefreshCw, Check, Terminal, RotateCcw, Coins } from 'lucide-react';
import { api, list, post } from '../api';
import { Button, ErrorBox, Field, Section, Status, Fold, TaskStatus, TaskProgress, isTaskActive } from '../ui';
import { getMachineViewState } from '../machineState';
import { taskTitle, taskTypeBadge, taskCostDisplay, formatCu } from '../taskData';

const GPU_HINTS = {
  G4: '96 GB 显存 · 视频与精修首推',
  H100: '80 GB 显存 · 旗舰算力',
  A100: '40 GB 显存 · 均衡高速',
  L4: '24 GB 显存 · 轻量高性价比',
  T4: '16 GB 显存 · 入门测试',
};

export default function Machine({ runtime, runtimeError, runtimeAction, settings, tasks, models = [], workflows = [], refresh, openSettings }) {
  const [gpus, setGpus] = useState([]);
  const [gpuInfo, setGpuInfo] = useState(null);
  const [gpu, setGpu] = useState('');
  const [mode, setMode] = useState(settings.mode || 'temporary');
  const [error, setError] = useState('');
  const [gpuError, setGpuError] = useState('');
  const [busy, setBusy] = useState('');
  const [logs, setLogs] = useState(null);

  const loadGpus = async () => {
    try {
      const response = await api('/api/runtime/gpus');
      const result = Array.isArray(response) ? response : list(response.gpus);
      setGpus(result);
      if (!Array.isArray(response) && response) setGpuInfo(response);
      setGpu(
        old =>
          old ||
          runtime?.gpu ||
          (result.some(x => (typeof x === 'string' ? x : x.id || x.name) === 'G4')
            ? 'G4'
            : typeof result[0] === 'string'
              ? result[0]
              : result[0]?.id || result[0]?.name) ||
          '',
      );
      setGpuError('');
    } catch (e) {
      setGpuError(e.message);
    }
  };

  useEffect(() => {
    loadGpus();
    const listener = () => loadGpus();
    window.addEventListener('studio:refresh', listener);
    return () => window.removeEventListener('studio:refresh', listener);
  }, []);

  useEffect(() => {
    if (settings.mode) setMode(settings.mode);
  }, [settings.mode]);

  const action = async (name, body) => {
    setBusy(name);
    setError('');
    runtimeAction();
    try {
      await post('/api/runtime/' + name, body);
      await refresh(['runtime']);
      loadGpus();
    } catch (e) {
      setError(e.message);
      await refresh(['runtime']);
    } finally {
      setBusy('');
    }
  };

  const {
    uncertain,
    isUnknown,
    isDisconnected,
    active,
    ready,
    canStart,
    needsReset,
    statusSubtitle,
    showMemory,
    memoryPercent,
  } = getMachineViewState(runtime, runtimeError);
  const steps = ['分配 GPU', '准备环境', '启动 ComfyUI', '服务可用'];
  const at = ({ allocating: 0, bootstrapping: 1, syncing: 1, starting: 2, starting_comfy: 2, ready: 3 })[runtime?.state] ?? -1;

  const balance = runtime?.quota?.balance ?? gpuInfo?.balance;
  const rate = runtime?.quota?.consumption_rate ?? gpuInfo?.consumption_rate;
  const sessionCu = runtime?.session_cu_used;
  const activeTasks = tasks.filter(isTaskActive);

  return (
    <div className="machine-layout">
      <div className="stack">
        {settings?.colab_configured === false && (
          <div className="machine-auth-alert" role="status">
            <span>尚未绑定 Google Colab 账号，开机前需先完成 OAuth 授权或导入凭据。</span>
            {openSettings && (
              <Button primary className="mini-btn" onClick={openSettings}>
                前往设置完成授权
              </Button>
            )}
          </div>
        )}
        <section className="machine-card">
          <div className="row between">
            <Status state={runtime?.state} />
            <Button className="mini-btn" onClick={() => { loadGpus(); refresh(['runtime']); }} title="刷新额度与状态">
              <RefreshCw size={13} />
              刷新额度
            </Button>
          </div>
          <h2>{runtime?.actual_gpu || runtime?.gpu || '等待选择 GPU'}</h2>
          <p className="muted">
            {statusSubtitle}
          </p>

          {/* 4-metric overview */}
          <div className="machine-metrics-grid">
            <div className="kpi-card">
              <small><Coins size={12} style={{ verticalAlign: '-2px', marginRight: '4px' }} />账号剩余额度</small>
              <strong>{balance != null ? formatCu(balance) : '—'}</strong>
              <span>{gpuInfo?.assignments_count != null ? `活跃实例 ${gpuInfo.assignments_count} 个` : 'Colab Compute Units'}</span>
            </div>
            <div className="kpi-card">
              <small>实时耗电与本次已耗</small>
              <strong>{sessionCu != null && sessionCu > 0 ? formatCu(sessionCu) : rate > 0 ? `${Number(rate).toFixed(2)} CU/h` : '0.00 CU/h'}</strong>
              <span>{rate > 0 ? `当前费率 ${Number(rate).toFixed(2)} CU/h` : '未开机无消耗'}</span>
            </div>
            <div className="kpi-card">
              <small>{isDisconnected ? '本次累计运行' : '运行时长'}</small>
              <strong>
                {runtime?.uptime != null && runtime.uptime > 0
                  ? `${Math.floor(runtime.uptime / 3600)} 小时 ${Math.floor((runtime.uptime % 3600) / 60)} 分钟`
                  : '未运行'}
              </strong>
              <span>{isDisconnected ? '已断开，停止计费' : ((active && runtime?.mode ? runtime.mode : settings.mode) || 'temporary') === 'temporary' ? `空闲 ${settings.idle_minutes ?? 60} 分自动关机` : '持久模式'}</span>
            </div>
            <div className="kpi-card">
              <small>任务队列</small>
              <strong>{runtime?.queue_count ?? 0} 个任务</strong>
              <span>{activeTasks.length ? '正在处理中' : '当前队列空闲'}</span>
            </div>
          </div>

          <div className="memory-label">
            <span>显存使用</span>
            <strong>
              {showMemory
                ? `${(runtime.memory_used / 1073741824).toFixed(1)} / ${runtime.memory_total ? (runtime.memory_total / 1073741824).toFixed(1) : '—'} GB`
                : '— / — GB'}
            </strong>
          </div>
          <progress max="100" value={memoryPercent} style={{ marginBottom: '14px' }} />

          {runtime?.error && <ErrorBox error={runtime.error} />}
          <a
            className={`primary ${!ready || !runtime?.comfy_url ? 'disabled' : ''}`}
            href={ready ? runtime.comfy_url : undefined}
            target="_blank"
            rel="noreferrer"
            aria-disabled={!ready}
          >
            <span>打开 ComfyUI 原生界面</span>
            <ArrowUpRight size={18} />
          </a>
        </section>

        <Section title="运行配置">
          <ErrorBox error={gpuError} retry={loadGpus} />
          <Field label="GPU 型号">
            <select value={gpu} onChange={e => setGpu(e.target.value)} disabled={active && !isUnknown}>
              <option value="">{gpus.length ? '选择可用 GPU' : '尚无可用 GPU 信息'}</option>
              {gpus.map(g => {
                const id = typeof g === 'string' ? g : g.id || g.name;
                const label = typeof g === 'string' ? g : g.name || g.id;
                const hint = g.memory ? `${g.memory} GB` : GPU_HINTS[String(id).toUpperCase()];
                return (
                  <option key={id} value={id} disabled={g.available === false || g.eligible === false}>
                    {label}
                    {hint ? ` · ${hint}` : ''}
                    {g.available === false || g.eligible === false ? ' · 暂不可用' : ''}
                  </option>
                );
              })}
            </select>
          </Field>

          <div className="mode-options">
            {[
              ['temporary', '临时模式', `空闲 ${settings.idle_minutes ?? 60} 分钟自动关闭，省额度`],
              ['persistent', '持久模式', '不主动关机，仍可能被 Colab 回收（用完请手动关机）'],
            ].map(([v, n, d]) => (
              <label key={v} className={`choice ${mode === v ? 'selected' : ''}`}>
                <input type="radio" name="runtime-mode" value={v} checked={mode === v} onChange={() => setMode(v)} disabled={active} />
                <span>
                  <strong>{n}</strong>
                  <small>{d}</small>
                </span>
              </label>
            ))}
          </div>
          <ErrorBox error={error} />
          <div className="row actions">
            {!active && !needsReset && (
              <Button primary onClick={() => action('start', { gpu, mode })} disabled={!canStart || !gpu || !!busy}>
                <Power size={17} />
                {busy === 'start' ? '正在申请…' : uncertain ? '正在确认状态…' : '申请开机'}
              </Button>
            )}
            {active && (
              <Button
                onClick={() => {
                  if (confirm('关闭机器将中断运行中的任务，确定关闭？')) action('stop');
                }}
                disabled={!!busy || uncertain}
              >
                <Power size={17} />
                {busy === 'stop' ? '正在关闭…' : '关闭机器'}
              </Button>
            )}
            {needsReset && (
              <Button primary={!active} onClick={() => action('reset')} disabled={!!busy}>
                <RotateCcw size={16} />
                {busy === 'reset' ? '正在复位…' : isUnknown ? '强制复位' : '复位状态'}
              </Button>
            )}
            {ready && (
              <Button onClick={() => action('restart')} disabled={!!busy || uncertain}>
                <RefreshCw size={16} />
                重启 ComfyUI
              </Button>
            )}
          </div>
        </Section>
      </div>

      <div className="stack">
        <Fold title={ready ? '启动进度 · 已完成' : '启动进度'} memory={ready ? 'startup-completed' : 'startup-running'} open={!ready}>
          <ol className="startup-steps">
            {steps.map((s, i) => (
              <li key={s} className={i <= at ? 'done' : ''}>
                <span>{i < at || ready ? <Check size={14} /> : i + 1}</span>
                <div>
                  <strong>{s}</strong>
                  <small>{i < at || ready ? '已完成' : i === at ? '正在进行' : uncertain ? '状态待确认' : '等待中'}</small>
                </div>
              </li>
            ))}
          </ol>
        </Fold>

        <Section title={`当前进行中任务 · ${activeTasks.length}`}>
          <div className="task-list">
            {activeTasks.length ? (
              activeTasks.map(t => {
                const badge = taskTypeBadge(t);
                const cost = taskCostDisplay(t, runtime);
                return (
                  <div key={t.id} className="task-mini">
                    <div className="row between" style={{ gap: '8px' }}>
                      <span className="row grow" style={{ gap: '6px', minWidth: 0 }}>
                        <span className={`pill tone-${badge.tone}`}>{badge.label}</span>
                        <strong className="truncate">{taskTitle(t, workflows, models)}</strong>
                      </span>
                      <TaskStatus task={t} />
                    </div>
                    <TaskProgress task={t} />
                    {cost.label !== '—' && <p className="muted small">消耗估算：{cost.label}</p>}
                  </div>
                );
              })
            ) : (
              <p className="muted">当前没有正在进行的任务。</p>
            )}
          </div>
        </Section>

        <Fold title="运行日志">
          <Button
            onClick={async () => {
              try {
                setLogs(list(await api('/api/runtime/logs')));
                setError('');
              } catch (e) {
                setError(e.message);
              }
            }}
          >
            <Terminal size={16} />
            刷新日志
          </Button>
          <div className="logs">
            {(logs || runtime?.logs || []).length ? (
              (logs || runtime.logs).map((l, i) => (
                <div key={i}>
                  {typeof l === 'string'
                    ? l
                    : `${l.time ? new Date(Number(l.time) * 1000).toLocaleTimeString('zh-CN') + ' · ' : ''}${l.message || l.text || JSON.stringify(l)}`}
                </div>
              ))
            ) : (
              <p>暂无日志。</p>
            )}
          </div>
        </Fold>
      </div>
    </div>
  );
}
