export const BATCH_PARAM_KEYS = [
  'width',
  'height',
  'steps',
  'cfg',
  'shift',
  'seed',
  'sampler',
  'scheduler',
  'denoise',
];

export const SAMPLER_OPTIONS = [
  'euler',
  'er_sde',
  'res_multistep',
  'euler_ancestral',
  'heun',
  'dpmpp_2s_ancestral',
  'dpmpp_2m',
  'dpmpp_2m_sde',
  'dpmpp_3m_sde',
  'uni_pc',
  'ddim',
];

export const SCHEDULER_OPTIONS = [
  'simple',
  'normal',
  'karras',
  'exponential',
  'sgm_uniform',
  'beta',
  'ddim_uniform',
  'linear_quadratic',
  'kl_optimal',
];

export const RESOLUTION_TIERS = [
  { id: '1K', label: '1K', hint: '~1024px' },
  { id: '2K', label: '2K', hint: '~2048px' },
  { id: '4K', label: '4K', hint: '~4096px' },
  { id: '8K', label: '8K', hint: '~8192px' },
];

export const ASPECT_RATIOS = [
  { id: '1:1', label: '1:1 方图', short: '1:1', ratio: 1 },
  { id: '2:3', label: '2:3 竖图', short: '2:3', ratio: 2 / 3 },
  { id: '3:2', label: '3:2 横图', short: '3:2', ratio: 3 / 2 },
  { id: '3:4', label: '3:4 竖屏', short: '3:4', ratio: 3 / 4 },
  { id: '4:3', label: '4:3 横屏', short: '4:3', ratio: 4 / 3 },
  { id: '16:9', label: '16:9 宽屏', short: '16:9', ratio: 16 / 9 },
  { id: '9:16', label: '9:16 竖屏', short: '9:16', ratio: 9 / 16 },
];

export const RESOLUTION_TABLE = {
  '1K': {
    '1:1': [1024, 1024],
    '2:3': [832, 1248],
    '3:2': [1248, 832],
    '3:4': [864, 1152],
    '4:3': [1152, 864],
    '16:9': [1280, 720],
    '9:16': [720, 1280],
  },
  '2K': {
    '1:1': [2048, 2048],
    '2:3': [1664, 2496],
    '3:2': [2496, 1664],
    '3:4': [1728, 2304],
    '4:3': [2304, 1728],
    '16:9': [2560, 1440],
    '9:16': [1440, 2560],
  },
  '4K': {
    '1:1': [4096, 4096],
    '2:3': [2688, 4032],
    '3:2': [4032, 2688],
    '3:4': [3072, 4096],
    '4:3': [4096, 3072],
    '16:9': [3840, 2160],
    '9:16': [2160, 3840],
  },
  '8K': {
    '1:1': [8192, 8192],
    '2:3': [5376, 8064],
    '3:2': [8064, 5376],
    '3:4': [6144, 8192],
    '4:3': [8192, 6144],
    '16:9': [7680, 4320],
    '9:16': [4320, 7680],
  },
};

export const IMAGE_SIZE_PRESETS = ASPECT_RATIOS.map(r => [r.label, ...RESOLUTION_TABLE['1K'][r.id]]);

export function resolveImageSize(tier = '1K', ratio = '1:1') {
  return RESOLUTION_TABLE[tier]?.[ratio] || RESOLUTION_TABLE['1K'][ratio] || RESOLUTION_TABLE['1K']['1:1'];
}

export function detectImageTierAndRatio(width, height) {
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
    return { tier: null, ratio: null };
  }
  for (const tier of RESOLUTION_TIERS) {
    const map = RESOLUTION_TABLE[tier.id];
    for (const r of ASPECT_RATIOS) {
      const [tw, th] = map[r.id];
      if (tw === w && th === h) return { tier: tier.id, ratio: r.id };
    }
  }
  return { tier: null, ratio: null };
}

