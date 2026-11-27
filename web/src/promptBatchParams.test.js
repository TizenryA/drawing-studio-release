import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  BATCH_PARAM_KEYS,
  SAMPLER_OPTIONS,
  SCHEDULER_OPTIONS,
  RESOLUTION_TIERS,
  ASPECT_RATIOS,
  RESOLUTION_TABLE,
  resolveImageSize,
  detectImageTierAndRatio,
  inferTierAndRatio,
  highResVramLevel,
  workflowHasShift,
  clampBatchCount,
  sanitizeParamValue,
  resolveBatchItemParams,
  applyParamEdit,
  applySizePresetEdit,
  hasDifferingParsedValues,
  buildBatchSubmissions,
} from './promptBatchParams.js';
import { parseMultiPrompts } from './promptParser.js';

const baseParams = {
  width: 1024,
  height: 1536,
  steps: 28,
  cfg: 5.5,
  seed: -1,
  sampler: 'euler',
  scheduler: 'normal',
  n: 1,
  denoise: 0.7,
};

test('resolveBatchItemParams follows precedence itemOverride > globalOverrides > parsedParams > baseParams', () => {
  const parsed = { width: 896, height: 1280, steps: 32, cfg: 3.2, sampler: 'dpmpp_2m' };
  const globalOverrides = { cfg: 4.0, steps: 24 };
  const itemOverride = { steps: 40, seed: 42 };

  const res = resolveBatchItemParams(baseParams, parsed, globalOverrides, itemOverride);

  assert.equal(res.display.width, 896);
  assert.equal(res.display.height, 1280);
  assert.equal(res.display.cfg, 4.0);
  assert.equal(res.display.steps, 40);
  assert.equal(res.display.seed, 42);
  assert.equal(res.display.sampler, 'dpmpp_2m');
  assert.equal(res.display.scheduler, 'normal');

  assert.deepEqual(res.effective, {
    width: 896,
    height: 1280,
    steps: 40,
    cfg: 4.0,
    seed: 42,
    sampler: 'dpmpp_2m',
    scheduler: 'normal',
  });
  assert.ok(!('denoise' in res.effective), 'denoise should not be emitted unless parsed or overridden');
  assert.deepEqual(res.overriddenKeys.sort(), ['cfg', 'seed', 'steps']);
  assert.deepEqual(res.parsedKeys.sort(), ['cfg', 'height', 'sampler', 'steps', 'width']);
});

test('scope all updates cfg & steps across all items while keeping distinct parsed dimensions and clearing stale item overrides', () => {
  const items = [
    { title: '图 1', prompt: 'p1', negative_prompt: 'n1', params: { width: 896, height: 1280, steps: 32, cfg: 3.2 } },
    { title: '图 2', prompt: 'p2', negative_prompt: 'n2', params: { width: 896, height: 1344, steps: 30, cfg: 4.5 } },
  ];

  // Item 0 had a per-item CFG tweak first, then user switches to scope=all and edits CFG & Steps
  let state = applyParamEdit({
    scope: 'current',
    index: 0,
    key: 'cfg',
    value: 6.0,
    globalOverrides: {},
    itemOverrides: {},
  });
  assert.equal(state.itemOverrides[0].cfg, 6.0);

  state = applyParamEdit({
    scope: 'all',
    index: 0,
    key: 'cfg',
    value: 3.5,
    ...state,
  });
  assert.equal(state.globalOverrides.cfg, 3.5);
  assert.equal(state.itemOverrides[0], undefined, 'global edit clears conflicting per-item override and prunes empty map');

  state = applyParamEdit({
    scope: 'all',
    index: 1,
    key: 'steps',
    value: 20,
    ...state,
  });

  const subs = buildBatchSubmissions(items, {
    baseParams,
    globalOverrides: state.globalOverrides,
    itemOverrides: state.itemOverrides,
    countPerItem: 2,
  });

  assert.equal(subs.length, 2);
  assert.equal(subs[0].params.width, 896);
  assert.equal(subs[0].params.height, 1280);
  assert.equal(subs[0].params.cfg, 3.5);
  assert.equal(subs[0].params.steps, 20);
  assert.equal(subs[0].n, 2);

  assert.equal(subs[1].params.width, 896);
  assert.equal(subs[1].params.height, 1344);
  assert.equal(subs[1].params.cfg, 3.5);
  assert.equal(subs[1].params.steps, 20);
  assert.equal(subs[1].n, 2);
});

