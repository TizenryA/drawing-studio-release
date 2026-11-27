import React, { useState, useMemo } from 'react';
import { Search, Star, Download, Cloud, RefreshCw, ArrowUpRight, Pencil, Trash2, HardDrive, Upload, X, CheckSquare } from 'lucide-react';
import { api, post, patch, remove, enc } from '../api';
import { Button, IconButton, Field, NumberField, Cover, Empty, ErrorBox, Modal, TaskStatus, TaskProgress, taskStage, words, formatSize, Fold } from '../ui';

const kinds = [
  ['all', '全部'],
  ['checkpoint', 'Checkpoint'],
  ['diffusion_models', 'Diffusion'],
  ['lora', 'LoRA'],
  ['text_encoders', '文本编码器'],
  ['vae', 'VAE'],
  ['seedvr2', 'SeedVR2'],
  ['upscale_models', '超分 / 补帧'],
  ['other', '其他'],
];

const kindMap = {
  checkpoints: 'checkpoint',
  checkpoint: 'checkpoint',
  diffusion_models: 'diffusion_models',
  unet: 'diffusion_models',
  loras: 'lora',
  lora: 'lora',
  text_encoders: 'text_encoders',
  clip: 'text_encoders',
  clip_vision: 'text_encoders',
  vae: 'vae',
  seedvr2: 'seedvr2',
  SEEDVR2: 'seedvr2',
  upscale: 'upscale_models',
  upscale_models: 'upscale_models',
  latent_upscale_models: 'upscale_models',
  vfi_models: 'upscale_models',
  embeddings: 'other',
};

const kindOf = m => kindMap[m.kind] || m.kind || 'other';
const isSyncingStage = stage => ['queued_sync', 'downloading', 'verifying', 'syncing'].includes(stage);