export function inferTierAndRatio(width, height) {
  const exact = detectImageTierAndRatio(width, height);
  if (exact.tier && exact.ratio) return exact;
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
    return { tier: '1K', ratio: '1:1' };
  }
  const maxDim = Math.max(w, h);
  const tier = maxDim <= 1600 ? '1K' : maxDim <= 3072 ? '2K' : maxDim <= 5600 ? '4K' : '8K';
  const curRatio = w / h;
  let bestRatio = '1:1';
  let bestDiff = Infinity;
  for (const r of ASPECT_RATIOS) {
    const diff = Math.abs(curRatio - r.ratio);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestRatio = r.id;
    }
  }
  return { tier, ratio: bestRatio };
}

export function highResVramLevel(width, height) {
  const exact = detectImageTierAndRatio(width, height);
  if (exact.tier === '4K' || exact.tier === '8K') return exact.tier;
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return '';
  const maxDim = Math.max(w, h);
  const pixels = w * h;
  if (maxDim >= 6000 || pixels >= 24_000_000) return '8K';
  if (maxDim >= 3500 || pixels >= 8_000_000) return '4K';
  return '';
}

export function workflowHasShift(workflow) {
  if (!workflow) return false;
  if (workflow.mapping && Object.prototype.hasOwnProperty.call(workflow.mapping, 'shift')) {
    return true;
  }
  return Object.values(workflow.workflow || {}).some(
    node => node?.class_type === 'ModelSamplingAuraFlow',
  );
}

export const MOSS_VARIANT_PRESETS = {
  base: {
    variant: 'base',
    label: 'MOSS Base',
    shortLabel: 'Base · 厚涂',
    width: 896,
    height: 1344,
    steps: 36,
    cfg: 3.2,
    shift: 5.0,
    sampler: 'euler',
    scheduler: 'sgm_uniform',
    summary: 'Shift 5.0 · 36步 · CFG 3.2 · euler/sgm_uniform · 896×1344',
  },
  sft: {
    variant: 'sft',
    label: 'MOSS SFT',
    shortLabel: 'SFT · 稳结构',
    width: 832,
    height: 1248,
    steps: 28,
    cfg: 3.5,
    shift: 3.5,
    sampler: 'er_sde',
    scheduler: 'simple',
    summary: 'Shift 3.5 · 28步 · CFG 3.5 · er_sde/simple · 832×1248',
  },
};

export function detectMossVariant(workflow, params = {}, models = []) {
  if (workflow) {
    const id = String(workflow.id || '').toLowerCase();
    const graphText = JSON.stringify(workflow.workflow || {}).toLowerCase();
    if (graphText.includes('moss_image_sft') || (id.includes('moss') && id.includes('sft'))) {
      return 'sft';
    }
    if (
      graphText.includes('moss_image_base') ||
      id === 'moss-base' ||
      id.startsWith('moss-') ||
      id.includes('krea2-moss')
    ) {
      return 'base';
    }
    return null;
  }
  const ref = params?.model;
  if (!ref) return null;
  const matched = (models || []).find(m => [m.id, m.filename, m.name, m.comfy_name].includes(ref));
  const label = String(matched?.filename || matched?.name || matched?.comfy_name || ref || '');
  if (!/moss_image/i.test(label)) return null;
  return /sft/i.test(label) ? 'sft' : 'base';
}

