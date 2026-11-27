import React, { useState, useMemo, useRef } from 'react';
import {
  Search, Plus, Play, Star, Check, Layers, Boxes,
  SlidersHorizontal, Shuffle, RotateCcw, ChevronDown, Trash2, CheckSquare,
} from 'lucide-react';
import { Modal, Button, IconButton, Field, NumberField, Cover, Empty, ErrorBox, words, formatSize, taskStage, Status } from '../ui';
import { parseMultiPrompts, containsMixedPrompt } from '../promptParser';
import {
  SAMPLER_OPTIONS,
  SCHEDULER_OPTIONS,
  RESOLUTION_TIERS,
  ASPECT_RATIOS,
  MOSS_VARIANT_PRESETS,
  resolveImageSize,
  detectImageTierAndRatio,
  inferTierAndRatio,
  highResVramLevel,
  getMossParamWarnings,
  clampBatchCount,
  resolveBatchItemParams,
  applyParamEdit,
  applySizePresetEdit,
  hasDifferingParsedValues,
  buildBatchSubmissions,
} from '../promptBatchParams';
import { setLoraStrength, toggleLora, matchesLora, loraPickerRows } from '../loraSelection';
import { injectTriggerIntoMossLayout } from '../mossLayout';
import { seedvr2ResolutionOptions, seedvr2BatchOptions, seedvr2ColorOptions, videoCrfOptions, parseVideoCrf } from '../h3Video';
import { classifyWorkflow, cleanModelName, workflowQuickSpec } from '../taskData';