test('scope current updates only the active item and leaves siblings untouched', () => {
  const items = [
    { title: '图 1', prompt: 'p1', negative_prompt: '', params: { steps: 32, cfg: 3.2 } },
    { title: '图 2', prompt: 'p2', negative_prompt: '', params: { steps: 32, cfg: 3.2 } },
  ];

  const state = applyParamEdit({
    scope: 'current',
    index: 1,
    key: 'cfg',
    value: 7.5,
    globalOverrides: {},
    itemOverrides: {},
  });

  const subs = buildBatchSubmissions(items, {
    baseParams,
    globalOverrides: state.globalOverrides,
    itemOverrides: state.itemOverrides,
    countPerItem: 1,
  });

  assert.equal(subs[0].params.cfg, 3.2);
  assert.equal(subs[1].params.cfg, 7.5);
});

test('empty string mid-typing stays in display while effective falls back safely without NaN', () => {
  const parsed = { steps: 32, cfg: 3.2 };
  const res = resolveBatchItemParams(baseParams, parsed, { cfg: 4.5 }, { cfg: '', steps: '' });

  assert.equal(res.display.cfg, '');
  assert.equal(res.display.steps, '');
  assert.equal(res.effective.cfg, 4.5);
  assert.equal(res.effective.steps, 32);
  for (const k of BATCH_PARAM_KEYS) {
    if (k in res.effective) {
      assert.notEqual(res.effective[k], '');
      if (typeof res.effective[k] === 'number') {
        assert.ok(Number.isFinite(res.effective[k]));
      }
    }
  }
});

test('seed -1/0, cfg 0, video mode omitting cfg/denoise, denoise touch, and count clamping work cleanly', () => {
  assert.equal(sanitizeParamValue('seed', -1), -1);
  assert.equal(sanitizeParamValue('seed', 0), 0);
  assert.equal(sanitizeParamValue('cfg', 0), 0);
  assert.equal(sanitizeParamValue('width', 0), undefined);
  assert.equal(sanitizeParamValue('steps', -5), undefined);

  assert.equal(clampBatchCount('', 3), 3);
  assert.equal(clampBatchCount(0, 1), 1);
  assert.equal(clampBatchCount(99, 1), 16);
  assert.equal(clampBatchCount(2.6, 1), 3);

  const videoRes = resolveBatchItemParams(
    baseParams,
    { width: 1024, height: 576, steps: 8, cfg: 3.5, denoise: 0.4 },
    { cfg: 6, denoise: 0.5 },
    {},
    { isVideo: true },
  );
  assert.ok(!('cfg' in videoRes.effective));
  assert.ok(!('denoise' in videoRes.effective));
  assert.equal(videoRes.effective.steps, 8);

  const imgWithDenoise = resolveBatchItemParams(
    baseParams,
    {},
    { denoise: 0.45 },
    { seed: 0, cfg: 0 },
  );
  assert.equal(imgWithDenoise.effective.denoise, 0.45);
  assert.equal(imgWithDenoise.effective.seed, 0);
  assert.equal(imgWithDenoise.effective.cfg, 0);
});

test('applySizePresetEdit and hasDifferingParsedValues work as expected', () => {
  const same = [
    { params: { width: 896, height: 1280, steps: 32, cfg: 3.2 } },
    { params: { width: 896, height: 1280, steps: 32, cfg: 3.2 } },
  ];
  assert.equal(hasDifferingParsedValues(same, baseParams), false);

  const diff = [
    { params: { width: 896, height: 1280, steps: 32, cfg: 3.2 } },
    { params: { width: 896, height: 1344, steps: 32, cfg: 3.2 } },
  ];
  assert.equal(hasDifferingParsedValues(diff, baseParams), true);

  const presetState = applySizePresetEdit({
    scope: 'all',
    index: 0,
    width: 1536,
    height: 1024,
    globalOverrides: {},
    itemOverrides: { 0: { width: 512, cfg: 4 } },
  });
  assert.equal(presetState.globalOverrides.width, 1536);
  assert.equal(presetState.globalOverrides.height, 1024);
  assert.deepEqual(presetState.itemOverrides, { 0: { cfg: 4 } });
});

