import React, { useState } from 'react';
import { Plus, X, ArrowLeft, ArrowRight, RotateCcw, Trash2 } from 'lucide-react';
import { Button } from '../ui';
import { keyframeRole, referenceThumbnail } from '../h3Video';

export default function KeyframeGrid({
  mode,
  items,
  indices = [],
  totalLength = 124,
  continueFrames = 5,
  customFrames,
  uploading = false,
  onUpload,
  onMove,
  onRemove,
  onClear,
  onResetFrames,
  onSetFrame,
  onInsertTag,
}) {
  const [dragIndex, setDragIndex] = useState(null);
  const [overIndex, setOverIndex] = useState(null);
  const [editingIndex, setEditingIndex] = useState(null);

  const isRef = mode === 'ref';
  const isContinue = mode === 'continue';
  const maxItems = 9;
  const minFrame = isContinue ? Number(continueFrames) || 5 : 0;

  const title = isRef
    ? `参考图片 · ${items.length}/9`
    : isContinue
      ? `目标引导关键帧（可选） · ${items.length}/9`
      : `关键帧时间轴（路线 A） · ${items.length}/9`;

  const hint = isRef
    ? '九宫格排列，可拖拽或用箭头调序，点「+P」插入到提示词中。'
    : isContinue
      ? '开头已锁定源视频尾段；传 1 张图 = 目标尾帧，多张图 = 中途过渡 + 尾帧。'
      : '1 张 = 首帧生视频；2 张 = 首尾帧平滑过渡；3~9 张 = 全程多关键帧精准锚定，可拖拽排序。';

  const handleDrop = targetIdx => {
    if (dragIndex == null || dragIndex === targetIdx || uploading) return;
    onMove(dragIndex, targetIdx - dragIndex);
    setDragIndex(null);
    setOverIndex(null);
  };

  return (
    <section
      className="section h3-references"
      data-h3-references={isRef ? '' : undefined}
      data-h3-keyframes={!isRef ? '' : undefined}
    >
      <div className="section-heading">
        <h2>{title}</h2>
        <div className="row" style={{ gap: '6px' }}>
          {!isRef && items.length > 1 && onResetFrames && (
            <Button
              className="mini-btn"
              disabled={uploading}
              onClick={onResetFrames}
              title="恢复均匀时间分布"
            >
              <RotateCcw size={13} />
              均匀分布
            </Button>
          )}
          {items.length > 0 && (
            <Button className="mini-btn" disabled={uploading} onClick={onClear}>
              <Trash2 size={13} />
              清空
            </Button>
          )}
        </div>
      </div>

      <p className="muted small" style={{ marginBottom: '10px' }}>{hint}</p>

      <div className="kf-grid">
        {items.map((path, index) => {
          const frameIdx = indices[index] ?? 0;
          const role = isRef ? `Picture ${index + 1}` : keyframeRole(frameIdx, totalLength, isContinue);
          const timeStr = isRef ? '' : `${(frameIdx / 24).toFixed(1)}s`;
          const shortBadge = isRef ? `P${index + 1}` : `#${index + 1} ${role} · ${timeStr}`;
          const isEditing = !isRef && editingIndex === index;

          return (
            <article
              key={`${path}-${index}`}
              className={`kf-tile ${overIndex === index ? 'drag-over' : ''}`}
              data-picture={isRef ? index + 1 : undefined}
              data-keyframe={!isRef ? index + 1 : undefined}
              draggable={!uploading}
              onDragStart={() => setDragIndex(index)}
              onDragOver={e => {
                e.preventDefault();
                if (overIndex !== index) setOverIndex(index);
              }}
              onDragLeave={() => setOverIndex(cur => (cur === index ? null : cur))}
              onDrop={e => {
                e.preventDefault();
                handleDrop(index);
              }}
              onDragEnd={() => {
                setDragIndex(null);
                setOverIndex(null);
              }}
            >
              <img
                src={referenceThumbnail(path)}
                alt={isRef ? `Picture ${index + 1}` : `${role} ${index + 1}`}
                onError={e => {
                  e.currentTarget.style.opacity = '0.25';
                }}
              />

              <span className="kf-badge" title={`${role} (${String(path).split('/').pop()})`}>
                {shortBadge}
              </span>

              <button
                type="button"
                className="kf-remove"
                disabled={uploading}
                aria-label={isRef ? `删除 Picture ${index + 1}` : `删除关键帧 ${index + 1}`}
                onClick={() => onRemove(index)}
              >
                <X size={13} />
              </button>

              <div className="kf-bar">
                <div className="kf-arrows">
                  <button
                    type="button"
                    disabled={uploading || index === 0}
                    aria-label={isRef ? `前移 Picture ${index + 1}` : `前移关键帧 ${index + 1}`}
                    onClick={() => onMove(index, -1)}
                    title="前移"
                  >
                    <ArrowLeft size={12} />
                  </button>
                  <button
                    type="button"
                    disabled={uploading || index === items.length - 1}
                    aria-label={isRef ? `后移 Picture ${index + 1}` : `后移关键帧 ${index + 1}`}
                    onClick={() => onMove(index, 1)}
                    title="后移"
                  >
                    <ArrowRight size={12} />
                  </button>
                </div>

                {isRef ? (
                  <button
                    type="button"
                    className="kf-action-pill"
                    onClick={() => onInsertTag?.(index)}
                    title={`插入 <Picture ${index + 1}> 到提示词`}
                  >
                    +P{index + 1}
                  </button>
                ) : isEditing ? (
                  <input
                    type="number"
                    className="kf-frame-input"
                    aria-label={`关键帧 ${index + 1} 帧号`}
                    min={minFrame}
                    max={totalLength - 1}
                    value={Array.isArray(customFrames) && customFrames[index] != null ? customFrames[index] : frameIdx}
                    autoFocus
                    onBlur={() => setEditingIndex(null)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') setEditingIndex(null);
                    }}
                    onChange={e => onSetFrame?.(index, e.target.value)}
                  />
                ) : (
                  <button
                    type="button"
                    className="kf-action-pill"
                    onClick={() => setEditingIndex(index)}
                    title="点击修改锚定帧号"
                  >
                    帧 {frameIdx}
                  </button>
                )}
              </div>
            </article>
          );
        })}

        {items.length < maxItems && (
          <label className={`kf-add ${uploading ? 'disabled' : ''}`}>
            <Plus size={20} />
            <span>{uploading ? '上传中…' : '添加图片'}</span>
            <small>{items.length} / {maxItems}</small>
            <input
              aria-label={isRef ? 'H3 参考图片' : 'H3 关键帧图片'}
              type="file"
              multiple
              accept="image/*"
              disabled={uploading || items.length >= maxItems}
              onChange={e => {
                onUpload(e.target.files);
                e.target.value = '';
              }}
            />
          </label>
        )}
      </div>
    </section>
  );
}
