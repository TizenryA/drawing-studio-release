import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Plus, X, Play, Shuffle, Save, Upload, Maximize2, Clock, FileText, Brush,
  Image as ImageIcon, Film, Sparkles, SlidersHorizontal, Cpu, Layers, ChevronRight, RotateCcw, LayoutGrid,
} from 'lucide-react';
import { api, list, post, upload, enc } from '../api';
import { Button, IconButton, Field, NumberField, Section, Fold, ErrorBox, Cover, Status, TaskStatus, taskStage, stateNames } from '../ui';
import { LoraPicker, WorkflowPicker, ModelPicker, UpscaleModal, baseUpscale, PromptParserModal } from '../components/Pickers';
import MossLayoutModal from '../components/MossLayoutModal';
import { MaskEditorModal } from '../components/MaskEditor';
import KeyframeGrid from '../components/KeyframeGrid';
import { workflowParams, validTasks, taskTitle, taskTypeBadge, cleanModelName, classifyWorkflow } from '../taskData';
import { defaultParams } from '../defaultParams';
import { containsMixedPrompt, parsePromptText } from '../promptParser';
import {
  snap16,
  isMossLayoutPrompt,
  parseMossLayoutPrompt,
  rescaleCompiledMossLayout,
  stripNegativeTextConflicts,
} from '../mossLayout';
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
  workflowHasShift,
  detectMossVariant,
  getMossParamWarnings,
  applyMossPresetToParams,
} from '../promptBatchParams';
import {
  h3Mode, videoPayload, videoTiming, videoResolutions, videoDurations,
  videoOrientation, h3References, h3Keyframes, computeKeyframeIndices,
  continueFrameOptions, moveReference, frameInterpOptions, parseFrameInterp,
  videoCrfOptions, seedvr2ResolutionOptions, seedvr2BatchOptions, seedvr2ColorOptions,
} from '../h3Video';
import { setLoraStrength, removeLora } from '../loraSelection';

export { defaultParams };