test('ui.jsx regression guard: Modal keeps stable effect deps and Field does not wrap in <label className="field">', () => {
  const uiSrc = fs.readFileSync(new URL('./ui.jsx', import.meta.url), 'utf8');
  assert.ok(!uiSrc.includes('<label className="field">'), 'Field must not render <label className="field">');
  assert.match(uiSrc, /onCloseRef\s*=\s*useRef\(onClose\)/, 'Modal must track onClose via ref');
  assert.match(uiSrc, /useEffect\(\(\)\s*=>\s*\{[\s\S]*?\},\s*\[\]\s*\)/, 'Modal focus/keyboard effect must only run on mount/unmount ([])');
});

test('shift param resolution, sampler options, and parser extraction for shift & er_sde work', () => {
  assert.ok(SAMPLER_OPTIONS.includes('er_sde'));
  assert.ok(SAMPLER_OPTIONS.includes('res_multistep'));
  assert.ok(SCHEDULER_OPTIONS.includes('linear_quadratic'));

  assert.equal(sanitizeParamValue('shift', 3.5), 3.5);
  assert.equal(sanitizeParamValue('shift', -1), undefined);
  assert.equal(sanitizeParamValue('shift', 101), undefined);

  const mossRes = resolveBatchItemParams(
    { ...baseParams, shift: 3.5 },
    { shift: 4.5, sampler: 'er_sde' },
    { shift: 5.0 },
    {},
    { hasShift: true },
  );
  assert.equal(mossRes.display.shift, 5.0);
  assert.equal(mossRes.effective.shift, 5.0);
  assert.equal(mossRes.effective.sampler, 'er_sde');

  const noShiftRes = resolveBatchItemParams(baseParams, { shift: 4.5 }, {}, {}, { hasShift: false });
  assert.ok(!('shift' in noShiftRes.effective));

  const parsed = parseMultiPrompts('1girl, shift: 3.8, sampler: er_sde, scheduler: simple, steps: 28, cfg: 3.5');
  assert.equal(parsed[0].params.shift, 3.8);
  assert.equal(parsed[0].params.sampler, 'er_sde');
  assert.equal(parsed[0].params.scheduler, 'simple');
});

test('1K/2K/4K/8K resolution tiers, 7 aspect ratios, detection, and workflowHasShift work accurately', () => {
  assert.deepEqual(RESOLUTION_TIERS.map(t => t.id), ['1K', '2K', '4K', '8K']);
  assert.deepEqual(ASPECT_RATIOS.map(r => r.id), ['1:1', '2:3', '3:2', '3:4', '4:3', '16:9', '9:16']);

  for (const tier of RESOLUTION_TIERS) {
    for (const ratio of ASPECT_RATIOS) {
      const [w, h] = resolveImageSize(tier.id, ratio.id);
      assert.equal(w % 16, 0, `${tier.id} ${ratio.id} width ${w} must be multiple of 16`);
      assert.equal(h % 16, 0, `${tier.id} ${ratio.id} height ${h} must be multiple of 16`);
      assert.deepEqual(detectImageTierAndRatio(w, h), { tier: tier.id, ratio: ratio.id });
      assert.deepEqual(RESOLUTION_TABLE[tier.id][ratio.id], [w, h]);
    }
  }

  assert.deepEqual(resolveImageSize('4K', '16:9'), [3840, 2160]);
  assert.deepEqual(resolveImageSize('8K', '16:9'), [7680, 4320]);
  assert.deepEqual(detectImageTierAndRatio(896, 1280), { tier: null, ratio: null });
  assert.deepEqual(inferTierAndRatio(896, 1280), { tier: '1K', ratio: '2:3' });
  assert.equal(highResVramLevel(2048, 2048), '');
  assert.equal(highResVramLevel(3840, 2160), '4K');
  assert.equal(highResVramLevel(7680, 4320), '8K');

  assert.equal(workflowHasShift({ mapping: { shift: '2.shift' }, workflow: {} }), true);
  assert.equal(workflowHasShift({ workflow: { '2': { class_type: 'ModelSamplingAuraFlow', inputs: { shift: 5 } } } }), true);
  assert.equal(workflowHasShift({ workflow: { '2': { class_type: 'KSampler', inputs: {} } } }), false);
});