export function getMossParamWarnings(variant, params = {}) {
  if (variant !== 'base' && variant !== 'sft') return [];
  const num = v => (v === '' || v === null || v === undefined || typeof v === 'boolean' ? null : Number(v));
  const shift = num(params?.shift);
  const steps = num(params?.steps);
  const cfg = num(params?.cfg);
  const width = num(params?.width);
  const height = num(params?.height);
  const warnings = [];
  if (variant === 'base') {
    if (shift !== null && Number.isFinite(shift) && shift < 4.3) {
      warnings.push(`当前为 MOSS Base，Shift ${shift} 偏低（建议 4.5~5.5，默认 5.0），过低易导致肢体畸形`);
    }
    if (steps !== null && Number.isFinite(steps) && steps < 30) {
      warnings.push(`MOSS Base 采样步数 ${steps} 偏少（建议 32~45 步），骨架易未收敛`);
    }
    if (cfg !== null && Number.isFinite(cfg) && cfg < 2.9) {
      warnings.push(`MOSS Base CFG ${cfg} 偏低（建议 3.0~3.5），结构约束较弱`);
    }
    if (
      width !== null &&
      height !== null &&
      Number.isFinite(width) &&
      Number.isFinite(height) &&
      width > 0 &&
      height > 0 &&
      width * height > 2_200_000
    ) {
      warnings.push(`MOSS Base 直出 ${width}×${height} 像素偏高，易触发肢体复制/比例变形，建议 1K~1.5K（如 896×1344）出图后再用 SeedVR2 超分`);
    }
  } else if (variant === 'sft') {
    if (shift !== null && Number.isFinite(shift) && shift > 4.3) {
      warnings.push(`当前为 MOSS SFT，Shift ${shift} 偏高（建议 3.2~3.8，默认 3.5），过高易导致画面僵硬`);
    }
    if (shift !== null && Number.isFinite(shift) && shift < 2.5) {
      warnings.push(`当前为 MOSS SFT，Shift ${shift} 过低（建议 3.2~3.8）`);
    }
  }
  return warnings;
}

export function applyMossPresetToParams(currentParams = {}, variant) {
  const preset = MOSS_VARIANT_PRESETS[variant];
  if (!preset) return currentParams;
  const keepDimensions = ['inpaint', 'img2img'].includes(currentParams?.mode);
  return {
    ...currentParams,
    steps: preset.steps,
    cfg: preset.cfg,
    shift: preset.shift,
    sampler: preset.sampler,
    scheduler: preset.scheduler,
    ...(keepDimensions ? {} : { width: preset.width, height: preset.height }),
  };
}

const hasOwn = (obj, key) => !!obj && Object.prototype.hasOwnProperty.call(obj, key) && obj[key] !== undefined;

export function clampBatchCount(value, fallback = 1) {
  const raw = value === '' || value === null || value === undefined ? fallback : value;
  const num = Number(raw);
  const fb = Number(fallback);
  const base = Number.isFinite(num) ? Math.round(num) : (Number.isFinite(fb) ? Math.round(fb) : 1);
  return Math.max(1, Math.min(16, base));
}

export function sanitizeParamValue(key, raw) {
  if (raw === '' || raw === null || raw === undefined) return undefined;
  if (key === 'sampler' || key === 'scheduler') {
    const str = String(raw).trim();
    return str || undefined;
  }
  const num = Number(raw);
  if (!Number.isFinite(num)) return undefined;
  if (key === 'width' || key === 'height' || key === 'steps') {
    return num > 0 ? Math.round(num) : undefined;
  }
  if (key === 'seed') {
    return Math.trunc(num);
  }
  if (key === 'shift') {
    return num >= 0 && num <= 100 ? num : undefined;
  }
  if (key === 'cfg' || key === 'denoise') {
    return num >= 0 ? num : undefined;
  }
  return undefined;
}

