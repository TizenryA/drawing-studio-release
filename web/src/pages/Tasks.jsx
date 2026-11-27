import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  RefreshCw, Play, Copy, RotateCcw, X, Film, Image as ImageIcon,
  Sparkles, SlidersHorizontal, ChevronLeft, ChevronRight,
} from 'lucide-react';
import { api, post, enc } from '../api';
import { Button, Fold, Field, ErrorBox, Modal, TaskStatus, TaskProgress, isTaskActive, taskStage } from '../ui';
import {
  taskModel, taskTitle, taskTypeBadge, taskSpecLine,
  elapsed, elapsedSeconds, formatDuration, taskCostDisplay, formatCu, isVideoTask,
  normalizeTasksResponse, upsertTaskList,
} from '../taskData';
import { buildPageNumbers } from '../galleryPagination';
import MediaPreview, { isVideo } from '../components/MediaPreview';

export default function Tasks({ tasks, models, workflows = [], runtime, refresh, reuse, submit, error, active = true }) {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [stats, setStats] = useState({ active: 0, completed: 0, failed: 0, total_cu: 0, has_cu: false });
  const [modelOptions, setModelOptions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  const [detail, setDetail] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [actionError, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [model, setModel] = useState('');
  const [date, setDate] = useState('');
  const [showExtraFilters, setShowExtraFilters] = useState(false);
  const [page, setPage] = useState(1);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const seq = useRef(0);
  const detailSeq = useRef(0);
  const detailRef = useRef(detail);
  detailRef.current = detail;
  const activeRef = useRef(active);
  activeRef.current = active;
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const tasksPropRef = useRef(tasks);
  tasksPropRef.current = tasks;
  const reloadTimerRef = useRef(null);

  const matchesFilters = useCallback(t => {
    if (!t) return false;
    if (t.status === 'completed' && !t.results?.length) return false;
    if (statusFilter === 'active' && !isTaskActive(t)) return false;
    if (statusFilter && statusFilter !== 'active' && t.status !== statusFilter) return false;
    if (kindFilter) {
      const b = taskTypeBadge(t);
      if (kindFilter === 'video' && b.kind !== 'video') return false;
      if (kindFilter === 'image' && !['image', 'img2img', 'inpaint', 'hires'].includes(b.kind)) return false;
      if (kindFilter === 'refine' && !['video_refine', 'image_refine'].includes(b.kind)) return false;
    }
    if (model && t.params?.model !== model) return false;
    if (date && !(t.finished_at || t.created_at || '').startsWith(date)) return false;
    return true;
  }, [statusFilter, kindFilter, model, date]);

  const load = useCallback(async () => {
    const reqId = ++seq.current;
    setLoading(true);
    try {
      const query = new URLSearchParams({
        page: String(page),
        limit: '20',
        paginated: 'true',
        status: statusFilter,
        kind: kindFilter,
        model,
        date,
      });
      const res = normalizeTasksResponse(await api('/api/tasks?' + query), page, 20);
      if (seq.current === reqId) {
        setRows(res.items);
        setTotal(res.total);
        setTotalPages(res.totalPages);
        if (res.page !== page) setPage(res.page);
        if (res.stats) setStats(res.stats);
        if (res.models) setModelOptions(res.models);
        setLoadError('');
      }
    } catch (e) {
      if (seq.current === reqId) setLoadError(e.message);
    } finally {
      if (seq.current === reqId) setLoading(false);
    }
  }, [page, statusFilter, kindFilter, model, date]);

  useEffect(() => {
    if (active) {
      load();
    }
  }, [active, load]);

  useEffect(() => {
    const onReload = () => {
      if (activeRef.current) load();
    };
    window.addEventListener('studio:refresh', onReload);
    window.addEventListener('studio:gallery-changed', onReload);
    return () => {
      window.removeEventListener('studio:refresh', onReload);
      window.removeEventListener('studio:gallery-changed', onReload);
    };
  }, [load]);

  const scheduleReload = useCallback(() => {
    if (reloadTimerRef.current) clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = setTimeout(() => {
      reloadTimerRef.current = null;
      if (activeRef.current) load();
    }, 250);
  }, [load]);

  useEffect(() => () => {
    if (reloadTimerRef.current) clearTimeout(reloadTimerRef.current);
  }, []);

  useEffect(() => {
    const onTaskEvent = e => {
      const item = e?.detail;
      if (!item?.id) return;
      if (detailRef.current === item.id) {
        setDetailData(old => (old ? { ...old, ...item } : item));
      }
      if (!activeRef.current) return;

      const known = rowsRef.current.some(r => r.id === item.id);
      const terminal = ['completed', 'failed', 'cancelled'].includes(item.status);
      const matches = matchesFilters(item);

      if (known) {
        if (statusFilter === 'active' && !isTaskActive(item)) {
          setRows(old => old.filter(r => r.id !== item.id));
        } else {
          setRows(old => old.map(r => (r.id === item.id ? { ...r, ...item } : r)));
        }
      } else if (page === 1 && matches) {
        setRows(old => upsertTaskList(old, item, 20).slice(0, 20));
      }

      if (terminal || (!known && matches)) {
        scheduleReload();
      }
    };
    window.addEventListener('studio:task-event', onTaskEvent);
    return () => window.removeEventListener('studio:task-event', onTaskEvent);
  }, [page, statusFilter, matchesFilters, scheduleReload]);

  const openDetail = useCallback(async (id, seed = null) => {
    const reqId = ++detailSeq.current;
    const initial =
      seed ||
      rowsRef.current.find(r => r.id === id) ||
      tasksPropRef.current.find(t => t.id === id) ||
      null;
    setDetail(id);
    if (initial) setDetailData(initial);
    try {
      const full = await api(`/api/tasks/${enc(id)}`);
      if (detailSeq.current === reqId) {
        setDetailData(full);
      }
    } catch {}
  }, []);

  const closeDetail = useCallback(() => {
    detailSeq.current += 1;
    setDetail(null);
    setDetailData(null);
  }, []);

  useEffect(() => {
    const f = e => {
      if (e.detail) openDetail(e.detail);
    };
    window.addEventListener('studio:task', f);
    return () => window.removeEventListener('studio:task', f);
  }, [openDetail]);

  const activeRows = useMemo(() => rows.filter(isTaskActive), [rows]);
  const activeCount = Math.max(stats?.active || 0, activeRows.length);

  useEffect(() => {
    if (!activeCount && !isTaskActive(detailData)) return;
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [activeCount, detailData?.status]);

  const task = useMemo(() => {
    if (!detail) return null;
    const fromRow = rows.find(t => t.id === detail);
    if (detailData && detailData.id === detail) {
      return fromRow ? { ...detailData, ...fromRow, params: detailData.params || fromRow.params } : detailData;
    }
    return fromRow || tasks.find(t => t.id === detail) || null;
  }, [detail, detailData, rows, tasks]);

  const act = async fn => {
    setBusy(true);
    setError('');
    try {
      await fn();
      await Promise.all([load(), refresh(['tasks'])]);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const changePage = nextPage => {
    const clamped = Math.min(totalPages, Math.max(1, nextPage));
    if (clamped === page) return;
    setPage(clamped);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const pageNumbers = buildPageNumbers(page, totalPages);

  const renderCard = t => {
    const running = isTaskActive(t);
    const badge = taskTypeBadge(t);
    const title = taskTitle(t, workflows, models);
    const spec = taskSpecLine(t);
    const firstResult = t.results?.[0];
    const firstId = typeof firstResult === 'string' ? firstResult : firstResult?.id;
    const thumbUrl = firstId
      ? firstResult?.thumbnail_url || firstResult?.url || `/api/gallery/${enc(firstId)}/thumbnail`
      : null;
    const isVidResult = isVideoTask(t);
    const gpuName = t.actual_gpu || t.gpu || t.params?.gpu || (running ? runtime?.actual_gpu || runtime?.gpu : null);
    const cost = taskCostDisplay(t, runtime);
    const liveElapsed = elapsedSeconds(t, running ? nowMs : null);
    const hasSplit = t.sync_seconds != null || t.gen_seconds != null;

    return (
      <article key={t.id} className={`task-card ${running ? 'is-active' : ''} status-${t.status}`}>
        <button type="button" className="task-card-main" onClick={() => openDetail(t.id, t)}>
          <div className="task-thumb">
            {thumbUrl ? (
              <img alt="任务缩略图" src={thumbUrl} loading="lazy" />
            ) : (
              <span className="task-thumb-placeholder">
                {badge.kind.includes('refine') ? <Sparkles size={20} /> : isVidResult ? <Film size={20} /> : <ImageIcon size={20} />}
              </span>
            )}
            {isVidResult && firstResult?.duration && (
              <span className="video-chip-overlay">▶ {Number(firstResult.duration).toFixed(1)}s</span>
            )}
          </div>

          <div className="task-card-body">
            <div className="row between" style={{ gap: '8px' }}>
              <div className="row grow" style={{ gap: '7px', minWidth: 0 }}>
                <span className={`pill tone-${badge.tone}`}>{badge.label}</span>
                <strong className="truncate">{title}</strong>
              </div>
              <TaskStatus task={t} />
            </div>

            <p className="task-spec muted small">{spec}</p>

            {running ? (
              <div className="stack" style={{ gap: '4px', marginTop: '2px' }}>
                <TaskProgress task={t} />
                <div className="row wrap muted small" style={{ gap: '8px' }}>
                  <span>已运行 {liveElapsed != null ? formatDuration(liveElapsed) : '准备中'}</span>
                  {gpuName && <span>· {gpuName}</span>}
                  {cost.label !== '—' && <span>· {cost.label}</span>}
                </div>
              </div>
            ) : (
              <div className="row wrap muted small" style={{ gap: '6px 10px', marginTop: '2px' }}>
                <span>
                  耗时 {elapsed(t)}
                  {hasSplit && (
                    Number(t.sync_seconds) > 0
                      ? <>（同步 {formatDuration(t.sync_seconds)} / 生成 {formatDuration(t.gen_seconds)}）</>
                      : t.gen_seconds != null
                        ? <>（模型已复用 / 生成 {formatDuration(t.gen_seconds)}）</>
                        : null
                  )}
                </span>
                {gpuName && <span>· {gpuName}</span>}
                {cost.label !== '—' && <span className="cu-pill">{cost.label}</span>}
                <span>· {t.finished_at ? new Date(t.finished_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}</span>
              </div>
            )}

            {t.status === 'failed' && t.error && (
              <p className="error-text small truncate" style={{ marginTop: '2px' }}>{t.error}</p>
            )}
          </div>
        </button>

        {!running && t.status === 'failed' && (
          <div className="task-card-quick">
            <Button className="mini-btn" disabled={busy} onClick={() => act(() => submit(t.params))}>
              <RotateCcw size={13} />
              重试
            </Button>
          </div>
        )}
        {running && (
          <div className="task-card-quick">
            <Button className="mini-btn" disabled={busy} onClick={() => act(() => post(`/api/tasks/${enc(t.id)}/cancel`))}>
              <X size={13} />
              取消
            </Button>
          </div>
        )}
      </article>
    );
  };

  const renderDetailModal = () => {
    if (!task) return null;
    const badge = taskTypeBadge(task);
    const running = isTaskActive(task);
    const cost = taskCostDisplay(task, runtime);
    const liveTotal = elapsedSeconds(task, running ? nowMs : null);
    const stStage = taskStage(task);
    const wfName = workflows.find(w => w.id === (task.params?.workflow_id || task.params?.workflow))?.name || task.params?.workflow_id || task.params?.workflow;
    const stepIdx =
      task.status === 'completed'
        ? 4
        : stStage === 'archiving'
          ? 3
          : ['running', 'generating', 'submitting'].includes(stStage)
            ? 2
            : (stStage === 'syncing' || stStage === 'preparing')
              ? 1
              : 0;

    const paramRows = isVideoTask(task)
      ? badge.kind === 'video_refine'
        ? [
            ['任务类型', badge.label],
            ['工作流', wfName],
            ['源视频', task.params?.source_video],
            ['目标短边', task.params?.resolution ? `${task.params.resolution}p` : undefined],
            ['时序批 / 重叠', `${task.params?.frame_batch_size ?? 5} 帧 / 重叠 ${task.params?.temporal_overlap ?? 2} 帧`],
            ['色彩校正', task.params?.color_correction],
            ['BlockSwap', task.params?.blocks_to_swap],
            ['导出画质', task.params?.video_crf != null ? `CRF ${task.params.video_crf}` : undefined],
            ['GPU 硬件', task.actual_gpu || task.gpu],
            ['提交时间', task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : undefined],
            ['完成时间', task.finished_at ? new Date(task.finished_at).toLocaleString('zh-CN') : undefined],
          ]
        : [
            ['任务类型', badge.label],
            ['工作流', wfName],
            ['正向提示词', task.params?.prompt],
            ['视频规格', taskSpecLine(task)],
            ['关键帧帧号', Array.isArray(task.params?.keyframe_frames) ? task.params.keyframe_frames.join(', ') : undefined],
            ['续写源视频', task.params?.source_video],
            ['导出画质', task.params?.video_crf != null ? `CRF ${task.params.video_crf}` : undefined],
            ['Seed', task.params?.seed],
            ['Sampler / Scheduler', `${task.params?.sampler ?? '—'} / ${task.params?.scheduler ?? '—'}`],
            ['GPU 硬件', task.actual_gpu || task.gpu],
            ['提交时间', task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : undefined],
            ['完成时间', task.finished_at ? new Date(task.finished_at).toLocaleString('zh-CN') : undefined],
          ]
      : [
          ['任务类型', badge.label],
          ['模型 / 工作流', `${taskModel(task, models)}${wfName ? ` · ${wfName}` : ''}`],
          ['正向提示词', task.params?.prompt],
          ['负向提示词', task.params?.negative_prompt],
          ['LoRA', task.params?.loras?.map(l => `${l.name || l.id} · ${l.strength ?? l.weight}`).join(' / ') || undefined],
          ['输出规格', taskSpecLine(task)],
          ['Seed', task.params?.seed],
          ['CFG', task.params?.cfg],
          ['Sampler / Scheduler', `${task.params?.sampler ?? '—'} / ${task.params?.scheduler ?? '—'}`],
          ['GPU 硬件', task.actual_gpu || task.gpu],
          ['提交时间', task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : undefined],
          ['完成时间', task.finished_at ? new Date(task.finished_at).toLocaleString('zh-CN') : undefined],
        ];

    return (
      <Modal title="任务详情" onClose={closeDetail} wide>
        <div className="stack">
          <div className="row between wrap" style={{ gap: '8px' }}>
            <div className="row" style={{ gap: '8px', minWidth: 0 }}>
              <span className={`pill tone-${badge.tone}`}>{badge.label}</span>
              <strong className="truncate">{taskTitle(task, workflows, models)}</strong>
            </div>
            <TaskStatus task={task} />
          </div>

          <TaskProgress task={task} />
          <ErrorBox error={task.error} />
          <ErrorBox error={actionError} />

          {/* 4-card KPI strip */}
          <div className="kpi-grid">
            <div className="kpi-card">
              <small>总耗时</small>
              <strong>{liveTotal != null ? formatDuration(liveTotal) : '—'}</strong>
              <span>
                {task.sync_seconds != null || task.gen_seconds != null
                  ? (Number(task.sync_seconds) > 0
                      ? `同步 ${formatDuration(task.sync_seconds)} · 生成 ${formatDuration(task.gen_seconds)}`
                      : `模型已复用 · 生成 ${formatDuration(task.gen_seconds)}`)
                  : '同步 + 推理'}
              </span>
            </div>
            <div className="kpi-card">
              <small>任务消耗额度</small>
              <strong>{cost.label}</strong>
              <span>{cost.detail}</span>
            </div>
            <div className="kpi-card">
              <small>执行硬件</small>
              <strong className="truncate">{task.actual_gpu || task.gpu || (running ? runtime?.actual_gpu || runtime?.gpu : '未记录') || '未记录'}</strong>
              <span>{task.cu_balance_end != null ? `任务后余额 ${formatCu(task.cu_balance_end)}` : task.id.slice(0, 12)}</span>
            </div>
            <div className="kpi-card">
              <small>输出规格</small>
              <strong>{task.results?.[0]?.width ? `${task.results[0].width}×${task.results[0].height}` : `${task.params?.width || task.params?.resolution || '—'}`}</strong>
              <span>{task.results?.length ? `${task.results.length} ${badge.unit}成品` : badge.label}</span>
            </div>
          </div>

          {/* 4-step pipeline timeline */}
          <ol className="pipeline-steps">
            {['队列等待', '准备/同步', '推理生成', '保存归档'].map((label, idx) => (
              <li key={label} className={idx < stepIdx ? 'done' : idx === stepIdx && running ? 'active' : ''}>
                <span className="step-dot">{idx < stepIdx ? '✓' : idx + 1}</span>
                <small>{label}</small>
              </li>
            ))}
          </ol>

          <div className="detail-actions">
            <Button disabled={busy} onClick={() => { reuse(task.params); closeDetail(); }}>
              <Copy size={15} />
              复用参数
            </Button>
            {!running ? (
              <Button primary disabled={busy} onClick={() => act(() => submit(task.params))}>
                <Play size={15} />
                {task.status === 'failed' ? '失败重试' : '再次生成'}
              </Button>
            ) : (
              <Button disabled={busy} onClick={() => act(() => post(`/api/tasks/${enc(task.id)}/cancel`))}>
                <X size={15} />
                取消任务
              </Button>
            )}
          </div>

          {(task.results || []).length > 0 && (
            <div className="results">
              {task.results.map(r => {
                const id = typeof r === 'string' ? r : r.id;
                return isVideo(r) ? (
                  <div key={id} style={{ width: '100%' }}>
                    <MediaPreview item={r} alt="任务视频输出" />
                  </div>
                ) : (
                  <button
                    type="button"
                    key={id}
                    onClick={() => {
                      closeDetail();
                      window.dispatchEvent(new CustomEvent('studio:image', { detail: id }));
                    }}
                  >
                    <img alt="任务输出" src={r.thumbnail_url || r.url || `/api/gallery/${enc(id)}/thumbnail`} />
                  </button>
                );
              })}
            </div>
          )}

          <dl className="params-list">
            {paramRows
              .filter(([, v]) => v !== undefined && v !== '')
              .map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
          </dl>

          <Fold title="完整参数快照">
            <pre className="json-preview">{JSON.stringify(task.params, null, 2)}</pre>
          </Fold>
        </div>
      </Modal>
    );
  };

  return (
    <div className="stack">
      {/* Top KPI summary bar */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <small>正在进行</small>
          <strong>{activeCount}</strong>
          <span>{activeCount ? '实时 SSE 追踪中' : '当前队列空闲'}</span>
        </div>
        <div className="kpi-card">
          <small>已完成</small>
          <strong>{stats?.completed ?? 0}</strong>
          <span>成功归档输出</span>
        </div>
        <div className="kpi-card">
          <small>失败记录</small>
          <strong>{stats?.failed ?? 0}</strong>
          <span>{stats?.failed ? '支持一键重试' : '无异常失败'}</span>
        </div>
        <div className="kpi-card">
          <small>记录消耗 CU</small>
          <strong>{stats?.has_cu ? formatCu(stats.total_cu) : '—'}</strong>
          <span>{runtime?.quota?.balance != null ? `账户余额 ${formatCu(runtime.quota.balance)}` : '开机后采集额度'}</span>
        </div>
      </div>

      {/* Filters toolbar */}
      <div className="row between wrap" style={{ gap: '8px' }}>
        <div className="chips">
          {[
            ['', '全部'],
            ['active', `进行中${activeCount ? ` (${activeCount})` : ''}`],
            ['completed', '已完成'],
            ['failed', '失败'],
          ].map(([v, label]) => (
            <button key={v || 'all'} type="button" className={statusFilter === v ? 'selected' : ''} onClick={() => { setStatusFilter(v); setPage(1); }}>
              {label}
            </button>
          ))}
        </div>

        <div className="row" style={{ gap: '6px' }}>
          <div className="chips">
            {[
              ['', '全部类型'],
              ['video', '视频'],
              ['image', '图片'],
              ['refine', '精修'],
            ].map(([v, label]) => (
              <button key={v || 'all-kind'} type="button" className={kindFilter === v ? 'selected' : ''} onClick={() => { setKindFilter(v); setPage(1); }}>
                {label}
              </button>
            ))}
          </div>
          <Button onClick={() => setShowExtraFilters(v => !v)}>
            <SlidersHorizontal size={15} />
            筛选
          </Button>
          <Button onClick={() => { load(); refresh(['tasks', 'runtime']); }}>
            <RefreshCw size={15} />
            刷新
          </Button>
        </div>
      </div>

      {showExtraFilters && (
        <div className="filter-panel grid-two">
          <Field label="模型筛选">
            <select value={model} onChange={e => { setModel(e.target.value); setPage(1); }}>
              <option value="">全部模型</option>
              {modelOptions.map(id => (
                <option key={id} value={id}>{models.find(m => m.id === id)?.name || id}</option>
              ))}
            </select>
          </Field>
          <Field label="日期筛选">
            <input type="date" value={date} onChange={e => { setDate(e.target.value); setPage(1); }} />
          </Field>
          <Button onClick={() => { setStatusFilter(''); setKindFilter(''); setModel(''); setDate(''); setPage(1); }}>
            清除所有筛选
          </Button>
        </div>
      )}

      <ErrorBox error={loadError || error} retry={load} />
      <ErrorBox error={actionError} />
      {loading && !rows.length && <p className="muted small">正在读取任务队列…</p>}

      <div className="task-card-list">
        {rows.map(renderCard)}
        {!rows.length && !loading && <p className="muted" style={{ padding: '24px 0', textAlign: 'center' }}>没有符合条件的任务记录。</p>}
      </div>

      {total > 0 && totalPages > 1 && (
        <div className="pagination-bar" aria-label="任务分页">
          <span className="muted small">第 {page} / {totalPages} 页（共 {total} 条）</span>
          <div className="page-numbers">
            <Button className="mini-btn" disabled={page <= 1 || loading} onClick={() => changePage(page - 1)}>
              <ChevronLeft size={14} />
              上一页
            </Button>
            {pageNumbers.map((item, idx) =>
              item === '...' ? (
                <span key={`e-${idx}`} className="page-ellipsis">…</span>
              ) : (
                <button
                  key={item}
                  type="button"
                  className={`page-btn ${item === page ? 'selected' : ''}`}
                  disabled={loading}
                  onClick={() => changePage(item)}
                >
                  {item}
                </button>
              ),
            )}
            <Button className="mini-btn" disabled={page >= totalPages || loading} onClick={() => changePage(page + 1)}>
              下一页
              <ChevronRight size={14} />
            </Button>
          </div>
        </div>
      )}

      {renderDetailModal()}
    </div>
  );
}