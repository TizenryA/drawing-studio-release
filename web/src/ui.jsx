import React, { useEffect, useId, useRef } from 'react';
import { X, ImageIcon, AlertCircle, ChevronDown } from 'lucide-react';

export function Button({ children, primary = false, className = '', ...props }) {
  return (
    <button type="button" className={`${primary ? 'primary' : 'button'} ${className}`.trim()} {...props}>
      {children}
    </button>
  );
}

export function IconButton({ icon: Icon, label, className = 'icon-button', ...props }) {
  return (
    <button type="button" className={className} aria-label={label} title={label} {...props}>
      <Icon size={18} />
    </button>
  );
}

export function Field({ label, children, hint, action }) {
  const labelId = useId();
  let labeled = false;
  const enhancedChildren = React.Children.map(children, child => {
    if (
      !labeled &&
      React.isValidElement(child) &&
      typeof child.type === 'string' &&
      ['input', 'select', 'textarea'].includes(child.type)
    ) {
      labeled = true;
      if (child.props['aria-label'] == null && child.props['aria-labelledby'] == null) {
        return React.cloneElement(child, { 'aria-labelledby': labelId });
      }
    }
    return child;
  });

  return (
    <div className="field">
      {action ? (
        <div className="field-head">
          <span id={labelId}>{label}</span>
          <div className="field-actions">{action}</div>
        </div>
      ) : (
        <span id={labelId}>{label}</span>
      )}
      {enhancedChildren}
      {hint && <small>{hint}</small>}
    </div>
  );
}

export function Empty({ children }) {
  return (
    <div className="empty">
      <ImageIcon size={28} strokeWidth={1.2} />
      <p>{children}</p>
    </div>
  );
}

export function ErrorBox({ error, retry }) {
  return error ? (
    <div className="error" role="alert">
      <AlertCircle size={17} />
      <span>{error}</span>
      {retry && <button type="button" onClick={retry}>重试</button>}
    </div>
  ) : null;
}

export function Cover({ src, name, className = '' }) {
  return src ? (
    <img
      className={`cover ${className}`.trim()}
      src={src}
      alt={name || ''}
      loading="lazy"
      onError={e => {
        e.currentTarget.style.visibility = 'hidden';
        if (e.currentTarget.parentElement) {
          e.currentTarget.parentElement.dataset.imageError = '图片加载失败';
        }
      }}
    />
  ) : (
    <span className={`cover placeholder ${className}`.trim()}>
      <ImageIcon size={22} strokeWidth={1.2} />
    </span>
  );
}