import {
  MOSS_VARIANT_PRESETS,
  detectMossVariant,
  getMossParamWarnings,
  applyMossPresetToParams,
} from './promptBatchParams.js';

test('MOSS Base and SFT variant detection, guard warnings, and preset application stay isolated', () => {
  assert.equal(MOSS_VARIANT_PRESETS.base.shift, 5.0);
  assert.equal(MOSS_VARIANT_PRESETS.base.steps, 36);
  assert.equal(MOSS_VARIANT_PRESETS.base.cfg, 3.2);
  assert.equal(MOSS_VARIANT_PRESETS.base.sampler, 'euler');
  assert.equal(MOSS_VARIANT_PRESETS.base.scheduler, 'sgm_uniform');
  assert.equal(MOSS_VARIANT_PRESETS.sft.shift, 3.5);
  assert.equal(MOSS_VARIANT_PRESETS.sft.steps, 28);
  assert.equal(MOSS_VARIANT_PRESETS.sft.sampler, 'er_sde');

  assert.equal(detectMossVariant({ id: 'moss-base', workflow: { '1': { inputs: { unet_name: 'moss_image_base_bf16.safetensors' } } } }), 'base');
  assert.equal(detectMossVariant({ id: 'moss-sft', workflow: { '1': { inputs: { unet_name: 'moss_image_sft_int8_convrot.safetensors' } } } }), 'sft');
  assert.equal(detectMossVariant({ id: 'sdxl-txt2img', workflow: {} }), null);

  const models = [
    { id: 'm-base', filename: 'moss_image_base_bf16.safetensors' },
    { id: 'm-sft', filename: 'moss_image_sft_int8_convrot.safetensors' },
    { id: 'm-sdxl', filename: 'wai_illustrious_sdxl_v17.safetensors' },
  ];
  assert.equal(detectMossVariant(null, { model: 'm-base' }, models), 'base');
  assert.equal(detectMossVariant(null, { model: 'm-sft' }, models), 'sft');
  assert.equal(detectMossVariant(null, { model: 'm-sdxl' }, models), null);

  const baseBad = getMossParamWarnings('base', { shift: 3.5, steps: 24, cfg: 2.5, width: 1664, height: 2496 });
  assert.equal(baseBad.length, 4);
  assert.deepEqual(getMossParamWarnings('base', MOSS_VARIANT_PRESETS.base), []);

  const sftHigh = getMossParamWarnings('sft', { shift: 5.0, steps: 28, cfg: 3.5, width: 1664, height: 2496 });
  assert.equal(sftHigh.length, 1);
  assert.deepEqual(getMossParamWarnings('sft', MOSS_VARIANT_PRESETS.sft), []);

  const appliedBase = applyMossPresetToParams(
    {
      mode: 'api',
      prompt: 'my prompt',
      negative_prompt: 'my neg',
      shift: 3.5,
      steps: 24,
      cfg: 2.5,
      width: 1440,
      height: 2560,
      loras: [{ name: 'demo_style_moss.safetensors', strength: 0.85 }],
    },
    'base',
  );
  assert.equal(appliedBase.prompt, 'my prompt');
  assert.equal(appliedBase.negative_prompt, 'my neg');
  assert.equal(appliedBase.shift, 5.0);
  assert.equal(appliedBase.steps, 36);
  assert.equal(appliedBase.cfg, 3.2);
  assert.equal(appliedBase.sampler, 'euler');
  assert.equal(appliedBase.scheduler, 'sgm_uniform');
  assert.equal(appliedBase.width, 896);
  assert.equal(appliedBase.height, 1344);
  assert.equal(appliedBase.loras[0].strength, 0.85);

  const appliedInpaint = applyMossPresetToParams(
    { mode: 'inpaint', width: 1024, height: 1024, shift: 3.5, prompt: 'fix' },
    'base',
  );
  assert.equal(appliedInpaint.width, 1024);
  assert.equal(appliedInpaint.height, 1024);
  assert.equal(appliedInpaint.shift, 5.0);
});
