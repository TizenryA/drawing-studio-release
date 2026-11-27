import React, { useRef, useState, useEffect } from 'react';
import { Brush, Eraser, RotateCcw, Check, X } from 'lucide-react';
import { Modal, Button } from '../ui';
import { upload } from '../api';

export function MaskEditorModal({ imageUrl, onClose, onSave }) {
  const containerRef = useRef(null);
  const imgRef = useRef(null);
  const canvasRef = useRef(null);

  const [mode, setMode] = useState('brush'); // 'brush' | 'eraser'
  const [brushSize, setBrushSize] = useState(36);
  const [isDrawing, setIsDrawing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [imgLoaded, setImgLoaded] = useState(false);
  const [hasDrawn, setHasDrawn] = useState(false);

  // 初始化 Canvas 尺寸匹配图片的实际天然尺寸
  useEffect(() => {
    if (!imgLoaded || !imgRef.current || !canvasRef.current) return;
    const img = imgRef.current;
    const canvas = canvasRef.current;
    canvas.width = img.naturalWidth || img.width;
    canvas.height = img.naturalHeight || img.height;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }, [imgLoaded]);

  const getCanvasCoords = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;

    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    return {
      x: (clientX - rect.left) * scaleX,
      y: (clientY - rect.top) * scaleY
    };
  };

  const startDraw = (e) => {
    if (e.touches && e.touches.length > 1) return;
    setIsDrawing(true);
    setHasDrawn(true);
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const { x, y } = getCanvasCoords(e);

    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    // 换算笔刷大小到画布天然分辨率
    const rect = canvas.getBoundingClientRect();
    const scaleRatio = canvas.width / (rect.width || 1);
    ctx.lineWidth = brushSize * scaleRatio;

    if (mode === 'brush') {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = 'rgba(255, 60, 60, 0.75)'; // 半透明红提示，导出时转为纯黑白
      ctx.fillStyle = 'rgba(255, 60, 60, 0.75)';
      ctx.arc(x, y, (brushSize * scaleRatio) / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(x, y);
    } else {
      ctx.globalCompositeOperation = 'destination-out';
      ctx.arc(x, y, (brushSize * scaleRatio) / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(x, y);
    }
  };

  const draw = (e) => {
    if (!isDrawing) return;
    if (e.cancelable && e.type.startsWith('touch')) {
      e.preventDefault();
    }
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const { x, y } = getCanvasCoords(e);

    const rect = canvas.getBoundingClientRect();
    const scaleRatio = canvas.width / (rect.width || 1);
    ctx.lineWidth = brushSize * scaleRatio;
    ctx.lineTo(x, y);
    ctx.stroke();
  };

  const endDraw = () => {
    if (!isDrawing) return;
    setIsDrawing(false);
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    ctx.closePath();
  };

  const handleClear = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasDrawn(false);
  };

  // 生成标准的黑白蒙版并上传：涂抹区域为纯白 #FFFFFF，未涂抹区域为纯黑 #000000
  const handleExportAndSave = async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setSaving(true);
    setError('');

    try {
      const exportCanvas = document.createElement('canvas');
      exportCanvas.width = canvas.width;
      exportCanvas.height = canvas.height;
      const expCtx = exportCanvas.getContext('2d');

      // 1. 底色铺纯黑
      expCtx.fillStyle = '#000000';
      expCtx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);

      // 2. 取当前涂抹层的 Alpha 通道，非透明处填纯白
      const tempCanvas = document.createElement('canvas');
      tempCanvas.width = canvas.width;
      tempCanvas.height = canvas.height;
      const tempCtx = tempCanvas.getContext('2d');
      tempCtx.drawImage(canvas, 0, 0);

      const imgData = tempCtx.getImageData(0, 0, canvas.width, canvas.height);
      const data = imgData.data;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 10) {
          data[i] = 255;     // R
          data[i + 1] = 255; // G
          data[i + 2] = 255; // B
          data[i + 3] = 255; // A
        } else {
          data[i] = 0;
          data[i + 1] = 0;
          data[i + 2] = 0;
          data[i + 3] = 255;
        }
      }
      expCtx.putImageData(imgData, 0, 0);

      const blob = await new Promise((resolve) => exportCanvas.toBlob(resolve, 'image/png'));
      const file = new File([blob], `mask_${Date.now()}.png`, { type: 'image/png' });
      const res = await upload(file);
      onSave(res.path || res.id);
      onClose();
    } catch (e) {
      setError(e.message || '上传蒙版失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="涂抹重绘蒙版" onClose={onClose} wide>
      <div className="stack" style={{ gap: '12px' }}>
        <div
          className="row between wrap"
          style={{
            alignItems: 'center',
            gap: '10px',
            background: 'var(--subtle)',
            padding: '8px 12px',
            borderRadius: '8px'
          }}
        >
          <div className="row" style={{ gap: '6px' }}>
            <button
              type="button"
              className={mode === 'brush' ? 'selected' : ''}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                padding: '6px 12px',
                background: mode === 'brush' ? 'var(--primary)' : 'var(--bg)',
                color: mode === 'brush' ? '#fff' : 'inherit',
                borderRadius: '6px',
                fontWeight: 600
              }}
              onClick={() => setMode('brush')}
            >
              <Brush size={15} /> 画笔
            </button>
            <button
              type="button"
              className={mode === 'eraser' ? 'selected' : ''}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                padding: '6px 12px',
                background: mode === 'eraser' ? 'var(--primary)' : 'var(--bg)',
                color: mode === 'eraser' ? '#fff' : 'inherit',
                borderRadius: '6px',
                fontWeight: 600
              }}
              onClick={() => setMode('eraser')}
            >
              <Eraser size={15} /> 橡皮
            </button>
            <button
              type="button"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                padding: '6px 12px',
                background: 'var(--bg)',
                borderRadius: '6px'
              }}
              onClick={handleClear}
            >
              <RotateCcw size={14} /> 清空
            </button>
          </div>

          <div className="row" style={{ alignItems: 'center', gap: '8px', minWidth: '160px' }}>
            <span className="small muted">粗细: {brushSize}px</span>
            <input
              type="range"
              min="8"
              max="120"
              value={brushSize}
              onChange={(e) => setBrushSize(Number(e.target.value))}
              style={{ flex: 1 }}
            />
          </div>
        </div>

        <div
          ref={containerRef}
          style={{
            position: 'relative',
            width: '100%',
            maxHeight: '62vh',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            background: '#111',
            borderRadius: '8px',
            overflow: 'hidden',
            touchAction: 'none',
            userSelect: 'none'
          }}
        >
          <img
            ref={imgRef}
            src={imageUrl}
            alt="参考原图"
            onLoad={() => setImgLoaded(true)}
            crossOrigin="anonymous"
            style={{
              display: 'block',
              maxWidth: '100%',
              maxHeight: '62vh',
              objectFit: 'contain',
              pointerEvents: 'none'
            }}
          />
          <canvas
            ref={canvasRef}
            onMouseDown={startDraw}
            onMouseMove={draw}
            onMouseUp={endDraw}
            onMouseLeave={endDraw}
            onTouchStart={startDraw}
            onTouchMove={draw}
            onTouchEnd={endDraw}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              objectFit: 'contain',
              cursor: mode === 'brush' ? 'crosshair' : 'cell',
              touchAction: 'none'
            }}
          />
        </div>
        <p className="muted small" style={{ margin: 0 }}>
          用红色画笔涂抹需要局部修复/重绘的区域，保存后将自动转为黑白蒙版并填入。
        </p>
        {error && <p className="error small">{error}</p>}
      </div>

      <div className="modal-footer" style={{ marginTop: '14px' }}>
        <Button onClick={onClose} disabled={saving}>
          取消
        </Button>
        <Button primary onClick={handleExportAndSave} disabled={saving || !hasDrawn}>
          <Check size={15} /> {saving ? '正在生成并上传…' : '完成并应用蒙版'}
        </Button>
      </div>
    </Modal>
  );
}
