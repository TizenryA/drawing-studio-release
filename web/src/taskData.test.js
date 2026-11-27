import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isVideoTask,
  taskTypeBadge,
  taskTitle,
  taskSpecLine,
  formatDuration,
  formatCu,
  taskCostDisplay,
  classifyWorkflow,
  workflowQuickSpec,
} from './taskData.js';

test('video vs image task detection and unit wording', () => {
  const h3Task = {
    id: 't1',
    params: {
      workflow_id: 'h3-fl2va-keyframes-8step-int8',
      width: 1024,
      height: 576,
      duration_seconds: 8,
      actual_duration_seconds: 8,
      fps: 24,
      keyframes: ['a.png', 'b.png', 'c.png'],
      video_crf: 15,
      prompt: 'cyberpunk runner unsheathing katana',
    },
  };
  assert.equal(isVideoTask(h3Task), true);
  assert.equal(taskTypeBadge(h3Task).unit, '段');
  assert.equal(taskTypeBadge(h3Task).label, '视频生成');
  assert.ok(taskSpecLine(h3Task).includes('1 段视频'));
  assert.ok(!taskSpecLine(h3Task).includes('张'));
  assert.ok(taskSpecLine(h3Task).includes('3 关键帧'));

  const sv2Task = {
    id: 't2',
    params: {
      mode: 'video_seedvr2',
      workflow_id: 'video-seedvr2',
      source_video: 'clip_8s.mp4',
      resolution: 1080,
      color_correction: 'lab',
      video_crf: 15,
    },
  };
  assert.equal(isVideoTask(sv2Task), true);
  assert.equal(taskTypeBadge(sv2Task).unit, '段');
  assert.equal(taskTypeBadge(sv2Task).label, '视频精修');
  assert.ok(taskSpecLine(sv2Task).includes('1 段视频'));
  assert.ok(taskTitle(sv2Task).includes('SeedVR2 视频精修'));

  const imgTask = {
    id: 't3',
    params: { mode: 'txt2img', width: 1024, height: 1536, steps: 28, n: 2, prompt: 'a portrait' },
  };
  assert.equal(isVideoTask(imgTask), false);
  assert.equal(taskTypeBadge(imgTask).unit, '张');
  assert.ok(taskSpecLine(imgTask).includes('2 张'));
});

test('duration, CU formatting and cost display helpers', () => {
  assert.equal(formatDuration(26.4), '26 秒');
  assert.equal(formatDuration(186), '3分 06秒');
  assert.equal(formatDuration(4320), '1小时 12分');
  assert.equal(formatCu(1952.367), '1952.4 CU');
  assert.equal(formatCu(0.34), '0.34 CU');

  const actualCost = taskCostDisplay({ cu_actual_delta: 0.42, cu_estimated: 0.4 });
  assert.equal(actualCost.isActual, true);
  assert.equal(actualCost.cu, 0.42);

  const estCost = taskCostDisplay({ cu_estimated: 0.35, cu_rate_hourly: 6.3 });
  assert.equal(estCost.isActual, false);
  assert.ok(estCost.label.startsWith('≈ '));
});

test('classifyWorkflow categorizes H3, MOSS, SDXL, and refine cleanly', () => {
  const h3 = classifyWorkflow({
    id: 'h3-fl2va-keyframes-8step-int8',
    workflow: { '1': { class_type: 'MiniMaxH3ImageToVideo', inputs: {} } },
  });
  assert.equal(h3.category, 'video');
  assert.ok(h3.badges.includes('路线A·多关键帧'));
  assert.ok(h3.badges.includes('INT8'));

  const moss = classifyWorkflow({ id: 'moss-civitai-3430983', workflow: {} });
  assert.equal(moss.category, 'moss');
  assert.equal(moss.mossVariant, 'base');
  assert.ok(moss.badges.includes('MOSS Base'));

  const mossBase = classifyWorkflow({
    id: 'moss-base',
    workflow: {
      '1': { class_type: 'UNETLoader', inputs: { unet_name: 'moss_image_base_bf16.safetensors' } },
      '2': { class_type: 'ModelSamplingAuraFlow', inputs: { shift: 5.0 } },
      '8': { class_type: 'EmptyFlux2LatentImage', inputs: { width: 896, height: 1344 } },
      '9': { class_type: 'KSampler', inputs: { steps: 36, cfg: 3.2, sampler_name: 'euler', scheduler: 'sgm_uniform' } },
    },
  });
  assert.equal(mossBase.mossVariant, 'base');
  assert.ok(mossBase.badges.includes('MOSS Base'));
  assert.ok(mossBase.badges.includes('BF16'));
  assert.equal(workflowQuickSpec({
    id: 'moss-base',
    workflow: {
      '1': { class_type: 'UNETLoader', inputs: { unet_name: 'moss_image_base_bf16.safetensors' } },
      '2': { class_type: 'ModelSamplingAuraFlow', inputs: { shift: 5.0 } },
      '8': { class_type: 'EmptyFlux2LatentImage', inputs: { width: 896, height: 1344 } },
      '9': { class_type: 'KSampler', inputs: { steps: 36, cfg: 3.2, sampler_name: 'euler', scheduler: 'sgm_uniform' } },
    },
  }), 'Shift 5 · 36步 · CFG 3.2 · euler/sgm_uniform · 896×1344');

  const mossSft = classifyWorkflow({
    id: 'moss-sft',
    workflow: {
      '1': { class_type: 'UNETLoader', inputs: { unet_name: 'moss_image_sft_int8_convrot.safetensors' } },
      '2': { class_type: 'ModelSamplingAuraFlow', inputs: { shift: 3.5 } },
      '8': { class_type: 'EmptyFlux2LatentImage', inputs: { width: 832, height: 1248 } },
      '9': { class_type: 'KSampler', inputs: { steps: 28, cfg: 3.5, sampler_name: 'er_sde', scheduler: 'simple' } },
    },
  });
  assert.equal(mossSft.mossVariant, 'sft');
  assert.ok(mossSft.badges.includes('MOSS SFT'));
  assert.ok(mossSft.badges.includes('INT8'));

  const sv2 = classifyWorkflow({ id: 'video-seedvr2', mode: 'video_seedvr2', builtin: true });
  assert.equal(sv2.category, 'refine');
});