export default function Models({ models, settings, workflows, onUse, refresh, error, setNotice }) {
  const [kind, setKind] = useState('all');
  const [q, setQ] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [status, setStatus] = useState('');
  const [tag, setTag] = useState('');
  const [scanning, setScanning] = useState(false);
  const [pendingIds, setPendingIds] = useState(() => new Set());
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [actionError, setError] = useState('');
  const [edit, setEdit] = useState(null);
  const [workflowOpen, setWorkflowOpen] = useState(false);

  const setIdPending = (id, on) => {
    setPendingIds(prev => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const modelAction = async (id, fn) => {
    setIdPending(id, true);
    setError('');
    try {
      await fn();
      await refresh(['models']);
    } catch (e) {
      setError(e.message);
    } finally {
      setIdPending(id, false);
    }
  };

  const scanModels = async () => {
    setScanning(true);
    setError('');
    try {
      await post('/api/models/scan');
      await refresh(['models']);
      setNotice?.('Drive 模型索引已更新');
    } catch (e) {
      setError(e.message);
    } finally {
      setScanning(false);
    }
  };

  const batchSync = async () => {
    if (!selectedIds.length) return;
    setError('');
    try {
      const res = await post('/api/models/sync-batch', { ids: selectedIds });
      setBatchMode(false);
      setSelectedIds([]);
      await refresh(['models']);
      setNotice?.(`已加入同步队列 ${res.started?.length || selectedIds.length} 个模型（默认并发 2 个）`);
    } catch (e) {
      setError(e.message);
    }
  };

  const tags = useMemo(() => [...new Set(models.flatMap(m => words(m.tags)))], [models]);
  const syncingModels = useMemo(() => models.filter(m => isSyncingStage(taskStage(m))), [models]);
  const filtered = useMemo(
    () =>
      models.filter(
        m =>
          (kind === 'all' || kindOf(m) === kind) &&
          (!favorite || m.favorite) &&
          (!status || taskStage(m) === status) &&
          (!tag || words(m.tags).includes(tag)) &&
          JSON.stringify(m).toLowerCase().includes(q.toLowerCase()),
      ),
    [models, kind, favorite, status, tag, q],
  );

  return (
    <div className="stack">
      <div className="tabs scroll">
        {kinds.map(([id, n]) => (
          <button key={id} type="button" className={kind === id ? 'active' : ''} onClick={() => setKind(id)}>
            {n}
          </button>
        ))}
      </div>

      <div className="toolbar">
        <div className="search">
          <Search size={17} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索模型名称、家族、触发词" />
        </div>
        <IconButton icon={Star} label={favorite ? '显示全部模型' : '仅看收藏模型'} aria-pressed={favorite} onClick={() => setFavorite(!favorite)} />
      </div>

      <div className="row between wrap" style={{ gap: '8px' }}>
        <div className="filter-row" style={{ flex: 1 }}>
          <select aria-label="存储状态" value={status} onChange={e => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="drive">仅 Drive</option>
            <option value="queued_sync">排队同步中</option>
            <option value="downloading">下载中</option>
            <option value="verifying">校验中</option>
            <option value="ready">已就绪</option>
            <option value="error">异常</option>
          </select>
          <select aria-label="模型标签" value={tag} onChange={e => setTag(e.target.value)}>
            <option value="">全部标签</option>
            {tags.map(t => <option key={t}>{t}</option>)}
          </select>
        </div>

        <div className="row wrap" style={{ gap: '8px' }}>
          <a
            href={settings.drive_folder_url || undefined}
            target="_blank"
            rel="noreferrer"
            className={`button ${!settings.drive_folder_url ? 'disabled' : ''}`}
            aria-disabled={!settings.drive_folder_url}
          >
            <Cloud size={15} />
            Drive 文件夹
            <ArrowUpRight size={14} />
          </a>
          <Button onClick={() => { setBatchMode(v => !v); setSelectedIds([]); }}>
            <CheckSquare size={15} />
            {batchMode ? '退出批量' : '批量多选同步'}
          </Button>
          <Button primary disabled={scanning} onClick={scanModels}>
            <RefreshCw size={15} />
            {scanning ? '扫描中…' : '扫描新模型'}
          </Button>
        </div>
      </div>

      {/* Active concurrent sync queue panel */}
      {syncingModels.length > 0 && (
        <section className="sync-queue-panel">
          <div className="row between" style={{ marginBottom: '10px' }}>
            <strong>同步队列（{syncingModels.length} 项进行中 / 排队 · 默认并发 2）</strong>
            <span className="muted small">可继续点选其他模型加入队列</span>
          </div>
          <div className="stack" style={{ gap: '10px' }}>
            {syncingModels.map(m => (
              <div key={m.id} className="sync-queue-item">
                <div className="row between" style={{ gap: '8px' }}>
                  <span className="truncate"><strong>{m.name}</strong></span>
                  <div className="row" style={{ gap: '6px' }}>
                    <TaskStatus task={m} model />
                    <Button className="mini-btn" onClick={() => modelAction(m.id, () => post(`/api/models/${enc(m.id)}/sync/cancel`))}>
                      <X size={13} />
                      取消
                    </Button>
                  </div>
                </div>
                <TaskProgress task={m} model />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Batch selection bar */}
      {batchMode && (
        <div className="batch-panel row between wrap" style={{ gap: '10px' }}>
          <strong>已选中 {selectedIds.length} 个模型</strong>
          <div className="row wrap" style={{ gap: '8px' }}>
            <Button
              onClick={() => {
                const candidates = filtered.filter(m => taskStage(m) !== 'ready').map(m => m.id);
                setSelectedIds(selectedIds.length === candidates.length ? [] : candidates);
              }}
            >
              全选未同步 ({filtered.filter(m => taskStage(m) !== 'ready').length})
            </Button>
            <Button primary disabled={!selectedIds.length} onClick={batchSync}>
              <Download size={15} />
              批量同步到 GPU ({selectedIds.length})
            </Button>
          </div>
        </div>
      )}

      {!settings.drive_folder_url && <small className="muted">请在右上角设置中填写 Drive 上传文件夹。</small>}
      <ErrorBox error={error} retry={() => refresh(['models'])} />
      <ErrorBox error={actionError} />

      <section className="model-list">
        {filtered.length ? (
          filtered.map(m => {
            const stage = taskStage(m);
            const syncing = isSyncingStage(stage);
            const rowPending = pendingIds.has(m.id);
            const checked = selectedIds.includes(m.id);
            const selectable = ['checkpoint', 'diffusion_models', 'lora', 'vae', 'upscale_models'].includes(kindOf(m));

            return (
              <article className="model-row" key={m.id}>
                {batchMode && (
                  <input
                    type="checkbox"
                    aria-label={`选择 ${m.name}`}
                    checked={checked}
                    onChange={() => setSelectedIds(old => (checked ? old.filter(id => id !== m.id) : [...old, m.id]))}
                    style={{ alignSelf: 'center' }}
                  />
                )}
                <Cover src={m.cover} name={m.name} />
                <div className="model-info">
                  <div className="row between" style={{ gap: '8px' }}>
                    <h3>{m.name}</h3>
                    <TaskStatus task={m} model />
                  </div>
                  <p>
                    {m.family || '通用/系统'} · {m.kind || 'other'} · {formatSize(m.size)}
                  </p>
                  <TaskProgress task={m} model />
                  {m.error && <p className="error-text">{m.error}</p>}
                  <div className="model-tags">
                    {words(m.tags).map(t => <span key={t}>{t}</span>)}
                  </div>
                  <div className="model-actions">
                    {selectable && <Button onClick={() => onUse(m)}>立即使用</Button>}
                    {syncing ? (
                      <Button disabled={rowPending} onClick={() => modelAction(m.id, () => post(`/api/models/${enc(m.id)}/sync/cancel`))}>
                        <X size={14} />
                        {rowPending ? '取消中…' : '取消同步'}
                      </Button>
                    ) : (
                      <Button disabled={rowPending} onClick={() => modelAction(m.id, () => post(`/api/models/${enc(m.id)}/sync`))}>
                        <Download size={14} />
                        {rowPending ? '提交中…' : stage === 'ready' ? '校验缓存' : '同步到 GPU'}
                      </Button>
                    )}
                    <details className="more-menu">
                      <summary aria-label="模型更多操作">···</summary>
                      <div>
                        <IconButton
                          icon={Star}
                          label={m.favorite ? '取消收藏' : '收藏模型'}
                          aria-pressed={!!m.favorite}
                          className={`icon-button ${m.favorite ? 'filled' : ''}`}
                          onClick={() => modelAction(m.id, () => patch(`/api/models/${enc(m.id)}`, { favorite: !m.favorite }))}
                        />
                        <IconButton icon={Pencil} label="编辑模型信息" onClick={() => setEdit(m)} />
                        <IconButton
                          icon={HardDrive}
                          label="卸载 GPU 缓存"
                          onClick={() => {
                            if (confirm(`卸载「${m.name}」的 GPU 缓存？Drive 文件会保留。`)) {
                              modelAction(m.id, () => remove(`/api/models/${enc(m.id)}/cache`));
                            }
                          }}
                        />
                        <IconButton
                          icon={Trash2}
                          label="从索引移除"
                          onClick={() => {
                            if (confirm(`将「${m.name}」从索引移除？`)) {
                              modelAction(m.id, () => remove(`/api/models/${enc(m.id)}`));
                            }
                          }}
                        />
                      </div>
                    </details>
                  </div>
                </div>
              </article>
            );
          })
        ) : (
          <Empty>{q || tag || favorite || status ? '没有符合筛选条件的模型。' : '还没有此类模型，上传到 Drive 后扫描即可。'}</Empty>
        )}
      </section>

      <Fold title="自定义与内置工作流列表">
        <p className="muted">导入原生 ComfyUI JSON，并提供字段映射。复杂节点可在 ComfyUI 中编辑。</p>
        <Button onClick={() => setWorkflowOpen(true)}>
          <Upload size={16} />
          导入工作流
        </Button>
        <div className="workflow-list">
          {workflows.map(w => (
            <div className="row between" key={w.id}>
              <span>
                <strong>{w.name}</strong>
                <small>{w.builtin ? '内置' : '自定义'} · v{w.version || 1}{w.dependency ? ` · ${w.dependency}` : ''}</small>
              </span>
              <Button
                onClick={async () => {
                  try {
                    const detail = await api(`/api/workflows/${enc(w.id)}`);
                    setWorkflowOpen(detail);
                  } catch (e) {
                    setError(e.message);
                  }
                }}
              >
                查看 / 编辑
              </Button>
            </div>
          ))}
        </div>
      </Fold>

      {edit && (
        <ModelEditor
          model={edit}
          onClose={() => setEdit(null)}
          onSave={async value => {
            await patch(`/api/models/${enc(edit.id)}`, value);
            await refresh(['models']);
            setEdit(null);
          }}
        />
      )}
      {workflowOpen && (
        <WorkflowEditor
          record={typeof workflowOpen === 'object' ? workflowOpen : null}
          onClose={() => setWorkflowOpen(false)}
          onSave={async value => {
            await post('/api/workflows', value);
            await refresh(['workflows']);
            setWorkflowOpen(false);
          }}
        />
      )}
    </div>
  );
}

function ModelEditor({ model, onClose, onSave }) {
  const [m, setM] = useState({
    ...model,
    tags: words(model.tags).join(', '),
    trigger_words: words(model.trigger_words).join(', '),
    defaults: JSON.stringify(model.defaults || {}, null, 2),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (k, v) => setM(old => ({ ...old, [k]: v }));

  return (
    <Modal title="编辑模型信息" onClose={onClose}>
      <form
        className="stack"
        onSubmit={async e => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSave({ ...m, tags: words(m.tags), trigger_words: words(m.trigger_words), defaults: JSON.parse(m.defaults) });
          } catch (e) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="名称"><input value={m.name || ''} onChange={e => set('name', e.target.value)} /></Field>
        <div className="grid-two">
          <Field label="分类">
            <select value={kindOf(m)} onChange={e => set('kind', e.target.value)}>
              {kinds.filter(([v]) => v !== 'all').map(([v, n]) => <option key={v} value={v}>{n}</option>)}
            </select>
          </Field>
          <Field label="底模家族"><input value={m.family || ''} onChange={e => set('family', e.target.value)} /></Field>
        </div>
        <Field label="封面 URL"><input value={m.cover || ''} onChange={e => set('cover', e.target.value)} /></Field>
        <Field label="标签（逗号分隔）"><input value={m.tags} onChange={e => set('tags', e.target.value)} /></Field>
        <Field label="触发词（逗号分隔）"><input value={m.trigger_words} onChange={e => set('trigger_words', e.target.value)} /></Field>
        <NumberField label="推荐 LoRA 权重" value={m.recommended_weight} onChange={v => set('recommended_weight', v)} />
        <Field label="备注"><textarea value={m.notes || ''} onChange={e => set('notes', e.target.value)} /></Field>
        <Field label="推荐参数 JSON"><textarea className="code" value={m.defaults} onChange={e => set('defaults', e.target.value)} /></Field>
        <ErrorBox error={error} />
        <Button primary type="submit" disabled={busy}>{busy ? '正在保存…' : '保存信息'}</Button>
      </form>
    </Modal>
  );
}

function WorkflowEditor({ record, onClose, onSave }) {
  const [name, setName] = useState(record?.name || '');
  const [json, setJson] = useState(JSON.stringify(record?.source_workflow || record?.workflow || {}, null, 2));
  const [mapping, setMapping] = useState(JSON.stringify(record?.mapping || {}, null, 2));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  return (
    <Modal title="自定义 ComfyUI 工作流" onClose={onClose}>
      <form
        className="stack"
        onSubmit={async e => {
          e.preventDefault();
          setBusy(true);
          setError('');
          try {
            await onSave({ ...(record?.id ? { id: record.id } : {}), name, workflow: JSON.parse(json), mapping: JSON.parse(mapping) });
          } catch (e) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="名称"><input value={name} onChange={e => setName(e.target.value)} required /></Field>
        <label className="upload-field">
          <Upload size={16} />
          选择 JSON 文件
          <input
            type="file"
            accept=".json,application/json"
            onChange={async e => {
              try {
                const data = JSON.parse(await e.target.files[0].text());
                if (data.workflow && data.mapping) {
                  setJson(JSON.stringify(data.workflow, null, 2));
                  setMapping(JSON.stringify(data.mapping, null, 2));
                  if (data.name) setName(data.name);
                } else {
                  setJson(JSON.stringify(data, null, 2));
                }
              } catch (e) {
                setError('无法读取工作流：' + e.message);
              }
            }}
          />
        </label>
        <Field label="原生工作流 JSON"><textarea className="code tall" value={json} onChange={e => setJson(e.target.value)} /></Field>
        <Field label="字段映射 JSON" hint={'例如：{"prompt":{"node":"7","input":"text"}}；节点编号来自自己的工作流。'}>
          <textarea className="code" value={mapping} onChange={e => setMapping(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        <Button primary type="submit" disabled={busy}>{busy ? '正在保存…' : '保存工作流'}</Button>
      </form>
    </Modal>
  );
}