export function resolveBatchItemParams(
  baseParams = {},
  parsedParams = {},
  globalOverrides = {},
  itemOverride = {},
  { isVideo = false, hasShift = undefined } = {},
) {
  const display = {};
  const effective = {};
  const overriddenKeys = [];
  const parsedKeys = [];

  for (const key of BATCH_PARAM_KEYS) {
    if (isVideo && (key === 'cfg' || key === 'denoise' || key === 'shift')) continue;

    const itemHas = hasOwn(itemOverride, key);
    const globalHas = hasOwn(globalOverrides, key);
    const parsedVal = sanitizeParamValue(key, parsedParams?.[key]);
    const baseVal = sanitizeParamValue(key, baseParams?.[key]);

    if (key === 'shift') {
      if (hasShift === false) continue;
      if (hasShift === undefined && !itemHas && !globalHas && parsedVal === undefined && baseVal === undefined) {
        continue;
      }
    }

    if (itemHas) {
      display[key] = itemOverride[key];
    } else if (globalHas) {
      display[key] = globalOverrides[key];
    } else if (parsedVal !== undefined) {
      display[key] = parsedVal;
    } else if (baseVal !== undefined) {
      display[key] = baseVal;
    } else {
      display[key] = '';
    }

    if (itemHas || globalHas) overriddenKeys.push(key);
    if (parsedVal !== undefined) parsedKeys.push(key);

    const candidates = [
      itemHas ? sanitizeParamValue(key, itemOverride[key]) : undefined,
      globalHas ? sanitizeParamValue(key, globalOverrides[key]) : undefined,
      parsedVal,
      baseVal,
    ];

    if (key === 'denoise') {
      const hasDenoiseTouch =
        sanitizeParamValue(key, itemOverride?.[key]) !== undefined ||
        sanitizeParamValue(key, globalOverrides?.[key]) !== undefined ||
        parsedVal !== undefined;
      if (!hasDenoiseTouch) continue;
    }

    const picked = candidates.find(v => v !== undefined);
    if (picked !== undefined) {
      effective[key] = picked;
    }
  }

  return { display, effective, overriddenKeys, parsedKeys };
}

export function applyParamEdit({
  scope,
  index,
  key,
  value,
  globalOverrides = {},
  itemOverrides = {},
}) {
  if (scope === 'all') {
    const nextGlobal = { ...globalOverrides, [key]: value };
    const nextItemOverrides = {};
    for (const [k, entry] of Object.entries(itemOverrides || {})) {
      if (!entry || typeof entry !== 'object') continue;
      const rest = { ...entry };
      delete rest[key];
      if (Object.keys(rest).length > 0) {
        nextItemOverrides[k] = rest;
      }
    }
    return { globalOverrides: nextGlobal, itemOverrides: nextItemOverrides };
  }

  return {
    globalOverrides,
    itemOverrides: {
      ...itemOverrides,
      [index]: {
        ...(itemOverrides?.[index] || {}),
        [key]: value,
      },
    },
  };
}

export function applySizePresetEdit({
  scope,
  index,
  width,
  height,
  globalOverrides = {},
  itemOverrides = {},
}) {
  const step1 = applyParamEdit({
    scope,
    index,
    key: 'width',
    value: width,
    globalOverrides,
    itemOverrides,
  });
  return applyParamEdit({
    scope,
    index,
    key: 'height',
    value: height,
    globalOverrides: step1.globalOverrides,
    itemOverrides: step1.itemOverrides,
  });
}

export function hasDifferingParsedValues(
  items = [],
  baseParams = {},
  keys = ['width', 'height', 'steps', 'cfg', 'shift'],
) {
  if (!Array.isArray(items) || items.length <= 1) return false;
  for (const key of keys) {
    const seen = new Set();
    for (const item of items) {
      const parsedVal = sanitizeParamValue(key, item?.params?.[key]);
      const baseVal = sanitizeParamValue(key, baseParams?.[key]);
      const val = parsedVal !== undefined ? parsedVal : baseVal;
      seen.add(val === undefined ? '__unset__' : String(val));
      if (seen.size > 1) return true;
    }
  }
  return false;
}

export function buildBatchSubmissions(
  items = [],
  {
    edits = {},
    baseParams = {},
    globalOverrides = {},
    itemOverrides = {},
    countPerItem = 1,
    isVideo = false,
    hasShift = undefined,
  } = {},
) {
  const safeCount = clampBatchCount(countPerItem, baseParams?.n || 1);
  return (items || []).map((it, idx) => {
    const resolved = resolveBatchItemParams(
      baseParams,
      it?.params || {},
      globalOverrides,
      itemOverrides?.[idx] || {},
      { isVideo, hasShift },
    );
    return {
      title: it?.title,
      prompt: edits?.[idx]?.prompt ?? it?.prompt ?? '',
      negative_prompt: edits?.[idx]?.negative_prompt ?? it?.negative_prompt ?? '',
      params: resolved.effective,
      loras: it?.loras || [],
      n: safeCount,
    };
  });
}