export function Modal({ title, onClose, children, wide = false, className = '' }) {
  const ref = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const backdropPressRef = useRef(false);

  useEffect(() => {
    const previous = document.activeElement;
    const old = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (ref.current && !ref.current.contains(document.activeElement)) {
      ref.current.focus({ preventScroll: true });
    }
    const key = e => {
      if (e.key === 'Escape') onCloseRef.current?.();
      if (e.key === 'Tab') {
        const a = ref.current?.querySelectorAll('button:not([disabled]),a[href],input,select,textarea,[tabindex="0"]');
        if (!a?.length) return;
        const first = a[0];
        const last = a[a.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', key);
    return () => {
      document.body.style.overflow = old;
      document.removeEventListener('keydown', key);
      if (previous && previous.isConnected && !['INPUT', 'TEXTAREA'].includes(previous.tagName)) {
        previous.focus?.({ preventScroll: true });
      }
    };
  }, []);

  return (
    <div
      className="modal-backdrop"
      onPointerDown={e => {
        backdropPressRef.current = e.target === e.currentTarget;
      }}
      onClick={e => {
        if (backdropPressRef.current && e.target === e.currentTarget) {
          onCloseRef.current?.();
        }
        backdropPressRef.current = false;
      }}
    >
      <section
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? 'wide' : ''} ${className}`.trim()}
      >
        <header className="modal-header">
          <h2>{title}</h2>
          <IconButton icon={X} label="关闭弹窗" onClick={() => onCloseRef.current?.()} />
        </header>
        <div className="modal-content">{children}</div>
      </section>
    </div>
  );
}

export function Section({ title, children, action }) {
  return (
    <section className="section">
      <div className="section-heading">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Fold({ title, children, open = false, memory }) {
  const key = 'studio-fold-' + (memory || title);
  const [expanded, setExpanded] = React.useState(() => {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? open;
    } catch {
      return open;
    }
  });
  return (
    <details
      className="fold"
      open={expanded}
      onToggle={e => {
        setExpanded(e.currentTarget.open);
        try {
          localStorage.setItem(key, JSON.stringify(e.currentTarget.open));
        } catch {}
      }}
    >
      <summary>
        {title}
        <ChevronDown size={17} />
      </summary>
      <div className="fold-content">{children}</div>
    </details>
  );
}

export function NumberField({ label, value, onChange, ...props }) {
  return (
    <Field label={label}>
      <input
        type="number"
        step="any"
        value={value ?? ''}
        onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        {...props}
      />
    </Field>
  );
}

export const stateNames = {
  off: '未启动',
  allocating: '分配 GPU 中',
  allocation_unknown: '确认分配中',
  bootstrapping: '准备环境中',
  starting: '启动 ComfyUI 中',
  starting_comfy: '启动 ComfyUI 中',
  syncing: '正在同步模型',
  queued_sync: '排队同步中',
  ready: 'ComfyUI 服务可用',
  stopping: '正在关闭',
  error: '异常',
  disconnected: '连接中断',
  queued: '排队中',
  preparing: '准备任务中',
  submitting: '提交任务中',
  running: '生成中',
  generating: '生成中',
  archiving: '归档中',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  cancelled: '已取消',
  drive: '仅 Drive',
  drive_only: '仅 Drive',
  downloading: '下载中',
  verifying: '校验中',
  cached: '已缓存',
};

export function stateTone(state) {
  if (['ready', 'completed', 'cached'].includes(state)) return 'ok';
  if (['running', 'generating', 'archiving', 'downloading', 'verifying', 'bootstrapping', 'starting', 'starting_comfy', 'syncing'].includes(state)) return 'info';
  if (['queued', 'queued_sync', 'preparing', 'submitting', 'allocating', 'allocation_unknown', 'stopping'].includes(state)) return 'warn';
  if (['failed', 'error', 'disconnected', 'interrupted'].includes(state)) return 'danger';
  return 'muted';
}

export function Status({ state, label }) {
  const tone = stateTone(state);
  const pulse = tone === 'info' ? 'pulse' : '';
  return (
    <span className={`status tone-${tone}`}>
      <i className={`${state === 'ready' ? 'ready' : ''} ${pulse}`.trim()} />
      {label || stateNames[state] || state || '状态未知'}
    </span>
  );
}

export const taskStage = task =>
  ['completed', 'failed', 'cancelled', 'interrupted', 'error'].includes(task?.status)
    ? task.status
    : task?.stage || task?.status;

export const isTaskActive = task =>
  ['queued', 'queued_sync', 'syncing', 'preparing', 'submitting', 'running', 'generating', 'archiving', 'downloading', 'verifying'].includes(
    taskStage(task),
  );

export function TaskStatus({ task, model = false }) {
  const stage = taskStage(task);
  const label = model && stage === 'ready' ? '已就绪' : undefined;
  return <Status state={stage} label={label} />;
}

const knownNumber = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;

export function TaskProgress({ task, model = false }) {
  const baseStage = taskStage(task);
  const stage = baseStage === 'syncing' && ['downloading', 'verifying'].includes(task.download_stage) ? task.download_stage : baseStage;

  if (stage === 'queued_sync') {
    return <p className="muted small">排队等待同步（默认并发 2 个）</p>;
  }

  if (['syncing', 'downloading', 'verifying'].includes(stage)) {
    const downloaded = knownNumber(task.downloaded_bytes) ? task.downloaded_bytes : null;
    const total = knownNumber(task.total_bytes) && task.total_bytes > 0 ? task.total_bytes : null;
    const percent =
      downloaded != null && total != null
        ? Math.min(100, (downloaded / total) * 100)
        : knownNumber(task.download_progress)
          ? Math.min(100, task.download_progress * 100)
          : null;
    const label = stage === 'syncing' ? '正在同步模型' : stage === 'verifying' ? '正在校验模型' : '正在下载模型';
    const name = task.model_name || task.filename || (model ? task.name : '');
    const bytes =
      downloaded != null || total != null
        ? `${downloaded != null ? (downloaded / 1073741824).toFixed(2) : '—'} / ${total != null ? (total / 1073741824).toFixed(2) : '—'} GB`
        : null;
    return (
      <>
        <p className="muted small">
          {label}
          {!model && name ? ` · ${name}` : ''}
          {bytes ? ` · ${bytes}` : ''}
          {percent != null ? ` · ${Math.round(percent)}%` : ''}
        </p>
        {percent != null && <progress aria-label="模型下载进度" value={percent} max="100" />}
      </>
    );
  }

  if (!model && ['running', 'generating'].includes(stage)) {
    const step = task.sampling_step;
    const total = task.total_steps ?? task.sampling_steps;
    const percent = knownNumber(step) && knownNumber(total) && total > 0 ? Math.min(100, (step / total) * 100) : null;
    const phaseLabel =
      {
        model_loading: '模型加载',
        loading_model: '模型加载',
        sampling: '采样推理',
        vae_decode: 'VAE 解码',
        vae_decoding: 'VAE 解码',
        vae_encoding: 'VAE 编码',
        conditioning: '提示词编码',
        executing: '执行工作流',
        upscaling: 'AI 精修中',
        saving: '视频编码保存',
      }[task.execution_phase] ||
      task.execution_phase ||
      task.node_label ||
      task.node_type ||
      task.phase ||
      task.node_name ||
      (task.current_node ? `执行节点 ${task.current_node}` : null);

    return (
      <>
        {phaseLabel && (
          <p className="muted small">
            {phaseLabel}
            {percent != null ? ` · ${step}/${total} 步 (${Math.round(percent)}%)` : ''}
          </p>
        )}
        {percent != null ? (
          <progress aria-label="采样进度" value={percent} max="100" />
        ) : (
          !phaseLabel && <p className="muted small">正在生成</p>
        )}
      </>
    );
  }

  return !model && isTaskActive(task) ? <p className="muted small">{stateNames[stage] || stage}</p> : null;
}

export function formatSize(value) {
  if (value == null) return '大小未知';
  if (typeof value === 'string') return value;
  return value > 1073741824 ? `${(value / 1073741824).toFixed(1)} GB` : `${(value / 1048576).toFixed(1)} MB`;
}

export function words(value) {
  return Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
      : [];
}