export function LoraPicker({ models, value, onChange, onClose, prompt, onPrompt, mossVariant = null }) {
  const [q, setQ] = useState('');
  const [family, setFamily] = useState('');
  const [tag, setTag] = useState('');
  const [selected, setSelected] = useState(value);
  const rawLoras = loraPickerRows(models.filter(m => ['lora', 'loras'].includes(m.kind)), selected);
  const loras = useMemo(() => {
    if (!mossVariant) return rawLoras;
    const score = m => {
      if (m.node_id != null) return 0;
      if (/moss/i.test(String(m.family || '')) || /moss/i.test(String(m.filename || m.name || ''))) return 1;
      return 2;
    };
    return [...rawLoras].sort((a, b) => score(a) - score(b));
  }, [rawLoras, mossVariant]);
  const families = [...new Set(loras.map(m => m.family || '').filter(Boolean))];
  const tags = [...new Set(loras.flatMap(m => words(m.tags)))];
  const toggle = m => setSelected(s => toggleLora(s, m));

  return (
    <Modal title="选择 LoRA" onClose={onClose} wide>
      <div className="search">
        <Search size={17} />
        <input placeholder="搜索名称、家族、标签、触发词" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      {families.length > 0 && (
        <div className="chips scroll" style={{ marginTop: '10px' }}>
          <button type="button" className={!family ? 'selected' : ''} onClick={() => setFamily('')}>全部家族</button>
          {families.map(f => (
            <button type="button" className={family === f ? 'selected' : ''} key={f} onClick={() => setFamily(f)}>
              {f}
            </button>
          ))}
        </div>
      )}
      <div className="chips scroll" style={{ marginTop: '8px' }}>
        <button type="button" className={!tag ? 'selected' : ''} onClick={() => setTag('')}>全部标签</button>
        {tags.map(t => (
          <button type="button" className={tag === t ? 'selected' : ''} key={t} onClick={() => setTag(t)}>
            {t}
          </button>
        ))}
      </div>
      <div className="picker-list">
        {loras
          .filter(
            m =>
              (!family || (m.family || '') === family) &&
              (!tag || words(m.tags).includes(tag)) &&
              JSON.stringify(m).toLowerCase().includes(q.toLowerCase()),
          )
          .map(m => {
            const chosen = selected.find(l => matchesLora(l, m) && l.enabled !== false);
            return (
              <div className="picker-item" key={m.node_id != null ? `node:${m.node_id}` : `model:${m.id}`} data-node-id={m.node_id}>
                <label className="row">
                  <input type="checkbox" checked={!!chosen} onChange={() => toggle(m)} />
                  <Cover src={m.cover} name={m.name} />
                  <span className="grow">
                    <strong>{m.name}</strong>
                    {m.node_id != null ? (
                      <small>工作流槽位 · 节点 {m.node_id}</small>
                    ) : chosen ? (
                      <small>叠加 LoRA（自动串联生效）</small>
                    ) : null}
                    <small>{m.family || '自定义'} · {formatSize(m.size)} · {taskStage(m) === 'ready' ? 'GPU 已就绪' : '在 Drive'}</small>
                    {m.notes && <small>{m.notes}</small>}
                    {words(m.tags).length > 0 && <small>{words(m.tags).join(' · ')}</small>}
                  </span>
                </label>
                {chosen && (
                  <div className="weight-control">
                    <span>权重</span>
                    <input
                      aria-label={`${m.name} 权重滑杆`}
                      type="range"
                      min={Math.min(-2, chosen.strength)}
                      max={Math.max(2, chosen.strength)}
                      step="0.05"
                      value={chosen.strength}
                      onChange={e => setSelected(s => s.map(l => matchesLora(l, m) && l.enabled !== false ? setLoraStrength(l, Number(e.target.value)) : l))}
                    />
                    <input
                      aria-label={`${m.name} 权重`}
                      type="number"
                      step="0.05"
                      value={chosen.strength}
                      onChange={e => setSelected(s => s.map(l => matchesLora(l, m) && l.enabled !== false ? setLoraStrength(l, e.target.value === '' ? '' : Number(e.target.value)) : l))}
                    />
                  </div>
                )}
                {words(m.trigger_words).length > 0 && (
                  <div className="trigger-words">
                    <span>触发词</span>
                    {words(m.trigger_words).map(w => (
                      <button type="button" key={w} onClick={() => onPrompt(injectTriggerIntoMossLayout(prompt, w))}>
                        <Plus size={12} />
                        {w}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        {!loras.length && <Empty>尚无 LoRA，请在模型页扫描 Drive。</Empty>}
      </div>
      <div className="modal-footer">
        <span className="muted">已选择 {selected.filter(l => l.enabled !== false).length} 个</span>
        <Button primary onClick={() => { onChange(selected); onClose(); }}>确定</Button>
      </div>
    </Modal>
  );
}

export function WorkflowPicker({ workflows, models, selectedId, onSelect, onClose, initialCategory = 'all' }) {
  const [cat, setCat] = useState(initialCategory);
  const [q, setQ] = useState('');

  const categories = [
    ['all', '全部'],
    ['video', '视频 H3'],
    ['moss', 'MOSS'],
    ['sdxl', 'SDXL 内置'],
    ['refine', 'AI 精修'],
    ['custom', '特色/自定义'],
  ];

  const modelReadyCount = w => {
    const refs = [];
    for (const n of Object.values(w?.workflow || {})) {
      const i = n?.inputs || {};
      for (const k of ['ckpt_name', 'unet_name', 'clip_name', 'clip_name1', 'clip_name2', 'vae_name', 'lora_name', 'model', 'model_name']) {
        if (typeof i[k] === 'string' && i[k]) refs.push(i[k]);
      }
    }
    const uniq = [...new Set(refs)];
    if (!uniq.length) return null;
    let ready = 0;
    for (const ref of uniq) {
      const m = models.find(x => [x.id, x.name, x.filename, x.comfy_name].includes(ref));
      if (m && taskStage(m) === 'ready') ready++;
    }
    return { ready, total: uniq.length };
  };

  const rows = useMemo(() => {
    return workflows
      .map(w => ({ w, meta: classifyWorkflow(w), counts: modelReadyCount(w), spec: workflowQuickSpec(w) }))
      .filter(({ w, meta }) => {
        if (cat !== 'all' && meta.category !== cat) return false;
        if (q.trim()) {
          const ql = q.trim().toLowerCase();
          return (w.name || '').toLowerCase().includes(ql) || (w.id || '').toLowerCase().includes(ql) || meta.badges.some(b => b.toLowerCase().includes(ql));
        }
        return true;
      });
  }, [workflows, models, cat, q]);

  return (
    <Modal title="选择工作流" onClose={onClose} wide>
      <div className="search">
        <Search size={17} />
        <input placeholder="搜索工作流名称、路线A/B、INT8/BF16、MOSS…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      <div className="chips scroll" style={{ marginTop: '10px' }}>
        {categories.map(([id, label]) => (
          <button type="button" key={id} className={cat === id ? 'selected' : ''} onClick={() => setCat(id)}>
            {label}
          </button>
        ))}
      </div>

      <div className="picker-card-list">
        <button
          type="button"
          className={`picker-card ${!selectedId ? 'selected' : ''}`}
          onClick={() => { onSelect(''); onClose(); }}
        >
          <div className="row between">
            <strong><Layers size={15} style={{ verticalAlign: '-2px', marginRight: '6px' }} />根据生成模式自动匹配（自由选底模）</strong>
            {!selectedId && <span className="pill tone-ok"><Check size={12} />当前</span>}
          </div>
          <small className="muted">使用右侧自由选择的 Checkpoint 底模与 LoRA 组合，适合标准 SDXL 出图/超分。</small>
        </button>

        {rows.map(({ w, meta, counts, spec }) => {
          const active = selectedId === w.id;
          return (
            <button
              type="button"
              key={w.id}
              className={`picker-card ${active ? 'selected' : ''}`}
              onClick={() => { onSelect(w.id); onClose(); }}
            >
              <div className="row between" style={{ gap: '8px' }}>
                <strong className="truncate">{w.name}</strong>
                {active && <span className="pill tone-ok"><Check size={12} />当前</span>}
              </div>
              <div className="row wrap" style={{ gap: '6px', marginTop: '6px' }}>
                <span className="pill tone-muted">{meta.categoryLabel}</span>
                {meta.badges.map(b => (
                  <span
                    key={b}
                    className={`pill ${
                      b === 'INT8' || b === '路线A·多关键帧' || b === 'MOSS SFT'
                        ? 'tone-info'
                        : b === 'BF16' || b === 'MOSS Base'
                          ? 'tone-warn'
                          : 'tone-muted'
                    }`}
                  >
                    {b}
                  </span>
                ))}
                {counts && (
                  <span className={`pill ${counts.ready === counts.total ? 'tone-ok' : 'tone-muted'}`}>
                    GPU 模型 {counts.ready}/{counts.total}
                  </span>
                )}
              </div>
              {spec && <small className="muted" style={{ display: 'block', marginTop: '6px' }}>{spec}</small>}
            </button>
          );
        })}
        {!rows.length && <Empty>没有匹配的工作流。</Empty>}
      </div>
    </Modal>
  );
}

export function ModelPicker({ models, selectedId, onSelect, onClose, title = '选择基础模型' }) {
  const [q, setQ] = useState('');
  const [family, setFamily] = useState('');
  const [onlyFav, setOnlyFav] = useState(false);

  const families = useMemo(
    () => [...new Set(models.map(m => m.family || '自定义').filter(Boolean))],
    [models],
  );

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return models.filter(m => {
      if (onlyFav && !m.favorite) return false;
      if (family && (m.family || '自定义') !== family) return false;
      if (ql) {
        return (
          (m.name || '').toLowerCase().includes(ql) ||
          (m.family || '').toLowerCase().includes(ql) ||
          words(m.tags).some(t => t.toLowerCase().includes(ql))
        );
      }
      return true;
    });
  }, [models, q, family, onlyFav]);

  return (
    <Modal title={title} onClose={onClose} wide>
      <div className="row" style={{ gap: '8px' }}>
        <div className="search">
          <Search size={17} />
          <input placeholder="搜索模型名称、底模家族、标签…" value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <Button className={onlyFav ? 'active-chip' : ''} onClick={() => setOnlyFav(v => !v)}>
          <Star size={15} fill={onlyFav ? 'currentColor' : 'none'} />
          收藏
        </Button>
      </div>

      <div className="chips scroll" style={{ marginTop: '10px' }}>
        <button type="button" className={!family ? 'selected' : ''} onClick={() => setFamily('')}>全部家族</button>
        {families.map(f => (
          <button type="button" className={family === f ? 'selected' : ''} key={f} onClick={() => setFamily(f)}>
            {f}
          </button>
        ))}
      </div>

      <div className="picker-card-list">
        {filtered.map(m => {
          const active = selectedId === m.id;
          const stage = taskStage(m);
          return (
            <button
              type="button"
              key={m.id}
              className={`picker-card model-pick-card ${active ? 'selected' : ''}`}
              onClick={() => { onSelect(m.id); onClose(); }}
            >
              <Cover src={m.cover} name={m.name} />
              <div className="grow" style={{ minWidth: 0, textAlign: 'left' }}>
                <div className="row between" style={{ gap: '8px' }}>
                  <strong className="truncate">{cleanModelName(m.name)}</strong>
                  <Status state={stage} label={stage === 'ready' ? '已就绪' : undefined} />
                </div>
                <small className="muted" style={{ display: 'block', marginTop: '4px' }}>
                  {m.family || '自定义'} · {m.kind} · {formatSize(m.size)}
                </small>
                {words(m.tags).length > 0 && (
                  <div className="row wrap" style={{ gap: '4px', marginTop: '6px' }}>
                    {words(m.tags).slice(0, 4).map(t => (
                      <span key={t} className="pill tone-muted">{t}</span>
                    ))}
                  </div>
                )}
              </div>
              {active && <Check size={16} />}
            </button>
          );
        })}
        {!filtered.length && <Empty><Boxes size={24} />没有匹配的模型，可前往「模型」页扫描 Drive。</Empty>}
      </div>
    </Modal>
  );
}

import { baseUpscale } from '../defaultParams.js';
export { baseUpscale };

export function UpscaleFields({ value, onChange, models, workflows }) {
  const set = (key, v) => onChange({ ...value, [key]: v });
  return (
    <div className="stack">
      <div className="mode-options">
        {[
          ['off', '关闭', '保留原始尺寸'],
          ['fast', '快速放大', 'ESRGAN / UltraSharp 等超分模型'],
          ['hires', '高清重绘', '低降噪重绘，增强画面细节'],
          ['ai', 'AI 精修', 'SeedVR2 / SUPIR 等自定义工作流'],
        ].map(([v, n, d]) => (
          <label key={v} className={`choice ${value.mode === v ? 'selected' : ''}`}>
            <input
              type="radio"
              name="upscale-mode"
              checked={value.mode === v}
              onChange={() => onChange({ ...value, mode: v, enabled: v !== 'off', workflow: v === 'ai' ? value.workflow || 'seedvr2' : value.workflow })}
            />
            <span>
              <strong>{n}</strong>
              <small>{d}</small>
            </span>
          </label>
        ))}
      </div>
      {value.enabled && (
        <>
          <div className="grid-two">
            <NumberField label="放大倍率" value={value.scale} onChange={v => set('scale', v)} />
            <NumberField label="Denoise" value={value.denoise} onChange={v => set('denoise', v)} />
          </div>
          <Field label="超分模型">
            <select value={value.model} onChange={e => set('model', e.target.value)}>
              <option value="">工作流默认</option>
              {models.filter(m => ['upscale', 'upscale_models'].includes(m.kind)).map(m => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          </Field>
          {value.mode === 'ai' && (
            <Field label="精修工作流">
              <select value={value.workflow} onChange={e => set('workflow', e.target.value)}>
                <option value="">选择工作流</option>
                {workflows.map(w => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
            </Field>
          )}
          <div className="grid-two">
            <NumberField label="目标宽度（可留空）" value={value.width} onChange={v => set('width', v)} />
            <NumberField label="目标高度（可留空）" value={value.height} onChange={v => set('height', v)} />
            <NumberField label="分块大小 Tile" value={value.tile_size} onChange={v => set('tile_size', v)} />
          </div>
        </>
      )}
    </div>
  );
}

export function UpscaleModal({ value, models, workflows, onClose, onApply, image, submit }) {
  const [draft, setDraft] = useState({ ...baseUpscale, ...value });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <Modal title={image ? '图片超分' : '超分 / 高清修复'} onClose={onClose}>
      {image && (
        <div className="source-image">
          <Cover src={image.thumbnail_url || image.url} />
          <span>
            <strong>原始图片</strong>
            <small>{image.width} × {image.height}</small>
          </span>
        </div>
      )}
      <UpscaleFields value={draft} onChange={setDraft} models={models} workflows={workflows} />
      {error && <p role="alert" className="error">{error}</p>}
      <div className="modal-footer">
        <Button onClick={onClose}>取消</Button>
        <Button
          primary
          disabled={busy || (!!image && !draft.enabled)}
          onClick={async () => {
            if (!image) {
              onApply(draft);
              onClose();
              return;
            }
            setBusy(true);
            try {
              const ai = draft.mode === 'ai';
              await submit({
                ...image.params,
                mode: ai ? (draft.workflow === 'supir' ? 'supir' : 'seedvr2') : draft.mode === 'hires' ? 'hires' : 'upscale',
                workflow: ai ? draft.workflow : '',
                workflow_id: ai ? draft.workflow : '',
                input_image: image.url,
                image_id: image.id,
                parent_id: image.id,
                n: 1,
                batch_size: 1,
                upscale: draft,
              });
              onClose();
            } catch (e) {
              setError(e.message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? '正在提交…' : image ? '开始超分' : '应用设置'}
        </Button>
      </div>
    </Modal>
  );
}

export function VideoSeedVR2Modal({ video, runtime, onClose, submit }) {
  const gpu = String(runtime?.actual_gpu || runtime?.gpu || '').toUpperCase();
  const highVram = /G4|RTX\s*PRO\s*6000|A100|H100|H200/.test(gpu);
  const [resolution, setResolution] = useState(1080);
  const [blocksToSwap, setBlocksToSwap] = useState(highVram ? 0 : 16);
  const [frameBatchSize, setFrameBatchSize] = useState(5);
  const [temporalOverlap, setTemporalOverlap] = useState(2);
  const [colorCorrection, setColorCorrection] = useState('lab');
  const [videoCrf, setVideoCrf] = useState(parseVideoCrf(video?.params?.video_crf, 15));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    setBusy(true);
    setError('');
    try {
      await submit({
        mode: 'video_seedvr2',
        workflow: 'video-seedvr2',
        workflow_id: 'video-seedvr2',
        source_video: video.filename || '',
        source_video_id: video.id,
        parent_id: video.id,
        prompt: video.params?.prompt || '',
        resolution: Number(resolution),
        frame_batch_size: Number(frameBatchSize),
        temporal_overlap: Number(temporalOverlap),
        color_correction: colorCorrection,
        blocks_to_swap: Number(blocksToSwap),
        swap_io_components: Number(blocksToSwap) > 0,
        video_crf: Number(videoCrf),
        free_gpu_before_run: true,
        dit_model: 'seedvr2_ema_3b_fp8_e4m3fn.safetensors',
        seedvr2_vae: 'ema_vae_fp16.safetensors',
        seed: -1,
        n: 1,
        batch_size: 1,
      });
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="SeedVR2 视频精修" onClose={onClose}>
      <div className="source-image">
        <Cover src={video.thumbnail_url || video.url} />
        <span>
          <strong>源视频素材</strong>
          <small>{video.width} × {video.height}{video.duration ? ` · ${Number(video.duration).toFixed(1)}s` : ''}</small>
        </span>
      </div>
      <div className="stack">
        <Field label="目标短边分辨率">
          <select aria-label="目标短边分辨率" value={resolution} onChange={e => setResolution(Number(e.target.value))}>
            {seedvr2ResolutionOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
          </select>
        </Field>
        <Field label="显存模式（BlockSwap）">
          <select aria-label="显存模式" value={blocksToSwap} onChange={e => setBlocksToSwap(Number(e.target.value))}>
            <option value={0}>0 · 全速模式（G4 96G / H100 80G / A100 40G 推荐）</option>
            <option value={16}>16 · 均衡省显存（L4 24G 推荐）</option>
            <option value={32}>32 · 极限省显存</option>
          </select>
        </Field>
        <div className="grid-two">
          <Field label="时序批大小">
            <select aria-label="时序批大小" value={frameBatchSize} onChange={e => setFrameBatchSize(Number(e.target.value))}>
              {seedvr2BatchOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
            </select>
          </Field>
          <Field label="批次重叠帧">
            <select aria-label="批次重叠帧" value={temporalOverlap} onChange={e => setTemporalOverlap(Number(e.target.value))}>
              {[0, 1, 2, 4].map(v => <option key={v} value={v}>{v === 0 ? '0 帧（关闭）' : `${v} 帧${v === 2 ? '（推荐）' : ''}`}</option>)}
            </select>
          </Field>
        </div>
        <Field label="色彩校正">
          <select aria-label="色彩校正" value={colorCorrection} onChange={e => setColorCorrection(e.target.value)}>
            {seedvr2ColorOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
          </select>
        </Field>
        <Field label="导出画质">
          <select aria-label="导出画质" value={videoCrf} onChange={e => setVideoCrf(Number(e.target.value))}>
            {videoCrfOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
          </select>
        </Field>
        <p className="muted small">提交前会自动释放 GPU 上已加载的模型显存，并保留原视频帧率与音轨。</p>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="modal-footer">
        <Button onClick={onClose} disabled={busy}>取消</Button>
        <Button primary disabled={busy} onClick={handleSubmit}>{busy ? '正在提交…' : '开始视频精修（1 段）'}</Button>
      </div>
    </Modal>
  );
}

export function PromptParserModal({
  initialText = '',
  currentPrompt = '',
  currentNegativePrompt = '',
  baseParams = {},
  defaultCount = 1,
  unitLabel = '张',
  isVideo = false,
  hasShift = false,
  mossVariant = null,
  onClose,
  onApply,
  onBatchGenerate,
}) {
  const rawRef = useRef(null);
  const defaultInit = initialText || (currentPrompt && containsMixedPrompt(currentPrompt) ? currentPrompt : '');
  const [rawText, setRawText] = useState(defaultInit);
  const items = useMemo(() => parseMultiPrompts(rawText), [rawText]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [edits, setEdits] = useState({});
  const [countPerItem, setCountPerItem] = useState(() => clampBatchCount(defaultCount, 1));
  const [paramScope, setParamScope] = useState('all');
  const [globalOverrides, setGlobalOverrides] = useState({});
  const [itemOverrides, setItemOverrides] = useState({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submittingProgress, setSubmittingProgress] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [clipboardHint, setClipboardHint] = useState('');

  const safeIdx = Math.min(activeIdx, Math.max(0, items.length - 1));
  const currentItem = items[safeIdx] || { prompt: '', negative_prompt: '', params: {}, loras: [] };

  const activePos = edits[safeIdx]?.prompt ?? currentItem.prompt;
  const activeNeg = edits[safeIdx]?.negative_prompt ?? currentItem.negative_prompt;

  const isMulti = items.length > 1;
  const effectiveScope = isMulti ? paramScope : 'current';

  const showShift = !isVideo && (hasShift || baseParams?.shift !== undefined || currentItem.params?.shift !== undefined);

  const resolved = useMemo(
    () =>
      resolveBatchItemParams(
        baseParams,
        currentItem.params || {},
        globalOverrides,
        itemOverrides[safeIdx] || {},
        { isVideo, hasShift: showShift },
      ),
    [baseParams, currentItem.params, globalOverrides, itemOverrides, safeIdx, isVideo, showShift],
  );

  const mossWarnings = !isVideo ? getMossParamWarnings(mossVariant, resolved.display) : [];

  const parserSizeMatch = detectImageTierAndRatio(resolved.display.width, resolved.display.height);
  const parserInferredSize = inferTierAndRatio(resolved.display.width, resolved.display.height);
  const parserVramRisk = !isVideo ? highResVramLevel(resolved.display.width, resolved.display.height) : '';
  const samplerChoices =
    resolved.display.sampler && !SAMPLER_OPTIONS.includes(resolved.display.sampler)
      ? [resolved.display.sampler, ...SAMPLER_OPTIONS]
      : SAMPLER_OPTIONS;
  const schedulerChoices =
    resolved.display.scheduler && !SCHEDULER_OPTIONS.includes(resolved.display.scheduler)
      ? [resolved.display.scheduler, ...SCHEDULER_OPTIONS]
      : SCHEDULER_OPTIONS;

  const hasAnyOverride =
    Object.keys(globalOverrides).length > 0 ||
    Object.values(itemOverrides).some(entry => entry && Object.keys(entry).length > 0);
  const differsAcrossItems = useMemo(
    () => isMulti && hasDifferingParsedValues(items, baseParams),
    [isMulti, items, baseParams],
  );
  const safeCount = clampBatchCount(countPerItem, defaultCount || baseParams?.n || 1);
  const noun = unitLabel === '段' ? '段视频' : '张图';

  const handleRawChange = val => {
    setRawText(val);
    setEdits({});
    setItemOverrides({});
    setActiveIdx(0);
    setSubmitError('');
    setClipboardHint('');
  };

  const handlePaste = async () => {
    setClipboardHint('');
    try {
      if (!navigator.clipboard?.readText) throw new Error('no-clipboard');
      const text = await navigator.clipboard.readText();
      if (text) {
        handleRawChange(text);
      } else {
        rawRef.current?.focus({ preventScroll: true });
        setClipboardHint('剪贴板为空，可直接长按输入框粘贴');
      }
    } catch {
      rawRef.current?.focus({ preventScroll: true });
      setClipboardHint('浏览器限制读取剪贴板，请长按输入框粘贴');
    }
  };

  const handleSelectAllRaw = () => {
    const el = rawRef.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    el.select();
    el.setSelectionRange?.(0, el.value.length);
  };

  const handlePosChange = val => setEdits(e => ({ ...e, [safeIdx]: { ...e[safeIdx], prompt: val } }));
  const handleNegChange = val => setEdits(e => ({ ...e, [safeIdx]: { ...e[safeIdx], negative_prompt: val } }));

  const editParam = (key, value) => {
    const next = applyParamEdit({
      scope: effectiveScope,
      index: safeIdx,
      key,
      value,
      globalOverrides,
      itemOverrides,
    });
    setGlobalOverrides(next.globalOverrides);
    setItemOverrides(next.itemOverrides);
  };

  const editSizePreset = (width, height) => {
    const next = applySizePresetEdit({
      scope: effectiveScope,
      index: safeIdx,
      width,
      height,
      globalOverrides,
      itemOverrides,
    });
    setGlobalOverrides(next.globalOverrides);
    setItemOverrides(next.itemOverrides);
  };

  const handleResetParams = () => {
    setGlobalOverrides({});
    setItemOverrides({});
  };

  const applyMossPresetToParser = () => {
    const preset = MOSS_VARIANT_PRESETS[mossVariant];
    if (!preset) return;
    let state = { globalOverrides, itemOverrides };
    for (const key of ['shift', 'steps', 'cfg', 'sampler', 'scheduler']) {
      state = applyParamEdit({
        scope: effectiveScope,
        index: safeIdx,
        key,
        value: preset[key],
        ...state,
      });
    }
    if (
      mossVariant === 'base' &&
      Number(resolved.display.width) * Number(resolved.display.height) > 2_200_000
    ) {
      state = applySizePresetEdit({
        scope: effectiveScope,
        index: safeIdx,
        width: preset.width,
        height: preset.height,
        ...state,
      });
    }
    setGlobalOverrides(state.globalOverrides);
    setItemOverrides(state.itemOverrides);
  };

  const handleApplyCurrent = () => {
    onApply({
      prompt: activePos,
      negative_prompt: activeNeg,
      params: resolved.effective,
      loras: currentItem.loras,
      count: safeCount,
    });
    onClose();
  };

  const handleBatchRun = async () => {
    if (!items.length || busy) return;
    setBusy(true);
    setSubmitError('');
    try {
      const prepared = buildBatchSubmissions(items, {
        edits,
        baseParams,
        globalOverrides,
        itemOverrides,
        countPerItem: safeCount,
        isVideo,
        hasShift: showShift,
      });
      await onBatchGenerate(prepared, (done, total) => {
        setSubmittingProgress(`正在提交第 ${done}/${total} 条…`);
      });
      onClose();
    } catch (e) {
      setSubmitError('批量提交失败: ' + (e?.message || e));
    } finally {
      setBusy(false);
      setSubmittingProgress('');
    }
  };

  const handleSingleRun = async () => {
    if ((!activePos && !activeNeg) || busy) return;
    setBusy(true);
    setSubmitError('');
    try {
      onApply({
        prompt: activePos,
        negative_prompt: activeNeg,
        params: resolved.effective,
        loras: currentItem.loras,
        count: safeCount,
      });
      if (onBatchGenerate) {
        const prepared = buildBatchSubmissions([currentItem], {
          edits: { 0: { prompt: activePos, negative_prompt: activeNeg } },
          baseParams,
          globalOverrides,
          itemOverrides: itemOverrides[safeIdx] ? { 0: itemOverrides[safeIdx] } : {},
          countPerItem: safeCount,
          isVideo,
          hasShift: showShift,
        });
        await onBatchGenerate(prepared);
      }
      onClose();
    } catch (e) {
      setSubmitError('提交失败: ' + (e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const statusTone = resolved.overriddenKeys.length > 0
    ? ['tone-info', '已手动调节']
    : resolved.parsedKeys.length > 0
      ? ['tone-ok', '已识别文本参数']
      : ['tone-muted', '沿用绘图页参数'];

  return (
    <Modal title={isMulti ? `识别提示词（共 ${items.length} 条）` : '识别提示词'} onClose={onClose} wide className="parser-modal">
      <div className="stack" style={{ gap: '12px' }}>
        <Field
          label="粘贴提示词文本（支持单条/多条列表、WebUI、NovelAI、中英文或 JSON）"
          hint={clipboardHint}
          action={
            <>
              <Button className="mini-btn" disabled={busy} onClick={handlePaste}>粘贴</Button>
              <Button className="mini-btn" disabled={busy || !rawText} onClick={handleSelectAllRaw}>
                <CheckSquare size={12} />
                全选
              </Button>
              <Button className="mini-btn" disabled={busy || !rawText} onClick={() => handleRawChange('')}>
                <Trash2 size={12} />
                清空
              </Button>
            </>
          }
        >
          <textarea
            ref={rawRef}
            className="prompt parser-raw-input"
            placeholder="在此粘贴包含单条或多条提示词的文本…"
            value={rawText}
            onChange={e => handleRawChange(e.target.value)}
          />
        </Field>

        {items.length > 0 && rawText.trim() ? (
          <>
            <div className="row between wrap" style={{ alignItems: 'center', gap: '8px', marginTop: '-2px' }}>
              {isMulti ? (
                <div className="chips scroll" style={{ padding: '2px 0', flex: '1', minWidth: 0 }}>
                  {items.map((it, i) => {
                    const modified =
                      (itemOverrides[i] && Object.keys(itemOverrides[i]).length > 0) ||
                      (edits[i] && (edits[i].prompt !== undefined || edits[i].negative_prompt !== undefined));
                    return (
                      <button key={i} className={i === safeIdx ? 'selected' : ''} type="button" onClick={() => setActiveIdx(i)}>
                        {it.title || `第 ${i + 1} 条`}{modified ? ' · 已调' : ''}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <span className="muted small">已识别单条提示词</span>
              )}
            </div>

            <div className="parser-prompt-grid">
              <Field label={`正向提示词（${activePos.length} 字符）`}>
                <textarea className="prompt" style={{ minHeight: '96px' }} value={activePos} onChange={e => handlePosChange(e.target.value)} />
              </Field>
              <Field label={`负向提示词（${activeNeg.length} 字符）`}>
                <textarea
                  className="prompt"
                  style={{ minHeight: '96px' }}
                  placeholder="未识别到负向提示词"
                  value={activeNeg}
                  onChange={e => handleNegChange(e.target.value)}
                />
              </Field>
            </div>

            <div className="parser-params-card">
              <div className="row between wrap" style={{ gap: '8px' }}>
                <div className="row" style={{ gap: '7px' }}>
                  <SlidersHorizontal size={15} />
                  <strong>生成参数调节</strong>
                  <span className={`pill ${statusTone[0]}`}>{statusTone[1]}</span>
                </div>
                {hasAnyOverride && (
                  <Button className="mini-btn" disabled={busy} onClick={handleResetParams}>
                    <RotateCcw size={12} />
                    重置参数
                  </Button>
                )}
              </div>

              {isMulti && (
                <>
                  <div className="parser-scope-bar">
                    <div className="chips" style={{ gap: '6px' }}>
                      <button
                        type="button"
                        className={paramScope === 'all' ? 'selected' : ''}
                        onClick={() => setParamScope('all')}
                      >
                        统一应用全部（{items.length} 条）
                      </button>
                      <button
                        type="button"
                        className={paramScope === 'current' ? 'selected' : ''}
                        onClick={() => setParamScope('current')}
                      >
                        仅调当前（{currentItem.title || `第 ${safeIdx + 1} 条`}）
                      </button>
                    </div>
                  </div>
                  {paramScope === 'all' && differsAcrossItems && (
                    <p className="muted small">各条原识别参数不完全一致；此处修改的项将统一同步到全部条目，未改项保留各自识别值。</p>
                  )}
                </>
              )}

              <div className="row between wrap" style={{ gap: '8px' }}>
                <span className="small muted">每条生成</span>
                <div className="row wrap" style={{ gap: '6px', alignItems: 'center' }}>
                  <div className="chips compact" style={{ margin: 0 }}>
                    {[1, 2, 4].map(num => (
                      <button
                        key={num}
                        type="button"
                        className={Number(countPerItem) === num ? 'selected' : ''}
                        style={{ padding: '4px 9px', minHeight: '30px', fontSize: '12px' }}
                        onClick={() => setCountPerItem(num)}
                      >
                        {num} {unitLabel}
                      </button>
                    ))}
                  </div>
                  <input
                    type="number"
                    min={1}
                    max={16}
                    step={1}
                    aria-label="每条生成数量"
                    className="parser-count-input"
                    value={countPerItem}
                    onChange={e => setCountPerItem(e.target.value === '' ? '' : clampBatchCount(e.target.value, 1))}
                    onBlur={() => setCountPerItem(safeCount)}
                  />
                  <span className="small muted">{unitLabel}</span>
                </div>
              </div>

              {!isVideo && (
                <div className="stack" style={{ gap: '6px' }}>
                  <div className="res-tier-row">
                    <span className="muted small">基础分辨率</span>
                    <div className="chips compact" style={{ margin: 0 }}>
                      {RESOLUTION_TIERS.map(t => (
                        <button
                          key={t.id}
                          type="button"
                          className={parserSizeMatch.tier === t.id ? 'selected' : ''}
                          style={{ padding: '4px 9px', minHeight: '30px', fontSize: '12px' }}
                          onClick={() => {
                            const [w, h] = resolveImageSize(t.id, parserSizeMatch.ratio || parserInferredSize.ratio || '1:1');
                            editSizePreset(w, h);
                          }}
                        >
                          {t.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="chips compact" style={{ margin: 0 }}>
                    {ASPECT_RATIOS.map(r => {
                      const targetTier = parserSizeMatch.tier || parserInferredSize.tier || '1K';
                      const [pw, ph] = resolveImageSize(targetTier, r.id);
                      return (
                        <button
                          key={r.id}
                          type="button"
                          className={parserSizeMatch.ratio === r.id ? 'selected' : ''}
                          style={{ padding: '4px 9px', minHeight: '30px', fontSize: '12px' }}
                          title={`${r.label} (${pw}×${ph})`}
                          onClick={() => editSizePreset(pw, ph)}
                        >
                          {r.label}
                        </button>
                      );
                    })}
                  </div>
                  {parserVramRisk && (
                    <p className="res-vram-warn" role="status">
                      {parserVramRisk} 直出显存占用与耗时极高，若显存不足或超时建议先出 1K / 2K 再超分。
                    </p>
                  )}
                </div>
              )}

              <div className="grid-two">
                <NumberField
                  label="宽度 Width"
                  min={isVideo ? 32 : 64}
                  max={16384}
                  step={isVideo ? 32 : 8}
                  value={resolved.display.width}
                  onChange={v => editParam('width', v)}
                />
                <NumberField
                  label="高度 Height"
                  min={isVideo ? 32 : 64}
                  max={16384}
                  step={isVideo ? 32 : 8}
                  value={resolved.display.height}
                  onChange={v => editParam('height', v)}
                />
                <NumberField
                  label="采样步数 Steps"
                  min={1}
                  max={150}
                  step={1}
                  value={resolved.display.steps}
                  onChange={v => editParam('steps', v)}
                />
                {!isVideo && (
                  <NumberField
                    label="引导系数 CFG"
                    min={0}
                    max={30}
                    step={0.1}
                    value={resolved.display.cfg}
                    onChange={v => editParam('cfg', v)}
                  />
                )}
                {showShift && (
                  <NumberField
                    label={mossVariant === 'base' ? '流调度 Shift（Base 建议 4.5~5.5）' : mossVariant === 'sft' ? '流调度 Shift（SFT 建议 3.2~3.8）' : '流调度 Shift'}
                    min={0}
                    max={100}
                    step={0.1}
                    value={resolved.display.shift}
                    onChange={v => editParam('shift', v)}
                  />
                )}
              </div>

              {mossWarnings.length > 0 && MOSS_VARIANT_PRESETS[mossVariant] && (
                <div className="res-vram-warn" role="status">
                  <div>{mossWarnings.join('；')}</div>
                  <Button className="mini-btn" style={{ marginTop: '6px' }} onClick={applyMossPresetToParser}>
                    改用 {MOSS_VARIANT_PRESETS[mossVariant].label} 推荐参数
                  </Button>
                </div>
              )}

              <button
                type="button"
                className="parser-adv-toggle"
                aria-expanded={showAdvanced}
                onClick={() => setShowAdvanced(v => !v)}
              >
                <ChevronDown size={14} />
                <span>高级采样参数（Seed / Sampler / Scheduler{!isVideo ? ' / Denoise' : ''}）</span>
              </button>

              {showAdvanced && (
                <div className="grid-two">
                  <Field label="Seed（-1 随机）">
                    <div className="input-action">
                      <input
                        aria-label="Seed（-1 随机）"
                        type="number"
                        step="1"
                        value={resolved.display.seed}
                        onChange={e => editParam('seed', e.target.value === '' ? '' : Number(e.target.value))}
                      />
                      <IconButton icon={Shuffle} label="随机种子" onClick={() => editParam('seed', -1)} />
                    </div>
                  </Field>
                  <Field label="Sampler">
                    <select
                      aria-label="Sampler"
                      value={resolved.display.sampler || 'euler'}
                      onChange={e => editParam('sampler', e.target.value)}
                    >
                      {samplerChoices.map(v => <option key={v} value={v}>{v}</option>)}
                    </select>
                  </Field>
                  <Field label="Scheduler">
                    <select
                      aria-label="Scheduler"
                      value={resolved.display.scheduler || 'normal'}
                      onChange={e => editParam('scheduler', e.target.value)}
                    >
                      {schedulerChoices.map(v => <option key={v} value={v}>{v}</option>)}
                    </select>
                  </Field>
                  {!isVideo && (
                    <NumberField
                      label="降噪强度 Denoise"
                      min={0}
                      max={1}
                      step={0.05}
                      value={resolved.display.denoise}
                      onChange={v => editParam('denoise', v)}
                    />
                  )}
                </div>
              )}
            </div>
          </>
        ) : (
          <p className="muted small">支持单条或连续多条提示词（形如「图1：... 负向：... 图2：...」），自动切分识别，并可直接调节步数、CFG 与尺寸后批量提交。</p>
        )}
      </div>

      {submitError && <div style={{ marginTop: '10px' }}><ErrorBox error={submitError} /></div>}

      <div className="modal-footer parser-footer" style={{ marginTop: '14px' }}>
        <Button onClick={onClose} disabled={busy}>取消</Button>
        <div className="row end" style={{ gap: '8px' }}>
          <Button disabled={busy || (!activePos && !activeNeg)} onClick={handleApplyCurrent}>
            仅填入当前页
          </Button>
          {isMulti ? (
            <Button primary disabled={busy || items.length === 0} onClick={handleBatchRun}>
              <Play size={14} />
              {busy ? (submittingProgress || '正在提交…') : `批量生成全部（${items.length} 条 × ${safeCount} ${unitLabel}）`}
            </Button>
          ) : (
            <Button
              primary
              disabled={busy || (!activePos && !activeNeg)}
              onClick={handleSingleRun}
            >
              <Play size={14} />
              {busy ? '正在提交…' : `应用并立即生成（${safeCount} ${noun}）`}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
