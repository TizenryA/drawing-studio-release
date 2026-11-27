import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Search, Heart, Upload, Download, Trash2, Check, Maximize2, Copy, Play,
  RefreshCw, ArrowUpRight, SlidersHorizontal, Film, Folder, FolderPlus, Pencil,
  ChevronLeft, ChevronRight, CloudDownload,
} from 'lucide-react';
import { api, post, patch, remove, enc, download } from '../api';
import { Button, IconButton, Field, Empty, ErrorBox, Modal, words, Fold } from '../ui';
import { UpscaleModal, VideoSeedVR2Modal, baseUpscale } from '../components/Pickers';
import MediaPreview, { isVideo } from '../components/MediaPreview';
import {
  UNASSIGNED_PROJECT,
  buildPageNumbers,
  normalizeGalleryResponse,
  projectLabel,
} from '../galleryPagination';

const readStoredLimit = () => {
  try {
    const val = Number(localStorage.getItem('studio-gallery-limit'));
    return [24, 36, 48, 72].includes(val) ? val : 36;
  } catch {
    return 36;
  }
};

export default function Gallery({
  runtime,
  models,
  workflows,
  reuse,
  continueVideo,
  keyframeFromVideo,
  submit,
  setNotice,
  active = true,
  galleryProjects = { projects: [], total: 0, unassigned_count: 0 },
  refreshProjects,
}) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [mediaFilter, setMediaFilter] = useState('');
  const [project, setProject] = useState('');
  const [model, setModel] = useState('');
  const [date, setDate] = useState('');
  const [tag, setTag] = useState('');
  const [lora, setLora] = useState('');
  const [order, setOrder] = useState('desc');
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(readStoredLimit);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [filters, setFilters] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState([]);
  const [detail, setDetail] = useState(null);
  const [upscale, setUpscale] = useState(null);
  const [videoUpscale, setVideoUpscale] = useState(null);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [projectModal, setProjectModal] = useState(null); // null | { mode: 'create' | 'rename', name: '', original?: string }
  const [batchProject, setBatchProject] = useState('');
  const [batchTags, setBatchTags] = useState('');
  const [concatTrim, setConcatTrim] = useState(5);

  const seq = useRef(0);
  const detailSeq = useRef(0);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const activeRef = useRef(active);
  activeRef.current = active;
  const dirtyRef = useRef(true);
  const prevTextKeyRef = useRef(`${q}|${tag}|${date}`);

  const projectList = galleryProjects?.projects || [];
  const activeProjectMeta = project && project !== UNASSIGNED_PROJECT
    ? projectList.find(p => p.name === project)
    : null;

  const load = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    try {
      const query = new URLSearchParams({
        q,
        favorite: String(favorite),
        project,
        media_type: mediaFilter,
        model,
        lora,
        date,
        tag,
        order,
        page: String(page),
        limit: String(limit),
        paginated: 'true',
      });
      const res = normalizeGalleryResponse(await api('/api/gallery?' + query), page, limit);
      if (seq.current === id) {
        setItems(res.items);
        setTotal(res.total);
        setTotalPages(res.totalPages);
        if (res.page !== page) setPage(res.page);
        dirtyRef.current = false;
        setError('');
      }
    } catch (e) {
      if (seq.current === id) setError(e.message);
    } finally {
      if (seq.current === id) setLoading(false);
    }
  }, [q, favorite, project, mediaFilter, model, lora, date, tag, order, page, limit]);

  useEffect(() => {
    try {
      localStorage.setItem('studio-gallery-limit', String(limit));
    } catch {}
  }, [limit]);

  useEffect(() => {
    const textKey = `${q}|${tag}|${date}`;
    const textChanged = prevTextKeyRef.current !== textKey;
    prevTextKeyRef.current = textKey;

    if (!active) {
      dirtyRef.current = true;
      return;
    }
    const timer = setTimeout(() => {
      load();
    }, textChanged ? 200 : 0);
    return () => clearTimeout(timer);
  }, [active, load, q, tag, date]);

  useEffect(() => {
    if (active) {
      refreshProjects?.();
    }
  }, [active, refreshProjects]);

  useEffect(() => {
    const onDirty = () => {
      if (activeRef.current) {
        load();
        refreshProjects?.();
      } else {
        dirtyRef.current = true;
      }
    };
    window.addEventListener('studio:refresh', onDirty);
    window.addEventListener('studio:gallery-dirty', onDirty);
    return () => {
      window.removeEventListener('studio:refresh', onDirty);
      window.removeEventListener('studio:gallery-dirty', onDirty);
    };
  }, [load, refreshProjects]);

  const open = useCallback(async (id, seed = null) => {
    const reqId = ++detailSeq.current;
    const initial = seed || itemsRef.current.find(item => item.id === id);
    if (initial) {
      setDetail(initial);
    }
    try {
      const full = await api(`/api/gallery/${enc(id)}`);
      if (detailSeq.current === reqId) {
        setDetail(full);
        setError('');
      }
    } catch (e) {
      if (detailSeq.current === reqId) {
        setError(e.message);
      }
    }
  }, []);

  const closeDetail = useCallback(() => {
    detailSeq.current += 1;
    setDetail(null);
  }, []);

  useEffect(() => {
    const listener = e => {
      if (e.detail) open(e.detail);
    };
    window.addEventListener('studio:image', listener);
    return () => window.removeEventListener('studio:image', listener);
  }, [open]);

  const selectProject = next => {
    setProject(next);
    setPage(1);
    setSelected([]);
  };

  const changePage = nextPage => {
    const clamped = Math.min(totalPages, Math.max(1, nextPage));
    if (clamped === page) return;
    setPage(clamped);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const update = async (id, data) => {
    const next = await patch(`/api/gallery/${enc(id)}`, data);
    setItems(old => old.map(i => (i.id === id ? { ...i, ...next } : i)));
    if (detail?.id === id) setDetail(old => (old ? { ...old, ...next } : next));
    if ('project' in data || 'album' in data) {
      await refreshProjects?.();
      await load();
    }
    return next;
  };

  const batch = async (action, value) => {
    setBusy(true);
    setError('');
    try {
      await post('/api/gallery/batch', { ids: selected, action, value });
      if (action === 'delete') window.dispatchEvent(new Event('studio:gallery-changed'));
      if (action === 'project' || action === 'album' || action === 'delete') {
        await refreshProjects?.();
      }
      setSelected([]);
      await load();
      setNotice('批量操作已完成');
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const syncDrive = async () => {
    setSyncing(true);
    setError('');
    try {
      const payload = project ? { project } : {};
      const res = await post('/api/gallery/sync', payload);
      await refreshProjects?.();
      await load();
      setNotice(`Drive 同步完成：发现 ${res.projects_found || 0} 个项目文件夹，新增 ${res.added || 0} 项，更新 ${res.moved || 0} 项`);
    } catch (e) {
      setError(e.message);
    } finally {
      setSyncing(false);
    }
  };

  const handleDeleteProject = async () => {
    if (!project || project === UNASSIGNED_PROJECT) return;
    const count = activeProjectMeta?.count || 0;
    const msg = count > 0
      ? `确定删除项目「${project}」？项目内的 ${count} 项作品将移入「未分类」，不会删除作品文件。`
      : `确定删除空项目「${project}」及对应的 Drive 空文件夹？`;
    if (!confirm(msg)) return;
    setBusy(true);
    setError('');
    try {
      await remove(`/api/gallery/projects/${enc(project)}?unassign=true`);
      setNotice(`已删除项目「${project}」`);
      selectProject('');
      await refreshProjects?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const selectedItems = selected.map(id => items.find(i => i.id === id)).filter(Boolean);
  const canConcat = selectedItems.length >= 2 && selectedItems.every(i => isVideo(i));

  const concatSelected = async () => {
    setBusy(true);
    setError('');
    try {
      const created = await post('/api/gallery/concat', {
        ids: selected,
        trim_overlap_frames: Number(concatTrim) || 0,
        ...(project && project !== UNASSIGNED_PROJECT ? { project } : {}),
      });
      setSelected([]);
      setSelecting(false);
      await refreshProjects?.();
      await load();
      setNotice(`已无痕拼接 ${selectedItems.length} 段视频并归档`);
      if (created?.id) open(created.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const pageNumbers = buildPageNumbers(page, totalPages);

  return (
    <div className="stack">
      {/* Project Folder Bar */}
      <div className="project-bar">
        <div className="project-chips" role="tablist" aria-label="项目文件夹">
          <button
            type="button"
            role="tab"
            aria-selected={project === ''}
            className={`project-chip ${project === '' ? 'selected' : ''}`}
            onClick={() => selectProject('')}
          >
            <span>全部</span>
            <span className="project-count">{galleryProjects?.total ?? total}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={project === UNASSIGNED_PROJECT}
            className={`project-chip ${project === UNASSIGNED_PROJECT ? 'selected' : ''}`}
            onClick={() => selectProject(UNASSIGNED_PROJECT)}
          >
            <span>未分类</span>
            <span className="project-count">{galleryProjects?.unassigned_count ?? 0}</span>
          </button>
          {projectList.map(proj => (
            <button
              key={proj.name}
              type="button"
              role="tab"
              aria-selected={project === proj.name}
              className={`project-chip ${project === proj.name ? 'selected' : ''}`}
              onClick={() => selectProject(proj.name)}
            >
              <Folder size={13} />
              <span>{proj.name}</span>
              <span className="project-count">{proj.count}</span>
            </button>
          ))}
        </div>
        <div className="row wrap" style={{ gap: '6px' }}>
          <Button className="mini-btn" onClick={() => setProjectModal({ mode: 'create', name: '' })}>
            <FolderPlus size={14} />
            新建项目
          </Button>
          <Button className="mini-btn" disabled={syncing} onClick={syncDrive}>
            <CloudDownload size={14} />
            {syncing ? '同步中…' : '同步 Drive'}
          </Button>
          {project && project !== UNASSIGNED_PROJECT && (
            <>
              <Button className="mini-btn" disabled={busy} onClick={() => setProjectModal({ mode: 'rename', name: project, original: project })}>
                <Pencil size={13} />
                重命名
              </Button>
              <Button className="mini-btn" disabled={busy} onClick={handleDeleteProject}>
                <Trash2 size={13} />
                删除项目
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search">
          <Search size={17} />
          <input value={q} onChange={e => { setQ(e.target.value); setPage(1); }} placeholder="搜索提示词、模型或标签" />
        </div>
        <IconButton icon={SlidersHorizontal} label="筛选图库" onClick={() => setFilters(!filters)} />
        <IconButton icon={RefreshCw} label="刷新图库" onClick={() => { refreshProjects?.(); load(); }} />
      </div>

      <div className="row between wrap" style={{ gap: '8px' }}>
        <div className="chips">
          <button type="button" className={!favorite && !mediaFilter ? 'selected' : ''} onClick={() => { setFavorite(false); setMediaFilter(''); setPage(1); }}>
            全部
          </button>
          <button type="button" className={mediaFilter === 'video' ? 'selected' : ''} onClick={() => { setMediaFilter(v => (v === 'video' ? '' : 'video')); setPage(1); }}>
            <Film size={14} />
            视频
          </button>
          <button type="button" className={mediaFilter === 'image' ? 'selected' : ''} onClick={() => { setMediaFilter(v => (v === 'image' ? '' : 'image')); setPage(1); }}>
            图片
          </button>
          <button type="button" className={favorite ? 'selected' : ''} onClick={() => { setFavorite(v => !v); setPage(1); }}>
            <Heart size={14} />
            收藏
          </button>
          <button type="button" className={order === 'desc' ? 'selected' : ''} onClick={() => { setOrder(order === 'desc' ? 'asc' : 'desc'); setPage(1); }}>
            {order === 'desc' ? '时间: 最新 ↓' : '时间: 最早 ↑'}
          </button>
          {model && (
            <button type="button" className="selected" onClick={() => { setModel(''); setPage(1); }}>
              模型: {models.find(m => m.id === model)?.name || model} ✕
            </button>
          )}
        </div>
        <div className="row">
          <Button onClick={() => setImporting(true)}><Upload size={15} />导入</Button>
          <Button onClick={() => { setSelecting(!selecting); setSelected([]); }}>{selecting ? '完成' : '多选'}</Button>
        </div>
      </div>

      {filters && (
        <div className="filter-panel grid-two">
          <Field label="项目">
            <select value={project} onChange={e => selectProject(e.target.value)}>
              <option value="">全部项目</option>
              <option value={UNASSIGNED_PROJECT}>未分类</option>
              {projectList.map(proj => <option key={proj.name} value={proj.name}>{proj.name}（{proj.count}）</option>)}
            </select>
          </Field>
          <Field label="标签"><input value={tag} onChange={e => { setTag(e.target.value); setPage(1); }} placeholder="输入标签" /></Field>
          <Field label="模型">
            <select value={model} onChange={e => { setModel(e.target.value); setPage(1); }}>
              <option value="">全部模型</option>
              {models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
          <Field label="LoRA">
            <select value={lora} onChange={e => { setLora(e.target.value); setPage(1); }}>
              <option value="">全部 LoRA</option>
              {models.filter(m => ['lora', 'loras'].includes(m.kind)).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
          <Field label="时间排序">
            <select value={order} onChange={e => { setOrder(e.target.value); setPage(1); }}>
              <option value="desc">最新生成在前</option>
              <option value="asc">最早生成在前</option>
            </select>
          </Field>
          <Field label="生成日期"><input type="date" value={date} onChange={e => { setDate(e.target.value); setPage(1); }} /></Field>
          <Button onClick={() => { setProject(''); setModel(''); setDate(''); setTag(''); setQ(''); setLora(''); setMediaFilter(''); setFavorite(false); setOrder('desc'); setPage(1); }}>
            清除筛选
          </Button>
        </div>
      )}

      {selecting && (
        <div className="batch-panel stack">
          <div className="row between">
            <strong>已选择 {selected.length} 项（视频按勾选顺序拼接）</strong>
            <Button onClick={() => setSelected(selected.length === items.length ? [] : items.map(i => i.id))}>本页全选 / 取消</Button>
          </div>
          <div className="row wrap">
            <Button
              disabled={!selected.length || busy}
              onClick={async () => {
                if (__DEMO_MODE__) {
                  setNotice('Demo 模式已模拟批量打包下载（单张作品可在详情页直接下载）');
                  return;
                }
                setBusy(true);
                try {
                  const response = await fetch('/api/gallery/batch', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: selected, action: 'download' }),
                  });
                  if (!response.ok) throw new Error('批量下载失败（HTTP ' + response.status + '）');
                  const u = URL.createObjectURL(await response.blob());
                  const a = document.createElement('a');
                  a.href = u;
                  a.download = 'drawing-studio.zip';
                  a.click();
                  setTimeout(() => URL.revokeObjectURL(u), 10000);
                } catch (e) {
                  setError(e.message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Download size={15} />
              下载
            </Button>
            <Button disabled={!selected.length || busy} onClick={() => batch('favorite', true)}>
              <Heart size={15} />
              收藏
            </Button>
            <Button
              disabled={!selected.length || busy}
              onClick={() => {
                if (confirm(`永久删除选中的 ${selected.length} 项作品及 Drive 文件？`)) batch('delete');
              }}
            >
              <Trash2 size={15} />
              删除
            </Button>
          </div>
          {canConcat && (
            <div className="input-action">
              <select aria-label="拼接裁剪重叠帧数" value={concatTrim} onChange={e => setConcatTrim(Number(e.target.value))}>
                <option value={0}>不裁剪直接拼接（0 帧）</option>
                <option value={1}>裁剪 1 帧（单尾帧续写去重）</option>
                <option value={5}>裁剪 5 帧（路线B 5帧续写去重）</option>
                <option value={22}>裁剪 22 帧（路线B 22帧续写去重）</option>
              </select>
              <Button primary disabled={busy} onClick={concatSelected}>
                {busy ? '正在拼接…' : `无痕拼接 ${selected.length} 段视频`}
              </Button>
            </div>
          )}
          <div className="input-action">
            <select aria-label="批量目标项目" value={batchProject} onChange={e => setBatchProject(e.target.value)}>
              <option value="">移入未分类</option>
              {projectList.map(proj => <option key={proj.name} value={proj.name}>{proj.name}</option>)}
            </select>
            <Button disabled={!selected.length || busy} onClick={() => batch('project', batchProject)}>移动到项目</Button>
          </div>
          <div className="input-action">
            <input placeholder="标签，逗号分隔" aria-label="批量标签" value={batchTags} onChange={e => setBatchTags(e.target.value)} />
            <Button disabled={!selected.length || busy} onClick={() => batch('tag', words(batchTags))}>设置标签</Button>
          </div>
        </div>
      )}

      <ErrorBox error={error} retry={load} />
      {loading && <p className="muted small">正在读取图库…</p>}

      {!items.length && !loading && !error ? (
        <Empty>还没有作品。生成、导入或点击「同步 Drive」的作品会显示在这里。</Empty>
      ) : (
        <div className="gallery-grid">
          {items.map(i => (
            <article className="gallery-tile" key={i.id}>
              <button
                className="gallery-image"
                onClick={() => (selecting ? setSelected(s => (s.includes(i.id) ? s.filter(v => v !== i.id) : [...s, i.id])) : open(i.id, i))}
                aria-label={selecting ? '选择作品' : '打开作品详情'}
              >
                <img src={i.thumbnail_url || i.url} alt={i.params?.prompt || '作品预览'} loading="lazy" width={i.width || 512} height={i.height || 768} />
              </button>
              {isVideo(i) && (
                <span className="video-tile-badge">
                  ▶ {i.duration ? `${Number(i.duration).toFixed(1)}s` : '视频'}
                </span>
              )}
              {selecting ? (
                <span className={`select-check ${selected.includes(i.id) ? 'checked' : ''}`}>
                  <Check size={16} />
                </span>
              ) : (
                <button
                  className={`image-favorite ${i.favorite ? 'filled' : ''}`}
                  aria-label={i.favorite ? '取消收藏作品' : '收藏作品'}
                  onClick={async () => {
                    try {
                      await update(i.id, { favorite: !i.favorite });
                    } catch (e) {
                      setError(e.message);
                    }
                  }}
                >
                  <Heart size={18} />
                </button>
              )}
              {i.archive_status === 'retry' && <span className="archive-badge">Drive 待重试</span>}
            </article>
          ))}
        </div>
      )}

      {total > 0 && (
        <div className="pagination-bar" aria-label="图库分页">
          <span className="muted small">
            共 {total} 项 · 第 {page} / {totalPages} 页
          </span>
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
          <label className="row" style={{ gap: '6px' }}>
            <span className="muted small">每页</span>
            <select
              aria-label="每页数量"
              className="page-size-select"
              value={limit}
              onChange={e => {
                setLimit(Number(e.target.value));
                setPage(1);
              }}
            >
              {[24, 36, 48, 72].map(n => <option key={n} value={n}>{n} 项</option>)}
            </select>
          </label>
        </div>
      )}

      {detail && (
        <ImageDetail
          image={detail}
          items={items}
          projects={projectList}
          onClose={closeDetail}
          update={update}
          reuse={reuse}
          continueVideo={continueVideo}
          keyframeFromVideo={keyframeFromVideo}
          submit={submit}
          onUpscale={() => { setUpscale(detail); closeDetail(); }}
          onVideoUpscale={() => { setVideoUpscale(detail); closeDetail(); }}
          onDelete={async () => {
            await remove(`/api/gallery/${enc(detail.id)}`);
            window.dispatchEvent(new Event('studio:gallery-changed'));
            closeDetail();
            await refreshProjects?.();
            await load();
          }}
          open={open}
          setNotice={setNotice}
        />
      )}
      {upscale && <UpscaleModal image={upscale} models={models} workflows={workflows} value={{ ...baseUpscale, enabled: true, mode: 'fast' }} onClose={() => setUpscale(null)} submit={submit} />}
      {videoUpscale && <VideoSeedVR2Modal video={videoUpscale} runtime={runtime} onClose={() => setVideoUpscale(null)} submit={submit} />}
      {importing && (
        <ImportModal
          projects={projectList}
          defaultProject={project && project !== UNASSIGNED_PROJECT ? project : ''}
          onClose={() => setImporting(false)}
          onDone={() => {
            setImporting(false);
            refreshProjects?.();
            load();
          }}
        />
      )}
      {projectModal && (
        <ProjectModal
          modal={projectModal}
          onClose={() => setProjectModal(null)}
          onSaved={async nextName => {
            setProjectModal(null);
            await refreshProjects?.();
            selectProject(nextName);
            setNotice(projectModal.mode === 'rename' ? `已重命名为「${nextName}」` : `已创建项目「${nextName}」`);
          }}
        />
      )}
    </div>
  );
}

function ImageDetail({
  image: i,
  items,
  projects,
  onClose,
  update,
  reuse,
  continueVideo,
  keyframeFromVideo,
  submit,
  onUpscale,
  onVideoUpscale,
  onDelete,
  open,
  setNotice,
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [itemProject, setItemProject] = useState(i.project || i.album || '');
  const [tags, setTags] = useState(words(i.tags).join(', '));
  const [compare, setCompare] = useState(null);
  const [full, setFull] = useState(false);
  const p = i.params || {};
  const video = isVideo(i);

  useEffect(() => {
    setItemProject(i.project || i.album || '');
    setTags(words(i.tags).join(', '));
    setCompare(null);
  }, [i.id, i.project, i.album]);

  const act = async fn => {
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const related = Array.isArray(i.related)
    ? i.related
    : items.filter(r => r.id !== i.id && (r.parent_id === i.id || r.id === i.parent_id || (i.parent_id && r.parent_id === i.parent_id)));
  const ext = i.filename?.split('.').pop() || (video ? 'mp4' : 'png');
  const storageLabel = i.archive_status === 'saved' && i.local_cached === false
    ? 'Drive 已归档 · 仅本地缩略图'
    : i.archive_status === 'saved'
      ? '本地 + Drive 已归档'
      : i.archive_status === 'retry'
        ? 'Drive 待重试'
        : 'Drive 归档中';

  return (
    <Modal title={video ? '视频详情' : '图片详情'} onClose={onClose} wide>
      <div className="detail-preview">
        <MediaPreview item={i} alt={p.prompt || (video ? '视频作品' : '图片作品')} onClick={video ? undefined : () => setFull(true)} />
        <button className="preview-expand" aria-label="全屏预览" onClick={() => setFull(true)}>
          <Maximize2 size={18} />
        </button>
      </div>
      <div className="row between mt wrap" style={{ gap: '8px' }}>
        <span className="muted small">
          {i.width || '?'} × {i.height || '?'}
          {video && i.duration ? ` · ${Number(i.duration).toFixed(2)}s` : ''}
          {' · '}
          {i.created_at ? new Date(i.created_at).toLocaleString('zh-CN') : '时间未知'}
        </span>
        <div className="row" style={{ gap: '6px' }}>
          <span className="pill tone-muted">{projectLabel(i.project || i.album)}</span>
          <span className="pill tone-muted">{storageLabel}</span>
          <IconButton icon={Heart} label={i.favorite ? '取消收藏' : '收藏作品'} aria-pressed={!!i.favorite} onClick={() => act(() => update(i.id, { favorite: !i.favorite }))} />
        </div>
      </div>
      <ErrorBox error={error} />
      {i.archive_status === 'retry' && (
        <div className="error">
          <span>Drive 归档失败：{i.archive_error || '尚未完成归档'}</span>
          <Button onClick={() => act(async () => { await post(`/api/gallery/${enc(i.id)}/archive`); await open(i.id); })}>重试归档</Button>
        </div>
      )}
      <div className="detail-actions">
        <Button primary disabled={busy} onClick={() => { reuse({ ...p, project: i.project || i.album || p.project || '' }); onClose(); }}><Copy size={15} />复用参数</Button>
        {video && continueVideo && <Button disabled={busy} onClick={() => { continueVideo(i); onClose(); }}><ArrowUpRight size={15} />尾段续写(路线B)</Button>}
        {video && keyframeFromVideo && (
          <Button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const res = await post(`/api/gallery/${enc(i.id)}/extract-frame`, { position: 'last' });
                await keyframeFromVideo(i, res.path);
                onClose();
              })
            }
          >
            <ArrowUpRight size={15} />
            抽尾帧作首帧(路线A)
          </Button>
        )}
        <Button disabled={busy} onClick={() => act(() => submit({ ...p, project: i.project || i.album || p.project || '' }))}><Play size={15} />再次生成</Button>
        {video ? (
          <Button disabled={busy} onClick={onVideoUpscale}><Maximize2 size={15} />SeedVR2 视频精修</Button>
        ) : (
          <Button disabled={busy} onClick={onUpscale}><Maximize2 size={15} />继续超分</Button>
        )}
        <Button onClick={() => act(() => download(`/api/gallery/${enc(i.id)}/download`, i.filename || `${i.id}.${ext}`))}><Download size={15} />下载</Button>
      </div>

      <h3 className="detail-heading">参数信息</h3>
      <dl className="params-list">
        {[
          ['项目', projectLabel(i.project || i.album)],
          ['模型', p.model],
          ['LoRA', (p.loras || []).map(l => `${l.name || l.id} · ${l.strength ?? l.weight}`).join(' / ') || undefined],
          ['正向提示词', p.prompt],
          ['负向提示词', p.negative_prompt || undefined],
          ['规格', `${p.width || i.width || '?'} × ${p.height || i.height || '?'}${video && i.duration ? ` · ${Number(i.duration).toFixed(2)}s` : ''}`],
          ['Steps / CFG', video ? (p.steps != null ? `${p.steps} 步` : undefined) : `${p.steps ?? '—'} / ${p.cfg ?? '—'}`],
          ['Seed', p.seed],
          ['Sampler / Scheduler', p.sampler || p.scheduler ? `${p.sampler ?? '—'} / ${p.scheduler ?? '—'}` : undefined],
          ['工作流', p.workflow || p.workflow_id],
          ['关键帧帧号', Array.isArray(p.keyframe_frames) ? p.keyframe_frames.join(', ') : undefined],
          ['续写源视频', p.source_video],
          ['导出画质 CRF', p.video_crf],
          ['超分配置', p.upscale?.enabled ? JSON.stringify(p.upscale) : undefined],
        ]
          .filter(([, v]) => v !== undefined && v !== '')
          .map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
          ))}
      </dl>

      <Fold title="完整参数 JSON"><pre className="json-preview">{JSON.stringify(p, null, 2)}</pre></Fold>

      <Fold title="项目与标签">
        <div className="grid-two">
          <Field label="归属项目">
            <select value={itemProject} onChange={e => setItemProject(e.target.value)}>
              <option value="">未分类</option>
              {projects.map(proj => <option key={proj.name} value={proj.name}>{proj.name}</option>)}
              {itemProject && !projects.some(proj => proj.name === itemProject) && (
                <option value={itemProject}>{itemProject}</option>
              )}
            </select>
          </Field>
          <Field label="标签（逗号分隔）"><input value={tags} onChange={e => setTags(e.target.value)} /></Field>
        </div>
        <Button disabled={busy} onClick={() => act(async () => { await update(i.id, { project: itemProject, tags: words(tags) }); setNotice('作品信息已保存（Drive 文件夹已同步）'); })}>保存信息</Button>
      </Fold>

      <Fold title={video ? '相关作品 / 源素材对比' : '相关图片 / 原图对比'}>
        {i.parent_id && (
          <Button onClick={() => act(async () => { setCompare(await api(`/api/gallery/${enc(i.parent_id)}`)); })}>
            {video ? '加载源素材对比' : '加载原图对比'}
          </Button>
        )}
        {compare && (
          <div className="compare-grid">
            <figure>
              <MediaPreview item={compare} alt="源素材" />
              <figcaption>源素材 · {compare.width} × {compare.height}</figcaption>
            </figure>
            <figure>
              <MediaPreview item={i} alt="当前作品" />
              <figcaption>当前 · {i.width} × {i.height}</figcaption>
            </figure>
          </div>
        )}
        <div className="related-images">
          {related.map(r => (
            <button key={r.id} onClick={() => open(r.id, r)}>
              <img src={r.thumbnail_url || r.url} alt="相关作品" />
            </button>
          ))}
        </div>
        {!related.length && !i.parent_id && <p className="muted">暂无关联作品。</p>}
      </Fold>

      <div className="row end mt">
        <Button disabled={busy} onClick={() => { if (confirm('永久删除该作品及 Drive 文件？')) act(onDelete); }}>
          <Trash2 size={15} />
          删除作品
        </Button>
      </div>

      {full && (
        <div className="fullscreen" role="dialog" aria-label="全屏预览">
          <Button onClick={() => setFull(false)}>关闭预览</Button>
          <MediaPreview item={i} alt="全屏预览" preferOriginal />
        </div>
      )}
    </Modal>
  );
}

function ImportModal({ projects, defaultProject = '', onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [importProject, setImportProject] = useState(defaultProject);
  const [metadata, setMetadata] = useState('{}');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="导入旧作品" onClose={onClose}>
      <form
        className="stack"
        onSubmit={async e => {
          e.preventDefault();
          setBusy(true);
          try {
            JSON.parse(metadata);
            const body = new FormData();
            body.append('file', file);
            body.append('metadata', metadata);
            body.append('project', importProject);
            await api('/api/gallery/import', { method: 'POST', body });
            onDone();
          } catch (e) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="归属项目">
          <select value={importProject} onChange={e => setImportProject(e.target.value)}>
            <option value="">未分类</option>
            {projects.map(proj => <option key={proj.name} value={proj.name}>{proj.name}</option>)}
          </select>
        </Field>
        <Field label="图片或视频文件"><input type="file" accept="image/*,video/mp4,video/webm" required onChange={e => setFile(e.target.files[0])} /></Field>
        <Field label="生成参数 JSON（可留为 {}）"><textarea className="code tall" value={metadata} onChange={e => setMetadata(e.target.value)} /></Field>
        <ErrorBox error={error} />
        <Button primary type="submit" disabled={busy || !file}>{busy ? '正在导入并归档…' : '导入作品'}</Button>
      </form>
    </Modal>
  );
}

function ProjectModal({ modal, onClose, onSaved }) {
  const [name, setName] = useState(modal?.name || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const isRename = modal?.mode === 'rename';
  return (
    <Modal title={isRename ? `重命名项目「${modal.original}」` : '新建项目文件夹'} onClose={onClose}>
      <form
        className="stack"
        onSubmit={async e => {
          e.preventDefault();
          const clean = name.trim();
          if (!clean) {
            setError('项目名称不能为空');
            return;
          }
          setBusy(true);
          setError('');
          try {
            if (isRename) {
              await patch(`/api/gallery/projects/${enc(modal.original)}`, { name: clean });
            } else {
              await post('/api/gallery/projects', { name: clean });
            }
            await onSaved(clean);
          } catch (err) {
            setError(err.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field
          label="项目名称"
          hint="将在 Google Drive 的 DrawingStudio/outputs/projects/<项目名> 下创建或重命名对应文件夹。"
        >
          <input value={name} onChange={e => setName(e.target.value)} placeholder="例如：角色设定 / 海报合集" autoFocus required />
        </Field>
        <ErrorBox error={error} />
        <Button primary type="submit" disabled={busy || !name.trim()}>
          {busy ? '处理中…' : isRename ? '保存新名称' : '创建项目'}
        </Button>
      </form>
    </Modal>
  );
}