export default function Draw({ params: p, setParams, models, presets, workflows, tasks, runtime, galleryProjects, refreshProjects, submit, refresh, errors, setNotice, navigate }) {
  const [loraOpen, setLoraOpen] = useState(false);
  const [workflowPickerOpen, setWorkflowPickerOpen] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [upscaleOpen, setUpscaleOpen] = useState(false);
  const [parserOpen, setParserOpen] = useState(false);
  const [maskEditorOpen, setMaskEditorOpen] = useState(false);
  const [layoutModalOpen, setLayoutModalOpen] = useState(false);
  const [customSize, setCustomSize] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState('');
  const [tab, setTab] = useState('prompt');
  const [history, setHistory] = useState(() => {
    try { return JSON.parse(localStorage.getItem('studio-prompts')) || []; } catch { return []; }
  });
  const [showHistory, setShowHistory] = useState(false);
  const [presetName, setPresetName] = useState('');
  const [galleryVideos, setGalleryVideos] = useState([]);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');

  const closeLora = useCallback(() => setLoraOpen(false), []);
  const closeWorkflowPicker = useCallback(() => setWorkflowPickerOpen(false), []);
  const closeModelPicker = useCallback(() => setModelPickerOpen(false), []);
  const closeUpscale = useCallback(() => setUpscaleOpen(false), []);
  const closeParser = useCallback(() => setParserOpen(false), []);
  const closeMaskEditor = useCallback(() => setMaskEditorOpen(false), []);
  const closeLayoutModal = useCallback(() => setLayoutModalOpen(false), []);

  const uploadLock = useRef(false);
  const workflowEpoch = useRef(0);
  useEffect(() => {
    const invalidate = () => { workflowEpoch.current++; };
    window.addEventListener('studio:params-replaced', invalidate);
    return () => {
      invalidate();
      window.removeEventListener('studio:params-replaced', invalidate);
    };
  }, []);

  const set = (k, v) =>
    setParams(old => ({
      ...old,
      [k]: v,
      ...(k === 'mode'
        ? { workflow: '', workflow_id: '', ref_images: undefined, keyframes: undefined, keyframe_frames: undefined, source_video: '', source_video_id: '' }
        : {}),
      ...(k === 'workflow' ? { workflow_id: v } : {}),
      ...(k === 'input_image' ? { image: v } : {}),
    }));

  const selectedWorkflow = workflows.find(w => w.id === (p.workflow || p.workflow_id));
  const hasCustomWorkflow = !!selectedWorkflow?.workflow;
  const videoMode = h3Mode(selectedWorkflow);
  const isH3Video = !!videoMode;
  const isVideoRefine = p.mode === 'video_seedvr2' || (p.workflow || p.workflow_id) === 'video-seedvr2';
  const isAnyVideo = isH3Video || isVideoRefine;
  const scene = isH3Video ? 'video' : (['video_seedvr2', 'seedvr2', 'supir'].includes(p.mode) || ['video-seedvr2', 'seedvr2', 'supir'].includes(p.workflow || p.workflow_id)) ? 'refine' : 'image';

  const baseModels = models.filter(
    m =>
      ['checkpoint', 'checkpoints'].includes(m.kind) ||
      (['diffusion_models', 'unet'].includes(m.kind) && /moss_image/i.test(String(m.filename || m.name || ''))),
  );

  const timing = isH3Video ? videoPayload(p, selectedWorkflow) : null;
  const refs = videoMode === 'ref' ? h3References(p) : [];
  const kfs = (videoMode === 'keyframes' || videoMode === 'continue') ? h3Keyframes(p, videoMode === 'continue') : [];
  const kfIndices =
    (videoMode === 'keyframes' || videoMode === 'continue') && timing
      ? computeKeyframeIndices(kfs.length, timing.length, {
          continueMode: videoMode === 'continue',
          continueFrames: Number(p.continue_frames) || 5,
          customFrames: Array.isArray(p.keyframe_frames) && p.keyframe_frames.length === kfs.length ? p.keyframe_frames : null,
        })
      : [];

  useEffect(() => {
    if (videoMode !== 'continue' && !isVideoRefine) return;
    let alive = true;
    api('/api/gallery?media_type=video&order=desc&page=1&limit=40')
      .then(payload => {
        const rows = list(payload);
        if (alive && Array.isArray(rows)) setGalleryVideos(rows.filter(r => r.media_type === 'video').slice(0, 40));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [videoMode, isVideoRefine]);

  const loadWorkflowById = async id => {
    workflowEpoch.current++;
    setCustomSize(false);
    if (!id) {
      setParams(o => {
        const next = { ...defaultParams, ...o, project: o.project || '', workflow: '', workflow_id: '', mode: o.mode === 'custom' ? 'txt2img' : o.mode };
        for (const key of ['duration_seconds', 'length', 'fps', 'actual_duration_seconds', 'ref_images', 'keyframes', 'keyframe_frames', 'source_video', 'source_video_id', 'continue_frames', 'frame_interp', 'output_fps']) {
          delete next[key];
        }
        if (baseModels.length && !baseModels.some(m => m.id === next.model || m.filename === next.model || m.name === next.model)) {
          next.model = (baseModels.find(m => m.status === 'ready') || baseModels[0]).id;
        }
        const resolvedNext = baseModels.find(m => m.id === next.model || m.filename === next.model || m.name === next.model);
        const nextIsMoss = /moss_image/i.test(String(resolvedNext?.filename || resolvedNext?.name || next.model || ''));
        if (!nextIsMoss) {
          delete next.shift;
        } else if (next.shift === undefined || next.shift === '') {
          next.shift = /sft/i.test(String(resolvedNext?.filename || resolvedNext?.name || next.model || '')) ? 3.5 : 5.0;
        }
        return next;
      });
      return;
    }
    try {
      const w = await api(`/api/workflows/${enc(id)}`);
      const wp = workflowParams(w, models);
      wp.project = p.project || '';
      if (w.id.includes('moss') || JSON.stringify(w).includes('moss_image')) {
        if (!w.mapping?.sampler && (!wp.sampler || wp.sampler === defaultParams.sampler)) {
          wp.sampler = w.id.includes('sft') ? 'er_sde' : 'euler';
        }
        if (!w.mapping?.scheduler && (!wp.scheduler || wp.scheduler === defaultParams.scheduler)) {
          wp.scheduler = 'simple';
        }
        if (!w.mapping?.cfg && (wp.cfg > 3.5 || !wp.cfg)) wp.cfg = 3.0;
      }
      setParams(wp);
      setError('');
      setNotice(`已切换至：${w.name}`);
    } catch (e) {
      setError(e.message);
    }
  };

  const switchMossVariant = async targetId => {
    if (!targetId || targetId === selectedWorkflow?.id) return;
    workflowEpoch.current++;
    setCustomSize(false);
    try {
      const w = await api(`/api/workflows/${enc(targetId)}`);
      const wp = workflowParams(w, models);
      const baseLoras = wp.loras || [];
      const extraLoras = (p.loras || []).filter(
        l => l.node_id == null && l.enabled !== false && !baseLoras.some(wl => wl.id === l.id || wl.name === l.name),
      );
      const oldBakedPrompt = String(selectedWorkflow?.workflow?.['6']?.inputs?.text || '').trim();
      const oldBakedNeg = String(selectedWorkflow?.workflow?.['7']?.inputs?.text || '').trim();
      const curPrompt = String(p.prompt || '').trim();
      const curNeg = String(p.negative_prompt || '').trim();
      setParams({
        ...wp,
        project: p.project || '',
        loras: [...baseLoras, ...extraLoras],
        prompt: curPrompt && curPrompt !== oldBakedPrompt ? p.prompt : wp.prompt,
        negative_prompt: curNeg && curNeg !== oldBakedNeg ? p.negative_prompt : wp.negative_prompt,
        n: p.n || wp.n || 1,
        batch_size: p.n || wp.batch_size || 1,
        upscale: p.upscale || wp.upscale,
        seed: -1,
      });
      setError('');
      const preset = MOSS_VARIANT_PRESETS[targetId === 'moss-sft' ? 'sft' : 'base'];
      setNotice(`已切换至 ${preset.label}（${preset.summary}）`);
    } catch (e) {
      setError(e.message);
    }
  };

  const switchScene = nextScene => {
    if (nextScene === scene) return;
    if (nextScene === 'image') {
      const fallback = workflows.find(w => w.id.includes('moss'))?.id || '';
      loadWorkflowById(fallback);
      if (!fallback) set('mode', 'txt2img');
    } else if (nextScene === 'video') {
      const prefer =
        workflows.find(w => w.id === 'h3-fl2va-keyframes-8step-int8') ||
        workflows.find(w => h3Mode(w) === 'keyframes') ||
        workflows.find(w => !!h3Mode(w));
      if (prefer) loadWorkflowById(prefer.id);
      else setNotice('尚未检测到视频工作流，请先导入 H3 工作流');
    } else if (nextScene === 'refine') {
      workflowEpoch.current++;
      setParams(o => ({
        ...defaultParams,
        ...o,
        mode: 'video_seedvr2',
        workflow: 'video-seedvr2',
        workflow_id: 'video-seedvr2',
        resolution: o.resolution || 1080,
        frame_batch_size: o.frame_batch_size || 5,
        temporal_overlap: o.temporal_overlap ?? 2,
        color_correction: o.color_correction || 'lab',
        blocks_to_swap: o.blocks_to_swap ?? (/G4|RTX\s*PRO\s*6000|A100|H100|H200/i.test(runtime?.actual_gpu || runtime?.gpu || '') ? 0 : 16),
        video_crf: o.video_crf ?? 15,
      }));
    }
  };

  const switchH3Variant = (targetRoute, targetPrecision = null) => {
    const curPrec = (selectedWorkflow?.id || '').includes('bf16') ? 'bf16' : 'int8';
    const prec = targetPrecision || curPrec;
    const match =
      workflows.find(w => h3Mode(w) === targetRoute && w.id.includes(prec) && !w.id.includes('hq20')) ||
      workflows.find(w => h3Mode(w) === targetRoute && w.id.includes(prec)) ||
      workflows.find(w => h3Mode(w) === targetRoute);
    if (match && match.id !== selectedWorkflow?.id) loadWorkflowById(match.id);
  };

  const fileChange = async (key, file) => {
    if (!file) return;
    setUploading(key);
    setError('');
    try {
      const data = await upload(file);
      set(key, data.path || data.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading('');
    }
  };

  const generate = async () => {
    if (uploadLock.current || uploading || busy) return;
    setBusy(true);
    setError('');
    try {
      await submit({ ...p, loras: p.loras.map(l => ({ ...l })), upscale: { ...p.upscale } });
      if (p.prompt) {
        const next = [p.prompt, ...history.filter(h => h !== p.prompt)].slice(0, 30);
        setHistory(next);
        localStorage.setItem('studio-prompts', JSON.stringify(next));
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const uploadReferences = async files => {
    if (uploadLock.current || !files.length) return;
    const epoch = workflowEpoch.current;
    const capacity = 9 - refs.length;
    const accepted = [...files].slice(0, Math.max(0, capacity));
    const failed = [];
    if (files.length > capacity) failed.push('最多 9 张，超出部分未上传');
    uploadLock.current = true;
    setUploading('ref_images');
    setError('');
    try {
      for (const file of accepted) {
        try {
          const data = await upload(file);
          const path = data.path || data.id;
          if (!path) throw Error('上传未返回图片路径');
          if (epoch === workflowEpoch.current) setParams(old => ({ ...old, ref_images: [...h3References(old), path].slice(0, 9) }));
        } catch (e) {
          failed.push(`${file.name}：${e.message}`);
        }
      }
    } finally {
      uploadLock.current = false;
      setUploading('');
      if (failed.length) setError('部分图片未上传：' + failed.join('；'));
    }
  };

  const uploadKeyframes = async files => {
    if (uploadLock.current || !files.length) return;
    const epoch = workflowEpoch.current;
    const capacity = 9 - kfs.length;
    const accepted = [...files].slice(0, Math.max(0, capacity));
    const failed = [];
    if (files.length > capacity) failed.push('最多 9 张，超出部分未上传');
    uploadLock.current = true;
    setUploading('keyframes');
    setError('');
    try {
      for (const file of accepted) {
        try {
          const data = await upload(file);
          const path = data.path || data.id;
          if (!path) throw Error('上传未返回图片路径');
          if (epoch === workflowEpoch.current) setParams(old => ({ ...old, keyframes: [...h3Keyframes(old, videoMode === 'continue'), path].slice(0, 9), keyframe_frames: undefined }));
        } catch (e) {
          failed.push(`${file.name}：${e.message}`);
        }
      }
    } finally {
      uploadLock.current = false;
      setUploading('');
      if (failed.length) setError('部分关键帧未上传：' + failed.join('；'));
    }
  };

  const uploadSourceVideo = async file => {
    if (!file || uploadLock.current) return;
    const epoch = workflowEpoch.current;
    uploadLock.current = true;
    setUploading('source_video');
    setError('');
    try {
      const data = await upload(file);
      const path = data.path || data.id;
      if (!path) throw Error('上传未返回视频路径');
      if (epoch === workflowEpoch.current) setParams(old => ({ ...old, source_video: path, source_video_id: '' }));
    } catch (e) {
      if (epoch === workflowEpoch.current) setError(e.message);
    } finally {
      uploadLock.current = false;
      setUploading('');
    }
  };

  const setKeyframeFrame = (index, raw) => {
    setParams(old => {
      const len = timing?.length || 124;
      const base = Array.isArray(old.keyframe_frames) && old.keyframe_frames.length === kfs.length ? [...old.keyframe_frames] : Array(kfs.length).fill(null);
      base[index] = raw === '' ? null : Math.max(videoMode === 'continue' ? (Number(old.continue_frames) || 5) : 0, Math.min(len - 1, Number(raw)));
      return { ...old, keyframe_frames: base };
    });
  };

  const setDuration = seconds => setParams(o => ({ ...o, duration_seconds: seconds, ...videoTiming(seconds) }));
  const setVideoSize = (w, h) => setParams(o => ({ ...o, width: w, height: h }));

  const loraNodes = hasCustomWorkflow ? Object.values(selectedWorkflow.workflow || {}).filter(n => ['LoraLoader', 'LoraLoaderModelOnly'].includes(n.class_type)) : [];
  const frozenLora = hasCustomWorkflow && loraNodes.length === 0;
  const frozenGraph = hasCustomWorkflow;
  const activeLoras = p.loras.filter(l => l.enabled !== false);
  const currentModel = models.find(m => m.id === p.model || m.filename === p.model || m.name === p.model || m.comfy_name === p.model);

  const builtinModelRefs = hasCustomWorkflow
    ? Object.values(selectedWorkflow.workflow || {}).flatMap(n => {
        const i = n?.inputs || {};
        const items = [];
        for (const [k, role] of [['unet_name', '底模'], ['ckpt_name', '底模'], ['clip_name', 'CLIP'], ['clip_name1', 'CLIP'], ['vae_name', 'VAE']]) {
          if (typeof i[k] === 'string' && i[k]) {
            const matched = models.find(m => [m.id, m.name, m.filename, m.comfy_name].includes(i[k]));
            items.push({ role, name: cleanModelName(matched?.name || i[k]), ready: matched ? taskStage(matched) === 'ready' : false });
          }
        }
        return items;
      })
    : [];

  const wfMeta = selectedWorkflow ? classifyWorkflow(selectedWorkflow) : null;
  const isBf16Selected = (selectedWorkflow?.id || '').includes('bf16');
  const mossVariant = detectMossVariant(selectedWorkflow, p, models);
  const isMossBaseModel = !selectedWorkflow && mossVariant !== null;
  const hasAuraFlowShift = workflowHasShift(selectedWorkflow) || isMossBaseModel || (p.shift !== undefined && p.shift !== '');
  const mossWarnings = !isH3Video && !isVideoRefine ? getMossParamWarnings(mossVariant, p) : [];
  const isLayoutPrompt = !isH3Video && !isVideoRefine && isMossLayoutPrompt(p.prompt);
  const parsedLayout = isLayoutPrompt ? parseMossLayoutPrompt(p.prompt, p.width, p.height) : null;
  const mossLoraSum = activeLoras.reduce((sum, l) => sum + (Number.isFinite(Number(l.strength)) ? Math.abs(Number(l.strength)) : 0), 0);
  const hasMossBaseWorkflow = workflows.some(w => w.id === 'moss-base');
  const hasMossSftWorkflow = workflows.some(w => w.id === 'moss-sft');
  const isMossVariantPair =
    scene === 'image' &&
    ['moss-base', 'moss-sft'].includes(selectedWorkflow?.id) &&
    hasMossBaseWorkflow &&
    hasMossSftWorkflow;
  const hasMappedDenoise = !!selectedWorkflow?.mapping?.denoise;
  const samplerChoices = p.sampler && !SAMPLER_OPTIONS.includes(p.sampler) ? [p.sampler, ...SAMPLER_OPTIONS] : SAMPLER_OPTIONS;
  const schedulerChoices = p.scheduler && !SCHEDULER_OPTIONS.includes(p.scheduler) ? [p.scheduler, ...SCHEDULER_OPTIONS] : SCHEDULER_OPTIONS;
  const sizeMatch = detectImageTierAndRatio(p.width, p.height);
  const inferredSize = inferTierAndRatio(p.width, p.height);
  const vramRisk = !isH3Video ? highResVramLevel(p.width, p.height) : '';

  const handleParsedApply = ({ prompt, negative_prompt, params, count }) => {
    const hasParamUpdates = !!(params && Object.keys(params).length > 0);
    setParams(old => {
      const next = { ...old, prompt, negative_prompt };
      if (typeof count === 'number' && Number.isFinite(count) && count >= 1) next.n = Math.round(count);
      if (hasParamUpdates) {
        if (typeof params.width === 'number' && Number.isFinite(params.width) && params.width > 0) next.width = params.width;
        if (typeof params.height === 'number' && Number.isFinite(params.height) && params.height > 0) next.height = params.height;
        if (typeof params.steps === 'number' && Number.isFinite(params.steps) && params.steps > 0) next.steps = params.steps;
        if (typeof params.cfg === 'number' && Number.isFinite(params.cfg) && params.cfg >= 0) next.cfg = params.cfg;
        if (typeof params.shift === 'number' && Number.isFinite(params.shift) && params.shift >= 0) next.shift = params.shift;
        if (typeof params.seed === 'number' && Number.isFinite(params.seed)) next.seed = params.seed;
        if (params.sampler) next.sampler = params.sampler;
        if (params.scheduler) next.scheduler = params.scheduler;
        if (typeof params.denoise === 'number' && Number.isFinite(params.denoise) && params.denoise >= 0) next.denoise = params.denoise;
      }
      return next;
    });
    setNotice(hasParamUpdates ? '已识别并应用提示词与参数' : '已识别并应用提示词');
  };

  const handleBatchGenerate = async (items, onProgress) => {
    if (uploadLock.current || uploading) throw Error('素材仍在上传，请稍候');
    let successCount = 0;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (onProgress) onProgress(i + 1, items.length);
      const count = item.n || p.n || 1;
      await submit({
        ...p,
        ...(item.params || {}),
        prompt: item.prompt,
        negative_prompt: item.negative_prompt,
        n: count,
        batch_size: count,
        loras: p.loras.map(l => ({ ...l })),
        upscale: { ...p.upscale },
      });
      successCount++;
    }
    setNotice(`已批量提交 ${successCount} 个生成任务`);
  };

  const generateLabel = busy
    ? '正在提交…'
    : isVideoRefine
      ? `开始视频精修（→ ${p.resolution || 1080}p · CRF ${p.video_crf ?? 15} · 1 段）`
      : isH3Video
        ? `生成 1 段视频（${(timing?.actual_duration_seconds ?? 5).toFixed(1)}s · ${p.width}×${p.height}）`
        : `生成（${p.n || 1} 张）`;

  return (
    <>
      {/* Top 3-scene segmented bar */}
      <div className="scene-switch" role="tablist" aria-label="创作场景">
        <button type="button" role="tab" aria-selected={scene === 'image'} className={scene === 'image' ? 'active' : ''} onClick={() => switchScene('image')}>
          <ImageIcon size={16} />
          <span>图片生成</span>
        </button>
        <button type="button" role="tab" aria-selected={scene === 'video'} className={scene === 'video' ? 'active' : ''} onClick={() => switchScene('video')}>
          <Film size={16} />
          <span>视频生成</span>
        </button>
        <button type="button" role="tab" aria-selected={scene === 'refine'} className={scene === 'refine' ? 'active' : ''} onClick={() => switchScene('refine')}>
          <Sparkles size={16} />
          <span>AI 精修</span>
        </button>
      </div>

      <div className="draw-layout">
        <div className="stack">
          {/* Workflow Selector Card */}
          <section className="section">
            <div className="section-heading">
              <h2>工作流与方案</h2>
              <Button className="mini-btn" onClick={() => setWorkflowPickerOpen(true)}>
                <Layers size={14} />
                切换工作流
              </Button>
            </div>

            <button type="button" className="selector-card" onClick={() => setWorkflowPickerOpen(true)}>
              <div className="grow" style={{ textAlign: 'left', minWidth: 0 }}>
                <strong className="truncate" style={{ display: 'block' }}>
                  {selectedWorkflow ? selectedWorkflow.name : '按生成模式自动匹配工作流'}
                </strong>
                <div className="row wrap" style={{ gap: '6px', marginTop: '6px' }}>
                  {wfMeta ? (
                    <>
                      <span className="pill tone-info">{wfMeta.categoryLabel}</span>
                      {wfMeta.badges.map(b => <span key={b} className="pill tone-muted">{b}</span>)}
                    </>
                  ) : isMossBaseModel ? (
                    <span className="pill tone-info">
                      MOSS 原生 · {{ txt2img: '文生图', img2img: '图生图', inpaint: '局部修复', hires: '高清重绘', upscale: '超分', seedvr2: 'AI 精修' }[p.mode] || p.mode}（AuraFlow + Flux2 VAE）
                    </span>
                  ) : (
                    <span className="pill tone-muted">自动匹配 · 自由选底模与 LoRA</span>
                  )}
                </div>
              </div>
              <ChevronRight size={17} className="muted" />
            </button>

            {/* Quick MOSS Base vs SFT switcher when using MOSS Base/SFT workflows */}
            {isMossVariantPair && (
              <div className="chips" style={{ marginTop: '10px' }}>
                <button
                  type="button"
                  className={selectedWorkflow?.id === 'moss-base' ? 'selected' : ''}
                  onClick={() => switchMossVariant('moss-base')}
                >
                  MOSS Base · 厚涂（Shift 5.0 / 36步）
                </button>
                <button
                  type="button"
                  className={selectedWorkflow?.id === 'moss-sft' ? 'selected' : ''}
                  onClick={() => switchMossVariant('moss-sft')}
                >
                  MOSS SFT · 稳结构/分镜（Shift 3.5 / 28步）
                </button>
              </div>
            )}

            {/* Quick H3 route & precision toggles when in Video scene */}
            {scene === 'video' && (
              <div className="stack" style={{ gap: '8px', marginTop: '12px' }}>
                <div className="chips">
                  {[
                    ['keyframes', '路线A · 多关键帧'],
                    ['continue', '路线B · 尾段续写'],
                    ['ref', '参考图 Ref2V'],
                  ].map(([r, label]) => (
                    <button key={r} type="button" className={videoMode === r ? 'selected' : ''} onClick={() => switchH3Variant(r)}>
                      {label}
                    </button>
                  ))}
                </div>
                <div className="row between wrap" style={{ gap: '8px' }}>
                  <div className="chips">
                    <button type="button" className={!isBf16Selected ? 'selected' : ''} onClick={() => switchH3Variant(videoMode || 'keyframes', 'int8')}>
                      INT8 极速（21GB）
                    </button>
                    <button type="button" className={isBf16Selected ? 'selected' : ''} onClick={() => switchH3Variant(videoMode || 'keyframes', 'bf16')}>
                      BF16 原精（66GB）
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Refine sub-mode toggle */}
            {scene === 'refine' && (
              <div className="chips" style={{ marginTop: '12px' }}>
                <button
                  type="button"
                  className={isVideoRefine ? 'selected' : ''}
                  onClick={() => {
                    workflowEpoch.current++;
                    setParams(o => ({ ...defaultParams, ...o, mode: 'video_seedvr2', workflow: 'video-seedvr2', workflow_id: 'video-seedvr2' }));
                  }}
                >
                  SeedVR2 视频精修
                </button>
                <button
                  type="button"
                  className={!isVideoRefine ? 'selected' : ''}
                  onClick={() => {
                    workflowEpoch.current++;
                    setParams(o => ({ ...defaultParams, ...o, mode: 'seedvr2', workflow: '', workflow_id: '' }));
                  }}
                >
                  图像精修 (SeedVR2 / 超分)
                </button>
              </div>
            )}

            {/* Image generation sub-mode selector */}
            {scene === 'image' && (
              <label className="mode-row" style={{ marginTop: '10px' }}>
                <span>生成模式</span>
                <select aria-label="生成模式" value={p.mode} onChange={e => { workflowEpoch.current++; set('mode', e.target.value); }}>
                  {[['txt2img', '基础出图'], ['img2img', '图生图'], ['inpaint', '局部修复'], ['hires', '高清重绘'], ['upscale', '单独超分'], ['custom', '自定义工作流']].map(([v, n]) => (
                    <option key={v} value={v}>{n}</option>
                  ))}
                </select>
              </label>
            )}

            <div className="project-select-row" style={{ marginTop: '10px' }}>
              <span>归属项目</span>
              <div className="row" style={{ gap: '6px', minWidth: 0, flex: 1 }}>
                <select aria-label="归属项目" value={p.project || ''} onChange={e => set('project', e.target.value)}>
                  <option value="">未分类（默认）</option>
                  {(galleryProjects?.projects || []).map(proj => (
                    <option key={proj.name} value={proj.name}>
                      {proj.name}（{proj.count}）
                    </option>
                  ))}
                  {p.project && !(galleryProjects?.projects || []).some(proj => proj.name === p.project) && (
                    <option value={p.project}>{p.project}</option>
                  )}
                </select>
                <IconButton
                  icon={Plus}
                  label="新建项目"
                  onClick={() => { setNewProjectOpen(v => !v); setNewProjectName(''); }}
                />
              </div>
            </div>
            {newProjectOpen && (
              <div className="input-action" style={{ marginTop: '8px' }}>
                <input
                  placeholder="新项目名（自动建 Drive 文件夹）"
                  aria-label="新项目名"
                  value={newProjectName}
                  onChange={e => setNewProjectName(e.target.value)}
                />
                <Button
                  primary
                  disabled={!newProjectName.trim()}
                  onClick={async () => {
                    try {
                      const created = await post('/api/gallery/projects', { name: newProjectName.trim() });
                      const name = created?.name || newProjectName.trim();
                      set('project', name);
                      setNewProjectOpen(false);
                      setNewProjectName('');
                      await refreshProjects?.();
                      setNotice(`已创建项目「${name}」`);
                    } catch (e) {
                      setError(e.message);
                    }
                  }}
                >
                  创建
                </Button>
              </div>
            )}
          </section>

          {/* Model & LoRA Section (hidden in video_seedvr2 since it uses fixed SeedVR2 DiT+VAE) */}
          {!isVideoRefine && (
            <Fold
              title={
                <span className="model-summary">
                  <span className="truncate" title={currentModel?.name || p.model}>
                    {frozenGraph ? '工作流绑定模型' : cleanModelName(currentModel?.name || p.model) || '选择基础模型'}
                  </span>
                  <span className="lora-count">LoRA × {activeLoras.length}</span>
                </span>
              }
              memory="draw-model"
            >
              <ErrorBox error={errors.models} />
              {frozenGraph ? (
                <div className="stack" style={{ gap: '10px' }}>
                  <p className="muted small">当前工作流使用预设节点绑定的模型；点击「切换工作流」可更换方案。</p>
                  {builtinModelRefs.length > 0 && (
                    <div className="row wrap" style={{ gap: '6px' }}>
                      {builtinModelRefs.map((ref, idx) => (
                        <span key={idx} className={`pill ${ref.ready ? 'tone-ok' : 'tone-muted'}`}>
                          {ref.role}：{ref.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <button type="button" className="selector-card" onClick={() => setModelPickerOpen(true)}>
                  <Cover src={currentModel?.cover} name={currentModel?.name} />
                  <div className="grow" style={{ textAlign: 'left', minWidth: 0 }}>
                    <strong className="truncate" style={{ display: 'block' }}>
                      {cleanModelName(currentModel?.name || p.model) || '点击选择基础模型'}
                    </strong>
                    <small className="muted">
                      {currentModel ? `${currentModel.family || '自定义'} · ${taskStage(currentModel) === 'ready' ? 'GPU 已就绪' : stateNames[taskStage(currentModel)] || '在 Drive'}` : '浏览、搜索并选择 Checkpoint 基础模型'}
                    </small>
                  </div>
                  <ChevronRight size={17} className="muted" />
                </button>
              )}

              <Button disabled={frozenLora} onClick={() => setLoraOpen(true)}>
                <Plus size={15} />
                {frozenLora ? '当前工作流不含 LoRA 节点' : '选择 / 管理 LoRA'}
              </Button>
              {activeLoras.length > 0 ? (
                <div className="stack" style={{ gap: '6px' }}>
                  <div className="selected-loras">
                    {activeLoras.map(l => (
                      <div className="selected-lora" key={l.node_id ?? l.id ?? l.name}>
                        <span className="truncate">{cleanModelName(l.name || l.id)}</span>
                        <input
                          disabled={frozenLora}
                          type="number"
                          step="0.05"
                          aria-label={`${l.name} 权重`}
                          value={l.strength}
                          onChange={e => set('loras', p.loras.map(item => (item === l ? setLoraStrength(item, e.target.value === '' ? '' : Number(e.target.value)) : item)))}
                        />
                        <IconButton disabled={frozenLora} icon={X} label={`移除 ${l.name}`} onClick={() => set('loras', removeLora(p.loras, l))} />
                      </div>
                    ))}
                  </div>
                  {mossVariant && activeLoras.length >= 2 && (
                    <p className={mossLoraSum > 1.85 ? 'res-vram-warn' : 'muted small'} style={{ margin: 0 }}>
                      已叠加 {activeLoras.length} 个 LoRA · 权重之和 {mossLoraSum.toFixed(2)}
                      {mossLoraSum > 1.85
                        ? '（建议画风 LoRA 0.65~0.75 + 概念 LoRA 0.70~0.90，总和尽量 ≤ 1.80）'
                        : ' · 已自动串联接入工作流'}
                    </p>
                  )}
                </div>
              ) : (
                !frozenLora && <p className="muted small">可自由叠加多个风格或概念 LoRA（如木马/绳缚/海报/漫画特效），每项独立调节权重。</p>
              )}
            </Fold>
          )}

          {/* Video SeedVR2 Source Picker */}
          {isVideoRefine && (
            <Section title="选择待精修视频">
              <div className="stack" style={{ gap: '12px' }}>
                <Field label="从图库选择已生成视频">
                  <select
                    aria-label="从图库选择待精修视频"
                    value={p.source_video_id || ''}
                    onChange={e => {
                      const vid = e.target.value;
                      const found = galleryVideos.find(v => v.id === vid);
                      setParams(old => ({
                        ...old,
                        source_video_id: vid,
                        source_video: found ? found.filename || '' : '',
                        parent_id: vid || undefined,
                      }));
                    }}
                  >
                    <option value="">请选择图库视频，或下方上传本地文件…</option>
                    {galleryVideos.map(v => (
                      <option key={v.id} value={v.id}>
                        {v.width}×{v.height} · {(v.duration || 0).toFixed(1)}s · {(v.params?.prompt || v.filename || v.id).slice(0, 28)}
                      </option>
                    ))}
                  </select>
                </Field>
                <label className="upload-field">
                  <Upload size={17} />
                  <span>{uploading === 'source_video' ? '正在上传源视频…' : p.source_video ? '更换上传视频' : '上传本地待精修视频（mp4 / webm）'}</span>
                  <input
                    aria-label="SeedVR2 待精修视频"
                    type="file"
                    accept="video/mp4,video/webm,video/quicktime"
                    disabled={!!uploading}
                    onChange={e => { uploadSourceVideo(e.target.files[0]); e.target.value = ''; }}
                  />
                </label>
                {p.source_video && <p className="file-path">当前源视频：{p.source_video}</p>}
              </div>
            </Section>
          )}

          {/* Image Input / Mask for img2img / inpaint / image refine */}
          {!isAnyVideo && ['img2img', 'inpaint', 'hires', 'upscale', 'seedvr2', 'supir'].includes(p.mode) && (
            <Section title="参考图片与重绘">
              <div className="stack">
                <label className="upload-field">
                  <Upload size={17} />
                  <span>{uploading === 'input_image' ? '正在上传…' : p.input_image ? '更换参考图片' : '上传参考图片'}</span>
                  <input type="file" accept="image/*" disabled={!!uploading} onChange={e => fileChange('input_image', e.target.files[0])} />
                </label>
                {p.input_image && <p className="file-path">已选择：{p.input_image}</p>}
                {p.mode === 'inpaint' && (
                  <>
                    <div className="row" style={{ gap: '8px', alignItems: 'center' }}>
                      <label className="upload-field" style={{ flex: 1 }}>
                        <Upload size={17} />
                        {uploading === 'mask' ? '正在上传…' : p.mask ? '更换蒙版' : '上传蒙版（白色区域重绘）'}
                        <input type="file" accept="image/*" disabled={!!uploading} onChange={e => fileChange('mask', e.target.files[0])} />
                      </label>
                      <Button disabled={!p.input_image} onClick={() => setMaskEditorOpen(true)}>
                        <Brush size={16} />
                        画蒙版
                      </Button>
                    </div>
                    {p.mask && <p className="file-path">蒙版：{p.mask}</p>}
                  </>
                )}
                <NumberField label="降噪强度 Denoise" min={0} max={1} step={0.05} value={p.denoise} onChange={v => set('denoise', v)} />
              </div>
            </Section>
          )}

          {/* H3 Route B Source Video */}
          {videoMode === 'continue' && (
            <Section title="续写源视频（路线 B · 尾段惯性衔接）" data-h3-continue>
              <div className="stack" style={{ gap: '12px' }}>
                <Field label="从图库选择上一段视频">
                  <select
                    aria-label="从图库选择源视频"
                    value={p.source_video_id || ''}
                    onChange={e => {
                      workflowEpoch.current++;
                      const vid = e.target.value;
                      const found = galleryVideos.find(v => v.id === vid);
                      setParams(old => ({
                        ...old,
                        source_video_id: vid,
                        source_video: found ? found.filename || '' : '',
                        ...(found && found.width && found.height && found.width % 32 === 0 && found.height % 32 === 0
                          ? { width: found.width, height: found.height }
                          : {}),
                      }));
                    }}
                  >
                    <option value="">请选择图库视频…</option>
                    {galleryVideos.map(v => (
                      <option key={v.id} value={v.id}>
                        {v.width}×{v.height} · {(v.duration || 0).toFixed(1)}s · {(v.params?.prompt || v.filename || v.id).slice(0, 26)}
                      </option>
                    ))}
                  </select>
                </Field>
                <label className="upload-field">
                  <Upload size={17} />
                  <span>{uploading === 'source_video' ? '正在上传源视频…' : p.source_video ? '更换本地源视频' : '或上传本地上一段视频（mp4 / webm）'}</span>
                  <input type="file" accept="video/mp4,video/webm,video/quicktime" disabled={!!uploading} onChange={e => { uploadSourceVideo(e.target.files[0]); e.target.value = ''; }} />
                </label>
                {p.source_video && <p className="file-path">当前源视频：{p.source_video}</p>}
                <Field label="惯性重叠帧数">
                  <select aria-label="惯性重叠帧数" value={Number(p.continue_frames) || 5} onChange={e => setParams(o => ({ ...o, continue_frames: Number(e.target.value), keyframe_frames: undefined }))}>
                    {continueFrameOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                  </select>
                </Field>
                <p className="muted small">自动取源视频末尾 {Number(p.continue_frames) || 5} 帧衔接本段开头，多段生成后可在图库一键「无痕拼接」。</p>
              </div>
            </Section>
          )}

          {/* 3x3 Nine-Grid Reference / Keyframes */}
          {videoMode === 'ref' && (
            <KeyframeGrid
              mode="ref"
              items={refs}
              uploading={!!uploading}
              onUpload={uploadReferences}
              onMove={(idx, delta) => set('ref_images', moveReference(refs, idx, delta))}
              onRemove={idx => set('ref_images', refs.filter((_, n) => n !== idx))}
              onClear={() => set('ref_images', [])}
              onInsertTag={idx => {
                setParams(old => ({ ...old, prompt: old.prompt + (old.prompt ? ' ' : '') + `<Picture ${idx + 1}>` }));
                setTab('prompt');
              }}
            />
          )}

          {(videoMode === 'keyframes' || videoMode === 'continue') && (
            <KeyframeGrid
              mode={videoMode}
              items={kfs}
              indices={kfIndices}
              totalLength={timing?.length || 124}
              continueFrames={Number(p.continue_frames) || 5}
              customFrames={p.keyframe_frames}
              uploading={!!uploading}
              onUpload={uploadKeyframes}
              onMove={(idx, delta) => setParams(old => ({ ...old, keyframes: moveReference(kfs, idx, delta), keyframe_frames: undefined }))}
              onRemove={idx => setParams(old => ({ ...old, keyframes: kfs.filter((_, n) => n !== idx), keyframe_frames: undefined }))}
              onClear={() => setParams(old => ({ ...old, keyframes: [], keyframe_frames: undefined }))}
              onResetFrames={() => setParams(old => ({ ...old, keyframe_frames: undefined }))}
              onSetFrame={setKeyframeFrame}
            />
          )}

          {/* Prompt Editor (not shown for video_seedvr2 refine) */}
          {!isVideoRefine && (
            <Section
              title={isH3Video ? '视频运镜与音效提示词' : '提示词'}
              action={
                <div className="row" style={{ gap: '4px' }}>
                  {!isH3Video && (
                    <Button className="mini-btn" onClick={() => setLayoutModalOpen(true)} title="MOSS 原生 JSON 像素坐标排版编辑器">
                      <LayoutGrid size={14} />
                      坐标排版
                    </Button>
                  )}
                  <IconButton icon={FileText} label="识别文本正负提示词" onClick={() => setParserOpen(true)} />
                  <IconButton icon={Clock} label="提示词历史" onClick={() => setShowHistory(!showHistory)} />
                </div>
              }
            >
              <div className="tabs">
                <button type="button" className={tab === 'prompt' ? 'active' : ''} onClick={() => setTab('prompt')}>正向提示词</button>
                {!isH3Video && (
                  <button type="button" className={tab === 'negative_prompt' ? 'active' : ''} onClick={() => setTab('negative_prompt')}>负向提示词</button>
                )}
              </div>
              <textarea
                className="prompt"
                aria-label={tab === 'prompt' ? '正向提示词' : '负向提示词'}
                placeholder={isH3Video ? '描述镜头运动、动作过渡；可用换行加 Audio: 描述背景声效…' : tab === 'prompt' ? '描述你想画的内容…' : '不希望出现的内容…'}
                value={p[tab] || ''}
                onChange={e => set(tab, e.target.value)}
              />
              <div className="row between muted small" style={{ marginTop: '6px' }}>
                <span>{isH3Video ? '支持末尾换行写 Audio: 音效描述' : tab === 'prompt' ? 'Prompt' : 'Negative prompt'}</span>
                <span>{(p[tab] || '').length} 字符</span>
              </div>
              {isLayoutPrompt && (
                <div className="moss-layout-banner">
                  <span>
                    <strong>JSON 坐标排版</strong> ·{' '}
                    {parsedLayout?.draft?.summary?.canvas === 'multi_panel'
                      ? '多格漫画'
                      : parsedLayout?.draft?.summary?.canvas === 'collage'
                        ? '视觉拼贴'
                        : '单幅海报'}{' '}
                    · {parsedLayout?.draft?.items?.length || 0} 个区块
                  </span>
                  <div className="row" style={{ gap: '6px' }}>
                    <Button className="mini-btn" onClick={() => setLayoutModalOpen(true)}>
                      可视化编辑
                    </Button>
                    <Button
                      className="mini-btn"
                      onClick={() => {
                        const cap = parsedLayout?.draft?.summary?.caption || '';
                        set('prompt', cap);
                        setNotice('已还原为纯文本全局描述');
                      }}
                    >
                      还原为纯文本
                    </Button>
                  </div>
                </div>
              )}
              {isLayoutPrompt && (parsedLayout?.outOfBounds || Number(p.width) % 16 !== 0 || Number(p.height) % 16 !== 0) && (
                <div className="res-vram-warn" role="status" style={{ marginTop: '6px' }}>
                  <div>
                    当前尺寸 {p.width}×{p.height} 与 JSON 坐标框越界或非 16 倍数，建议同步重算坐标以防切边报错。
                  </div>
                  <Button
                    className="mini-btn"
                    style={{ marginTop: '6px' }}
                    onClick={() => {
                      const w16 = snap16(p.width, 832);
                      const h16 = snap16(p.height, 1248);
                      setParams(old => ({
                        ...old,
                        width: w16,
                        height: h16,
                        prompt: rescaleCompiledMossLayout(old.prompt, w16, h16),
                      }));
                      setNotice(`已自动重算 JSON 坐标适配 ${w16}×${h16}`);
                    }}
                  >
                    自动适配当前尺寸
                  </Button>
                </div>
              )}
              {isLayoutPrompt &&
                parsedLayout?.hasTextItems &&
                /(?:^|,)\s*(?:text|words|letters|typography|speech bubbles?)\s*(?:,|$)/i.test(p.negative_prompt || '') && (
                  <div className="res-vram-warn" role="status" style={{ marginTop: '6px' }}>
                    <div>
                      JSON 排版中包含文字区块，但负向提示词带有 <code>text</code> / <code>speech bubbles</code>，可能拦截出字。
                    </div>
                    <Button
                      className="mini-btn"
                      style={{ marginTop: '6px' }}
                      onClick={() => {
                        set('negative_prompt', stripNegativeTextConflicts(p.negative_prompt));
                        setNotice('已移除负向中的 text / speech bubbles 冲突词');
                      }}
                    >
                      一键清理负向文字冲突
                    </Button>
                  </div>
                )}
              {!isH3Video && tab === 'prompt' && containsMixedPrompt(p.prompt) && (
                <div className="row between small" style={{ marginTop: '6px', padding: '6px 10px', background: 'var(--subtle)', borderRadius: '6px' }}>
                  <span className="muted">检测到文本含正负提示词标记</span>
                  <button
                    type="button"
                    style={{ textDecoration: 'underline', fontWeight: 600 }}
                    onClick={() => {
                      const res = parsePromptText(p.prompt);
                      setParams(o => ({ ...o, prompt: res.prompt, negative_prompt: res.negative_prompt || o.negative_prompt, ...res.params }));
                      setNotice('已一键拆分正负提示词');
                    }}
                  >
                    一键拆分
                  </button>
                </div>
              )}
              {showHistory && (
                <div className="history-list">
                  {history.length ? (
                    history.map((h, i) => (
                      <button type="button" key={i} onClick={() => { set('prompt', h); setTab('prompt'); setShowHistory(false); }}>
                        {h}
                      </button>
                    ))
                  ) : (
                    <p className="muted">生成后会保留最近的提示词。</p>
                  )}
                </div>
              )}
            </Section>
          )}
        </div>

        {/* Right Column: Parameters */}
        <div className="stack">
          {isVideoRefine ? (
            <Section title="SeedVR2 视频精修参数">
              <div className="stack" style={{ gap: '12px' }}>
                <Field label="目标短边分辨率">
                  <select aria-label="精修短边分辨率" value={p.resolution ?? 1080} onChange={e => set('resolution', Number(e.target.value))}>
                    {seedvr2ResolutionOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                  </select>
                </Field>
                <Field label="显存模式（BlockSwap）">
                  <select aria-label="显存模式" value={p.blocks_to_swap ?? 0} onChange={e => setParams(o => ({ ...o, blocks_to_swap: Number(e.target.value), swap_io_components: Number(e.target.value) > 0 }))}>
                    <option value={0}>0 · 全速模式（G4 / A100 / H100 推荐）</option>
                    <option value={16}>16 · 均衡省显存（L4 24G 推荐）</option>
                    <option value={32}>32 · 极限省显存</option>
                  </select>
                </Field>
                <div className="grid-two">
                  <Field label="时序批大小">
                    <select aria-label="时序批大小" value={p.frame_batch_size ?? 5} onChange={e => set('frame_batch_size', Number(e.target.value))}>
                      {seedvr2BatchOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                    </select>
                  </Field>
                  <Field label="批次重叠帧">
                    <select aria-label="批次重叠帧" value={p.temporal_overlap ?? 2} onChange={e => set('temporal_overlap', Number(e.target.value))}>
                      {[0, 1, 2, 4].map(v => <option key={v} value={v}>{v === 0 ? '0 帧（关闭）' : `${v} 帧${v === 2 ? '（推荐）' : ''}`}</option>)}
                    </select>
                  </Field>
                </div>
                <div className="grid-two">
                  <Field label="色彩校正">
                    <select aria-label="色彩校正" value={p.color_correction ?? 'lab'} onChange={e => set('color_correction', e.target.value)}>
                      {seedvr2ColorOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                    </select>
                  </Field>
                  <Field label="导出画质">
                    <select aria-label="导出画质" value={p.video_crf ?? 15} onChange={e => set('video_crf', Number(e.target.value))}>
                      {videoCrfOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                    </select>
                  </Field>
                </div>
                <p className="muted small">提交前会自动释放 GPU 上残留模型显存，并保留原视频帧率与音轨。</p>
              </div>
            </Section>
          ) : (
            <Section
              title={isH3Video ? '视频参数' : '画面与生成参数'}
              action={
                !isH3Video && mossVariant ? (
                  <Button
                    className="mini-btn"
                    onClick={() => {
                      setParams(old => applyMossPresetToParams(old, mossVariant));
                      setNotice(`已恢复 ${MOSS_VARIANT_PRESETS[mossVariant].label} 推荐参数（${MOSS_VARIANT_PRESETS[mossVariant].summary}）`);
                    }}
                  >
                    <RotateCcw size={13} />
                    恢复 {MOSS_VARIANT_PRESETS[mossVariant].label} 推荐参数
                  </Button>
                ) : undefined
              }
            >
              {isH3Video && (
                <>
                  <Field label="视频分辨率">
                    <select
                      aria-label="视频分辨率"
                      value={!customSize && (videoResolutions.find(([, w, h]) => Math.min(p.width, p.height) === h && Math.max(p.width, p.height) === w)?.[0] || 'custom')}
                      onChange={e => {
                        setCustomSize(e.target.value === 'custom');
                        if (e.target.value === 'custom') requestAnimationFrame(() => document.querySelector('[data-video-width] input')?.focus());
                        const row = videoResolutions.find(([name]) => name === e.target.value);
                        if (row) {
                          const [, w, h] = row;
                          setVideoSize(p.width < p.height ? h : w, p.width < p.height ? w : h);
                        }
                      }}
                    >
                      {videoResolutions.map(([name, w, h]) => <option key={name} value={name}>{name} · {w} × {h}</option>)}
                      <option value="custom">自定义尺寸</option>
                    </select>
                  </Field>
                  <p className="muted small" style={{ marginTop: '6px' }}>当前尺寸 {p.width} × {p.height} · 宽高须为 32 的倍数</p>
                </>
              )}

              {isH3Video ? (
                <div className="chips compact" style={{ marginTop: '10px' }}>
                  {[
                    ['横屏', ...videoOrientation(p.width, p.height)],
                    ['竖屏', ...videoOrientation(p.width, p.height, true)],
                    ['1:1', Math.min(p.width, p.height), Math.min(p.width, p.height)],
                  ].map(([n, w, h]) => (
                    <button type="button" key={n} className={p.width === w && p.height === h ? 'selected' : ''} onClick={() => setVideoSize(w, h)}>
                      {n}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="stack" style={{ gap: '8px', marginBottom: '12px' }}>
                  <div className="res-tier-row">
                    <span className="muted small">基础分辨率</span>
                    <div className="chips compact" style={{ margin: 0 }}>
                      {RESOLUTION_TIERS.map(t => (
                        <button
                          type="button"
                          key={t.id}
                          className={sizeMatch.tier === t.id ? 'selected' : ''}
                          title={`${t.label} (${t.hint})`}
                          onClick={() => {
                            const [w, h] = resolveImageSize(t.id, sizeMatch.ratio || inferredSize.ratio || '1:1');
                            setVideoSize(w, h);
                          }}
                        >
                          {t.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="chips compact" style={{ margin: 0 }}>
                    {ASPECT_RATIOS.map(r => {
                      const targetTier = sizeMatch.tier || inferredSize.tier || '1K';
                      const [pw, ph] = resolveImageSize(targetTier, r.id);
                      return (
                        <button
                          type="button"
                          key={r.id}
                          className={sizeMatch.ratio === r.id ? 'selected' : ''}
                          title={`${r.label} (${pw}×${ph})`}
                          onClick={() => setVideoSize(pw, ph)}
                        >
                          {r.label}
                        </button>
                      );
                    })}
                  </div>
                  <p className="muted small" style={{ margin: 0 }}>
                    当前分辨率 {p.width} × {p.height}{sizeMatch.tier && sizeMatch.ratio ? ` · ${sizeMatch.tier} · ${sizeMatch.ratio}` : ' · 自定义尺寸'}
                  </p>
                  {vramRisk && (
                    <p className="res-vram-warn" role="status">
                      {vramRisk} 直出显存占用与耗时极高，若显存不足或超时，建议先出 1K / 2K 底图再用超分或 AI 精修放大。
                    </p>
                  )}
                </div>
              )}

              {isH3Video && (
                <div data-video-controls className="stack" style={{ gap: '10px', marginBottom: '12px' }}>
                  <div className="grid-two">
                    <Field label="视频时长">
                      <select aria-label="视频时长" value={p.duration_seconds ?? 'original'} onChange={e => setDuration(Number(e.target.value))}>
                        {!videoDurations.includes(p.duration_seconds) && <option value={p.duration_seconds ?? 'original'}>原工作流 · {timing.length} 帧</option>}
                        {videoDurations.map(s => <option key={s} value={s}>{s} 秒</option>)}
                      </select>
                    </Field>
                    <Field label="RIFE 补帧">
                      <select
                        aria-label="补帧"
                        value={parseFrameInterp(p.frame_interp) || 'off'}
                        onChange={e => {
                          const v = parseFrameInterp(e.target.value);
                          setParams(o => {
                            const next = { ...o };
                            if (v) { next.frame_interp = v; next.output_fps = v; }
                            else { delete next.frame_interp; delete next.output_fps; }
                            return next;
                          });
                        }}
                      >
                        {frameInterpOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                      </select>
                    </Field>
                  </div>
                  <Field label="导出画质">
                    <select aria-label="导出画质" value={p.video_crf ?? 15} onChange={e => set('video_crf', Number(e.target.value))}>
                      {videoCrfOptions.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                    </select>
                  </Field>
                  <p className="muted small" data-video-timing>
                    {timing.length} 帧 · 24 fps 生成{parseFrameInterp(p.frame_interp) ? ` · 补帧 ${parseFrameInterp(p.frame_interp)} fps` : ''} · 实际 {timing.actual_duration_seconds.toFixed(2)} 秒
                  </p>
                </div>
              )}

              <div className="grid-two">
                <span data-video-width>
                  <NumberField label="宽度" value={p.width} min={isH3Video ? 32 : undefined} max={isH3Video ? 16384 : undefined} step={isH3Video ? 32 : undefined} onChange={v => set('width', v)} />
                </span>
                <NumberField label="高度" value={p.height} min={isH3Video ? 32 : undefined} max={isH3Video ? 16384 : undefined} step={isH3Video ? 32 : undefined} onChange={v => set('height', v)} />
                <NumberField label="采样步数 Steps" value={p.steps} onChange={v => set('steps', v)} />
                {!isH3Video && <NumberField label="引导系数 CFG" value={p.cfg} onChange={v => set('cfg', v)} />}
                {!isH3Video && hasAuraFlowShift && (
                  <NumberField
                    label={mossVariant === 'base' ? '流调度 Shift（Base 建议 4.5~5.5）' : mossVariant === 'sft' ? '流调度 Shift（SFT 建议 3.2~3.8）' : '流调度 Shift'}
                    min={0}
                    max={100}
                    step={0.1}
                    value={p.shift ?? ''}
                    onChange={v => set('shift', v)}
                  />
                )}
                {!isH3Video && hasMappedDenoise && p.mode !== 'img2img' && p.mode !== 'inpaint' && (
                  <NumberField label="二采重绘 Denoise" min={0} max={1} step={0.05} value={p.denoise} onChange={v => set('denoise', v)} />
                )}
                {!isH3Video && <NumberField label="生成张数" value={p.n} min={1} max={16} onChange={v => set('n', v)} />}
                <Field label="Seed（-1 随机）">
                  <div className="input-action">
                    <input type="number" step="1" value={p.seed} onChange={e => set('seed', e.target.value)} />
                    <IconButton icon={Shuffle} label="随机种子" onClick={() => set('seed', -1)} />
                  </div>
                </Field>
                <Field label="采样器 Sampler">
                  <select aria-label="采样器 Sampler" value={p.sampler || 'euler'} onChange={e => set('sampler', e.target.value)}>
                    {samplerChoices.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
                <Field label="调度器 Scheduler">
                  <select aria-label="调度器 Scheduler" value={p.scheduler || 'normal'} onChange={e => set('scheduler', e.target.value)}>
                    {schedulerChoices.map(v => <option key={v} value={v}>{v}</option>)}
                  </select>
                </Field>
              </div>
              {mossWarnings.length > 0 && MOSS_VARIANT_PRESETS[mossVariant] && (
                <div className="res-vram-warn" role="status" style={{ marginTop: '10px' }}>
                  <div>{mossWarnings.join('；')}</div>
                  <Button
                    className="mini-btn"
                    style={{ marginTop: '6px' }}
                    onClick={() => {
                      setParams(old => applyMossPresetToParams(old, mossVariant));
                      setNotice(`已套用 ${MOSS_VARIANT_PRESETS[mossVariant].label} 推荐参数（${MOSS_VARIANT_PRESETS[mossVariant].summary}）`);
                    }}
                  >
                    一键套用 {MOSS_VARIANT_PRESETS[mossVariant].label} 推荐参数
                  </Button>
                </div>
              )}
            </Section>
          )}

          {!isVideoRefine && (!isH3Video || errors.workflows) && (
            <Fold title="高级参数与扩展">
              {!isH3Video && (
                <>
                  <Field label="VAE">
                    <select disabled={frozenGraph} value={p.vae} onChange={e => set('vae', e.target.value)}>
                      <option value="">模型默认 VAE</option>
                      {models.filter(m => m.kind === 'vae').map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                    </select>
                  </Field>
                  <Field label="ControlNet（需工作流映射）">
                    <select value={p.controlnet || ''} onChange={e => set('controlnet', e.target.value)}>
                      <option value="">关闭</option>
                      {models.filter(m => m.kind === 'controlnet').map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                    </select>
                  </Field>
                  <label className="row">
                    <input type="checkbox" checked={p.face_restore} onChange={e => set('face_restore', e.target.checked)} />
                    基础修脸（需工作流支持）
                  </label>
                </>
              )}
              <ErrorBox error={errors.workflows} />
            </Fold>
          )}

          {!isAnyVideo && (
            <Fold title="超分 / 高清修复">
              <Button onClick={() => setUpscaleOpen(true)}>
                <Maximize2 size={16} />
                配置放大选项
              </Button>
              <p className="muted small">
                {p.upscale?.enabled
                  ? `${({ fast: '快速放大', hires: '高清重绘', ai: 'AI 精修' })[p.upscale.mode] || p.upscale.mode} · ${p.upscale.scale}×`
                  : '关闭 · 保留原始尺寸'}
              </p>
            </Fold>
          )}

          <Fold title="参数与 LoRA 组合预设">
            <ErrorBox error={errors.presets} />
            <Field label="载入预设">
              <select
                disabled={!!uploading}
                defaultValue=""
                onChange={e => {
                  const pr = presets.find(v => v.id === e.target.value);
                  if (pr) setParams({ ...defaultParams, ...pr.params, upscale: { ...baseUpscale, ...pr.params?.upscale } });
                  e.target.value = '';
                }}
              >
                <option value="">选择已保存的预设</option>
                {presets.map(pr => <option key={pr.id} value={pr.id}>{pr.name}</option>)}
              </select>
            </Field>
            <Field label="预设名称">
              <input value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="例如：8s 高画质 / 常用竖图" />
            </Field>
            <Button
              disabled={!presetName.trim()}
              onClick={async () => {
                try {
                  await post('/api/presets', { name: presetName, params: p });
                  await refresh(['presets']);
                  setPresetName('');
                  setNotice('预设已保存');
                } catch (e) {
                  setError(e.message);
                }
              }}
            >
              <Save size={16} />
              保存当前参数
            </Button>
          </Fold>

          <Fold title="最近任务 · 最多 3 条" memory="draw-recent">
            <ErrorBox error={errors.tasks} />
            <div className="task-list">
              {validTasks(tasks).slice(0, 3).map(t => {
                const badge = taskTypeBadge(t);
                return (
                  <button
                    className="recent-task"
                    key={t.id}
                    onClick={() => {
                      navigate('tasks');
                      window.dispatchEvent(new CustomEvent('studio:task', { detail: t.id }));
                    }}
                  >
                    <span className="row grow" style={{ gap: '8px', minWidth: 0 }}>
                      <span className={`pill tone-${badge.tone}`}>{badge.label}</span>
                      <span className="truncate">{taskTitle(t, workflows, models)}</span>
                    </span>
                    <TaskStatus task={t} />
                  </button>
                );
              })}
              {!validTasks(tasks).length && <p className="muted small">暂无任务记录。</p>}
            </div>
            <Button onClick={() => navigate('tasks')}>查看全部任务</Button>
          </Fold>
        </div>
      </div>

      {/* Bottom sticky Generate Bar */}
      <div className="generate-bar">
        <ErrorBox error={error} />
        {runtime?.state !== 'ready' && (
          <button type="button" className="gpu-warn-pill" onClick={() => navigate('machine')}>
            <Cpu size={14} />
            <span>GPU {stateNames[runtime?.state] || '未就绪'} · 点此开机</span>
          </button>
        )}
        <Button primary onClick={generate} disabled={busy || !!uploading}>
          <Play size={18} />
          {generateLabel}
        </Button>
      </div>

      {loraOpen && (
        <LoraPicker models={models} value={p.loras} mossVariant={mossVariant} onChange={v => set('loras', v)} onClose={closeLora} prompt={p.prompt} onPrompt={v => set('prompt', v)} />
      )}
      {workflowPickerOpen && (
        <WorkflowPicker
          workflows={workflows}
          models={models}
          selectedId={p.workflow || p.workflow_id || ''}
          initialCategory={scene === 'video' ? 'video' : scene === 'refine' ? 'refine' : 'all'}
          onSelect={loadWorkflowById}
          onClose={closeWorkflowPicker}
        />
      )}
      {modelPickerOpen && (
        <ModelPicker
          models={baseModels}
          selectedId={p.model}
          onSelect={id => {
            const picked = models.find(m => m.id === id);
            const label = String(picked?.filename || picked?.name || id || '');
            const isMoss = /moss_image/i.test(label);
            const isSft = /sft/i.test(label);
            const oldLabel = String(currentModel?.filename || currentModel?.name || p.model || '');
            const wasMoss = /moss_image/i.test(oldLabel);
            const wasSft = /sft/i.test(oldLabel);
            setParams(old => {
              const next = { ...old, model: id };
              if (isMoss) {
                const preset = MOSS_VARIANT_PRESETS[isSft ? 'sft' : 'base'];
                if (!wasMoss || wasSft !== isSft || old.shift === undefined || old.shift === '') {
                  next.shift = preset.shift;
                  next.steps = preset.steps;
                  next.cfg = preset.cfg;
                  next.sampler = preset.sampler;
                  next.scheduler = preset.scheduler;
                }
                const fluxVae = models.find(v => v.kind === 'vae' && /flux2/i.test(String(v.filename || v.name || '')));
                const curVae = models.find(v => v.id === old.vae || v.filename === old.vae || v.name === old.vae);
                if (fluxVae) next.vae = fluxVae.id;
                else if (old.vae && !/flux2/i.test(String(curVae?.filename || old.vae))) next.vae = '';
              } else {
                delete next.shift;
                const curVae = models.find(v => v.id === old.vae || v.filename === old.vae || v.name === old.vae);
                if (old.vae && /flux2/i.test(String(curVae?.filename || curVae?.name || old.vae))) next.vae = '';
              }
              return next;
            });
          }}
          onClose={closeModelPicker}
        />
      )}
      {upscaleOpen && (
        <UpscaleModal value={p.upscale} models={models} workflows={workflows} onClose={closeUpscale} onApply={v => set('upscale', v)} />
      )}
      {parserOpen && (
        <PromptParserModal
          currentPrompt={p.prompt}
          currentNegativePrompt={p.negative_prompt}
          baseParams={p}
          defaultCount={p.n || 1}
          unitLabel={isAnyVideo ? '段' : '张'}
          isVideo={isH3Video}
          hasShift={hasAuraFlowShift}
          mossVariant={mossVariant}
          onClose={closeParser}
          onApply={handleParsedApply}
          onBatchGenerate={handleBatchGenerate}
        />
      )}
      {maskEditorOpen && p.input_image && (
        <MaskEditorModal
          imageUrl={p.input_image.startsWith('http') || p.input_image.startsWith('/') ? p.input_image : `/api/uploads/${p.input_image}`}
          onClose={closeMaskEditor}
          onSave={path => {
            set('mask', path);
            setNotice('蒙版已保存并应用');
          }}
        />
      )}
      {layoutModalOpen && (
        <MossLayoutModal
          width={p.width}
          height={p.height}
          currentPrompt={p.prompt}
          defaultTrigger=""
          onClose={closeLayoutModal}
          onApply={({ prompt: compiledPrompt, width: nextW, height: nextH, hasTextItems }) => {
            setParams(old => {
              const next = { ...old, prompt: compiledPrompt, width: nextW, height: nextH };
              if (hasTextItems && old.negative_prompt) {
                const cleanedNeg = stripNegativeTextConflicts(old.negative_prompt);
                if (cleanedNeg !== old.negative_prompt) next.negative_prompt = cleanedNeg;
              }
              return next;
            });
            setTab('prompt');
            setNotice(`已应用 MOSS 坐标排版 JSON（${nextW}×${nextH}）`);
          }}
        />
      )}
    </>
  );
}
