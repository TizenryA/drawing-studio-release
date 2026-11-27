import React, { useState, useRef, useMemo } from 'react';
import { Plus, Trash2, ChevronDown, Check, Sparkles, Maximize2 } from 'lucide-react';
import { Modal, Button, Field, ErrorBox } from '../ui';
import {
  MOSS_CANVAS_TYPES,
  MOSS_ITEM_LABELS,
  MOSS_LAYOUT_PRESETS,
  snap16,
  compileMossLayout,
  parseMossLayoutPrompt,
  fitMossLayoutToCanvas,
  buildMossLayoutPreset,
} from '../mossLayout';

const QUICK_SIZES = [
  ['SFT 1K 竖', 832, 1248],
  ['Base 1K 竖', 896, 1344],
  ['2K 竖版精排', 1664, 2496],
  ['2K 横版展开', 2496, 1664],
];

const LABEL_BADGES = {
  character: '角色',
  object: '物体/分格',
  text: '文字',
};

export default function MossLayoutModal({
  width,
  height,
  currentPrompt,
  defaultTrigger = '',
  onClose,
  onApply,
}) {
  const [draft, setDraft] = useState(() => {
    const parsed = parseMossLayoutPrompt(currentPrompt, width, height);
    if (parsed.ok && parsed.draft) {
      return parsed.outOfBounds
        ? fitMossLayoutToCanvas(parsed.draft, snap16(width, 832), snap16(height, 1248))
        : parsed.draft;
    }
    return buildMossLayoutPreset('poster-guochao', snap16(width, 832), snap16(height, 1248), {
      triggerPrefix: defaultTrigger,
    });
  });
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [presetId, setPresetId] = useState('poster-guochao');
  const [jsonOpen, setJsonOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [error, setError] = useState('');

  const stageRef = useRef(null);
  const dragRef = useRef(null);

  const safeIdx = Math.min(selectedIdx, Math.max(0, draft.items.length - 1));
  const selectedItem = draft.items[safeIdx] || null;

  const compiledPreview = useMemo(() => {
    try {
      return { ok: true, json: compileMossLayout(draft), error: '' };
    } catch (e) {
      return { ok: false, json: '', error: e.message };
    }
  }, [draft]);

  const centeredPreview = useMemo(() => {
    if (!selectedItem) return null;
    const [x1, y1, x2, y2] = selectedItem.bbox;
    const hw = Math.trunc(draft.width / 2);
    const hh = Math.trunc(draft.height / 2);
    return [y1 - hh, x1 - hw, y2 - hh, x2 - hw];
  }, [selectedItem, draft.width, draft.height]);

  const applyPreset = id => {
    setPresetId(id);
    setDraft(buildMossLayoutPreset(id, draft.width, draft.height, { triggerPrefix: defaultTrigger }));
    setSelectedIdx(0);
    setError('');
  };

  const changeCanvasSize = (nextW, nextH) => {
    setDraft(old => fitMossLayoutToCanvas(old, snap16(nextW, 832), snap16(nextH, 1248)));
    setError('');
  };

  const updateItem = (idx, patch) => {
    setDraft(old => ({
      ...old,
      items: old.items.map((it, i) => (i === idx ? { ...it, ...patch } : it)),
    }));
  };

  const updateItemBox = (idx, edgeIdx, rawVal) => {
    const val = Math.round(Number(rawVal) || 0);
    setDraft(old => ({
      ...old,
      items: old.items.map((it, i) => {
        if (i !== idx) return it;
        const box = [...it.bbox];
        box[edgeIdx] = val;
        return { ...it, bbox: box };
      }),
    }));
  };

  const addItem = label => {
    const W = draft.width;
    const H = draft.height;
    const w = Math.max(64, Math.round(W * 0.36));
    const h = Math.max(64, Math.round(H * (label === 'text' ? 0.14 : 0.34)));
    const x1 = Math.max(0, Math.round((W - w) / 2));
    const y1 = Math.max(0, Math.round((H - h) / 2));
    const newItem = {
      label,
      caption:
        label === 'text'
          ? 'Crisp bold headline typography centered in box.'
          : label === 'character'
            ? 'Hero character focal figure positioned cleanly within frame.'
            : 'Focused detail inset or scene prop inside this region.',
      ...(label === 'text' ? { text: '主视觉标题' } : {}),
      bbox: [x1, y1, Math.min(W, x1 + w), Math.min(H, y1 + h)],
    };
    setDraft(old => {
      const items = [...old.items, newItem];
      setSelectedIdx(items.length - 1);
      return { ...old, items };
    });
    setError('');
  };

  const removeItem = idx => {
    if (draft.items.length <= 1) {
      setError('至少需要保留 1 个排版区块');
      return;
    }
    setDraft(old => ({
      ...old,
      items: old.items.filter((_, i) => i !== idx),
    }));
    setSelectedIdx(i => Math.max(0, i >= idx ? i - 1 : i));
  };

  const startDrag = (e, idx, mode) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedIdx(idx);
    const stageEl = stageRef.current;
    if (!stageEl) return;
    const rect = stageEl.getBoundingClientRect();
    const box = draft.items[idx]?.bbox;
    if (!box || rect.width <= 0 || rect.height <= 0) return;
    dragRef.current = {
      idx,
      mode,
      startX: e.clientX,
      startY: e.clientY,
      rectW: rect.width,
      rectH: rect.height,
      orig: [...box],
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onDragMove = e => {
    const d = dragRef.current;
    if (!d) return;
    const dxPx = ((e.clientX - d.startX) / d.rectW) * draft.width;
    const dyPx = ((e.clientY - d.startY) / d.rectH) * draft.height;
    const [ox1, oy1, ox2, oy2] = d.orig;
    let next;
    if (d.mode === 'move') {
      const bw = ox2 - ox1;
      const bh = oy2 - oy1;
      const x1 = Math.max(0, Math.min(draft.width - bw, Math.round(ox1 + dxPx)));
      const y1 = Math.max(0, Math.min(draft.height - bh, Math.round(oy1 + dyPx)));
      next = [x1, y1, x1 + bw, y1 + bh];
    } else {
      const x2 = Math.max(ox1 + 24, Math.min(draft.width, Math.round(ox2 + dxPx)));
      const y2 = Math.max(oy1 + 24, Math.min(draft.height, Math.round(oy2 + dyPx)));
      next = [ox1, oy1, x2, y2];
    }
    updateItem(d.idx, { bbox: next });
  };

  const endDrag = e => {
    if (!dragRef.current) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    dragRef.current = null;
  };

  const importJsonDraft = () => {
    const res = parseMossLayoutPrompt(importText, draft.width, draft.height);
    if (!res.ok || !res.draft) {
      setError(res.error || 'JSON 格式无法识别');
      return;
    }
    setDraft(res.draft);
    setSelectedIdx(0);
    setImportText('');
    setError('');
  };

  const handleApply = () => {
    try {
      const compiled = compileMossLayout(draft);
      onApply({
        prompt: compiled,
        width: draft.width,
        height: draft.height,
        hasTextItems: draft.items.some(it => it.label === 'text'),
      });
      onClose();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <Modal title="MOSS 原生 JSON 坐标排版（海报 / 漫画分镜）" onClose={onClose} wide>
      <div className="stack" style={{ gap: '12px' }}>
        <ErrorBox error={error} />

        {/* Template presets + quick canvas sizes */}
        <div className="stack" style={{ gap: '8px' }}>
          <div className="chips scroll">
            {MOSS_LAYOUT_PRESETS.map(p => (
              <button
                type="button"
                key={p.id}
                className={presetId === p.id ? 'selected' : ''}
                onClick={() => applyPreset(p.id)}
              >
                <Sparkles size={12} />
                {p.label}
              </button>
            ))}
          </div>
          <div className="row wrap between" style={{ gap: '8px' }}>
            <div className="chips compact">
              {QUICK_SIZES.map(([label, w, h]) => (
                <button
                  type="button"
                  key={label}
                  className={draft.width === w && draft.height === h ? 'selected' : ''}
                  onClick={() => changeCanvasSize(w, h)}
                >
                  {label} ({w}×{h})
                </button>
              ))}
            </div>
            <span className="muted small">
              画布 {draft.width}×{draft.height}（须为 16 倍数）
            </span>
          </div>
        </div>

        {/* Visual stage + inspector */}
        <div className="moss-layout-grid">
          <div className="stack" style={{ gap: '8px' }}>
            <div
              ref={stageRef}
              className="moss-layout-stage"
              style={{
                aspectRatio: `${draft.width} / ${draft.height}`,
                maxWidth: draft.height > draft.width ? `${Math.round(420 * (draft.width / draft.height))}px` : '100%',
              }}
            >
              <div className="moss-layout-crosshair" aria-hidden="true" />
              {draft.items.map((item, idx) => {
                const [x1, y1, x2, y2] = item.bbox;
                const left = `${(x1 / draft.width) * 100}%`;
                const top = `${(y1 / draft.height) * 100}%`;
                const boxW = `${((x2 - x1) / draft.width) * 100}%`;
                const boxH = `${((y2 - y1) / draft.height) * 100}%`;
                const isSelected = idx === safeIdx;
                return (
                  <div
                    key={idx}
                    role="button"
                    tabIndex={0}
                    className={`moss-layout-box is-${item.label} ${isSelected ? 'is-selected' : ''}`}
                    style={{ left, top, width: boxW, height: boxH }}
                    onClick={() => setSelectedIdx(idx)}
                    onPointerDown={e => startDrag(e, idx, 'move')}
                    onPointerMove={onDragMove}
                    onPointerUp={endDrag}
                    onPointerCancel={endDrag}
                  >
                    <span className="moss-layout-tag">
                      #{idx + 1} {LABEL_BADGES[item.label] || item.label}
                    </span>
                    <span className="moss-layout-preview-text">
                      {item.label === 'text' ? item.text : item.caption}
                    </span>
                    <span
                      className="moss-layout-handle"
                      title="拖动调整方框大小"
                      onPointerDown={e => startDrag(e, idx, 'resize')}
                      onPointerMove={onDragMove}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                    />
                  </div>
                );
              })}
            </div>
            <p className="muted small" style={{ margin: 0 }}>
              拖动方框调整位置，拖动右下角白点缩放大小；原点自动按官方中心系 `(0,0)` / `yxyx` 换算。
            </p>
          </div>

          {/* Right column: summary & item inspector */}
          <div className="stack" style={{ gap: '10px', minWidth: 0 }}>
            <div className="grid-two">
              <Field label="画布模式">
                <select
                  aria-label="画布模式"
                  value={draft.summary.canvas || 'single'}
                  onChange={e =>
                    setDraft(old => ({ ...old, summary: { ...old.summary, canvas: e.target.value } }))
                  }
                >
                  {MOSS_CANVAS_TYPES.map(([val, name]) => (
                    <option key={val} value={val}>{name}</option>
                  ))}
                </select>
              </Field>
              <Field label="风格标签 meta">
                <input
                  value={(draft.summary.meta || []).join(', ')}
                  placeholder="poster, editorial, comic"
                  onChange={e =>
                    setDraft(old => ({
                      ...old,
                      summary: {
                        ...old.summary,
                        meta: e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
                      },
                    }))
                  }
                />
              </Field>
            </div>

            <Field label="全局画面基调 summary.caption">
              <textarea
                rows={3}
                value={draft.summary.caption}
                placeholder="全局画风、光影、质感描述（如搭配带触发词的 LoRA，可在开头保留触发词）"
                onChange={e =>
                  setDraft(old => ({ ...old, summary: { ...old.summary, caption: e.target.value } }))
                }
              />
            </Field>

            <div className="row wrap between" style={{ gap: '6px' }}>
              <span className="muted small">区块列表（点选编辑）</span>
              <div className="row wrap" style={{ gap: '4px' }}>
                <Button className="mini-btn" onClick={() => addItem('text')}>
                  <Plus size={12} />文字
                </Button>
                <Button className="mini-btn" onClick={() => addItem('character')}>
                  <Plus size={12} />角色
                </Button>
                <Button className="mini-btn" onClick={() => addItem('object')}>
                  <Plus size={12} />分格/物体
                </Button>
              </div>
            </div>

            <div className="chips compact">
              {draft.items.map((it, idx) => (
                <button
                  type="button"
                  key={idx}
                  className={idx === safeIdx ? 'selected' : ''}
                  onClick={() => setSelectedIdx(idx)}
                >
                  #{idx + 1} {it.label === 'text' ? `字:${(it.text || '').slice(0, 5)}` : LABEL_BADGES[it.label]}
                </button>
              ))}
            </div>

            {selectedItem && (
              <div className="parser-params-card">
                <div className="row between" style={{ gap: '8px' }}>
                  <strong>编辑区块 #{safeIdx + 1}</strong>
                  <Button className="mini-btn" onClick={() => removeItem(safeIdx)}>
                    <Trash2 size={12} />
                    删除
                  </Button>
                </div>

                <Field label="区块类别 label">
                  <select
                    aria-label="区块类别"
                    value={selectedItem.label}
                    onChange={e => {
                      const nextLabel = e.target.value;
                      updateItem(safeIdx, {
                        label: nextLabel,
                        ...(nextLabel === 'text' && !selectedItem.text ? { text: '主视觉标题' } : {}),
                      });
                    }}
                  >
                    {MOSS_ITEM_LABELS.map(([v, n]) => (
                      <option key={v} value={v}>{n}</option>
                    ))}
                  </select>
                </Field>

                {selectedItem.label === 'text' && (
                  <Field label="渲染文字内容 text（支持换行）">
                    <textarea
                      rows={2}
                      value={selectedItem.text || ''}
                      placeholder="精确要画出的中英文文字，换行即代表排版换行"
                      onChange={e => updateItem(safeIdx, { text: e.target.value })}
                    />
                  </Field>
                )}

                <Field label="区块视觉 / 字体样式说明 caption">
                  <textarea
                    rows={3}
                    value={selectedItem.caption}
                    placeholder="描述这一区域的角色姿势、前景遮挡关系或字体质感/颜色"
                    onChange={e => updateItem(safeIdx, { caption: e.target.value })}
                  />
                </Field>

                <div className="grid-two">
                  <Field label="左 x1 / 宽上限">
                    <input
                      type="number"
                      min={0}
                      max={draft.width}
                      value={selectedItem.bbox[0]}
                      onChange={e => updateItemBox(safeIdx, 0, e.target.value)}
                    />
                  </Field>
                  <Field label="上 y1 / 高上限">
                    <input
                      type="number"
                      min={0}
                      max={draft.height}
                      value={selectedItem.bbox[1]}
                      onChange={e => updateItemBox(safeIdx, 1, e.target.value)}
                    />
                  </Field>
                  <Field label="右 x2">
                    <input
                      type="number"
                      min={0}
                      max={draft.width}
                      value={selectedItem.bbox[2]}
                      onChange={e => updateItemBox(safeIdx, 2, e.target.value)}
                    />
                  </Field>
                  <Field label="下 y2">
                    <input
                      type="number"
                      min={0}
                      max={draft.height}
                      value={selectedItem.bbox[3]}
                      onChange={e => updateItemBox(safeIdx, 3, e.target.value)}
                    />
                  </Field>
                </div>

                {centeredPreview && (
                  <small className="muted" style={{ fontFamily: 'ui-monospace, monospace' }}>
                    MOSS 中心坐标 yxyx: [{centeredPreview.join(', ')}]
                  </small>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Collapsible raw JSON preview & external paste import */}
        <button
          type="button"
          className="parser-adv-toggle"
          aria-expanded={jsonOpen}
          onClick={() => setJsonOpen(v => !v)}
        >
          <ChevronDown size={15} />
          查看编译后单行 JSON / 导入外部 Layout JSON
        </button>

        {jsonOpen && (
          <div className="stack" style={{ gap: '8px' }}>
            <pre className="json-preview" style={{ maxHeight: '140px', overflow: 'auto', padding: '10px', background: 'var(--subtle)', borderRadius: '8px' }}>
              {compiledPreview.ok ? compiledPreview.json : `校验未通过：${compiledPreview.error}`}
            </pre>
            <div className="row" style={{ gap: '8px' }}>
              <input
                className="grow"
                placeholder="粘贴外部 abs/xyxy 或 abs_centralized/yxyx JSON 以导入画框…"
                value={importText}
                onChange={e => setImportText(e.target.value)}
              />
              <Button onClick={importJsonDraft} disabled={!importText.trim()}>
                <Maximize2 size={14} />
                导入
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="modal-footer">
        <span className="muted small">
          {draft.items.length} 个区块 · {draft.width}×{draft.height}
        </span>
        <div className="row" style={{ gap: '8px' }}>
          <Button onClick={onClose}>取消</Button>
          <Button primary onClick={handleApply} disabled={!compiledPreview.ok}>
            <Check size={16} />
            套用到提示词与尺寸
          </Button>
        </div>
      </div>
    </Modal>
  );
}
