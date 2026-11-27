export const MOSS_CANVAS_TYPES = [
  ['single', '单画幅海报 / 插画'],
  ['multi_panel', '多格漫画分镜'],
  ['collage', '拼贴视觉页'],
];

export const MOSS_ITEM_LABELS = [
  ['character', '角色主体'],
  ['object', '物体 / 分格 / 场景'],
  ['text', '文字 / 标题 / 拟声词'],
];

export function snap16(value, fallback = 832) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.max(256, Math.round(n / 16) * 16);
}

function enclosePixelBox(box, width, height, label) {
  if (!Array.isArray(box) || box.length !== 4) {
    throw new Error(`${label} bbox 必须是 [x1, y1, x2, y2]`);
  }
  const nums = box.map(Number);
  if (nums.some(v => !Number.isFinite(v))) {
    throw new Error(`${label} bbox 必须包含 4 个有效数字`);
  }
  const [x1, y1, x2, y2] = nums;
  if (!(x1 < x2 && y1 < y2)) {
    throw new Error(`${label} 宽高必须大于 0`);
  }
  if (x1 < 0 || y1 < 0 || x2 > width || y2 > height) {
    throw new Error(`${label} 越出 ${width}×${height} 画布范围`);
  }
  const halfW = Math.trunc(width / 2);
  const halfH = Math.trunc(height / 2);
  const cy1 = Math.floor(Math.round(y1 * 1e9) / 1e9) - halfH;
  const cx1 = Math.floor(Math.round(x1 * 1e9) / 1e9) - halfW;
  const cy2 = Math.ceil(Math.round(y2 * 1e9) / 1e9) - halfH;
  const cx2 = Math.ceil(Math.round(x2 * 1e9) / 1e9) - halfW;
  if (!(cy1 < cy2 && cx1 < cx2)) {
    throw new Error(`${label} 坐标框过小`);
  }
  return [cy1, cx1, cy2, cx2];
}

/**
 * Compile a top-left `abs` + `xyxy` Layout draft into MOSS-Image's native
 * single-line `abs_centralized` + `yxyx` JSON prompt string.
 */
export function compileMossLayout(draft) {
  if (!draft || typeof draft !== 'object' || Array.isArray(draft)) {
    throw new Error('Layout 草稿必须是 JSON 对象');
  }
  const { width, height, bbox_coord_space, bbox_format, summary, items } = draft;
  for (const [name, val] of [['width', width], ['height', height]]) {
    if (!Number.isInteger(val) || val <= 0 || val % 16 !== 0) {
      throw new Error(`${name} 必须是 16 的正整数倍（当前：${val}）`);
    }
  }
  if (bbox_coord_space !== 'abs') {
    throw new Error('草稿 bbox_coord_space 必须为 "abs"');
  }
  if (bbox_format !== 'xyxy') {
    throw new Error('草稿 bbox_format 必须为 "xyxy"');
  }
  if (!summary || typeof summary !== 'object' || typeof summary.caption !== 'string' || !summary.caption.trim()) {
    throw new Error('summary.caption 不能为空');
  }
  if (summary.canvas !== undefined && !['single', 'multi_panel', 'collage'].includes(summary.canvas)) {
    throw new Error('summary.canvas 必须是 single、multi_panel 或 collage');
  }
  if (!Array.isArray(items) || items.length === 0) {
    throw new Error('items 至少需要 1 个排版区块');
  }

  const outSummary = {
    ...(Array.isArray(summary.meta) && summary.meta.length ? { meta: summary.meta.map(String).filter(Boolean) } : {}),
    ...(summary.canvas ? { canvas: summary.canvas } : {}),
    caption: summary.caption.trim(),
    ...(Array.isArray(summary.palette) && summary.palette.length ? { palette: summary.palette.map(String).filter(Boolean) } : {}),
  };

  const compiledItems = items.map((item, idx) => {
    const loc = `第 ${idx + 1} 个区块`;
    if (!item || typeof item !== 'object') throw new Error(`${loc} 格式无效`);
    if (!['object', 'character', 'text'].includes(item.label)) {
      throw new Error(`${loc} 类别必须是 character、object 或 text`);
    }
    if (typeof item.caption !== 'string' || !item.caption.trim()) {
      throw new Error(`${loc} 的视觉描述 caption 不能为空`);
    }
    if (item.label === 'text' && (typeof item.text !== 'string' || !item.text.trim())) {
      throw new Error(`${loc} 是文字区块，必须填写要渲染的准确文字`);
    }
    const out = {
      label: item.label,
      caption: item.caption.trim(),
      ...(typeof item.text === 'string' && item.text.length ? { text: item.text } : {}),
      ...(Array.isArray(item.tags) && item.tags.length ? { tags: item.tags.map(String).filter(Boolean) } : {}),
      ...(Array.isArray(item.palette) && item.palette.length ? { palette: item.palette.map(String).filter(Boolean) } : {}),
      bbox: enclosePixelBox(item.bbox, width, height, loc),
    };
    return out;
  });

  return JSON.stringify({
    summary: outSummary,
    items: compiledItems,
    bbox_coord_space: 'abs_centralized',
    bbox_format: 'yxyx',
  });
}

function unwrapJsonString(raw) {
  if (!raw || typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return (fenced ? fenced[1] : trimmed).trim();
}

export function isMossLayoutPrompt(text) {
  if (!text || typeof text !== 'string') return false;
  const cleaned = unwrapJsonString(text);
  if (!cleaned.startsWith('{') || !cleaned.endsWith('}')) return false;
  try {
    const parsed = JSON.parse(cleaned);
    return !!(
      parsed &&
      typeof parsed === 'object' &&
      !Array.isArray(parsed) &&
      typeof parsed.summary?.caption === 'string' &&
      parsed.summary.caption.trim() &&
      Array.isArray(parsed.items) &&
      parsed.items.length > 0 &&
      parsed.bbox_coord_space === 'abs_centralized' &&
      parsed.bbox_format === 'yxyx'
    );
  } catch {
    return false;
  }
}

export function parseMossLayoutPrompt(raw, width = 832, height = 1248) {
  const targetW = snap16(width, 832);
  const targetH = snap16(height, 1248);
  const cleaned = unwrapJsonString(raw);
  if (!cleaned.startsWith('{')) {
    return { ok: false, draft: null, outOfBounds: false, hasTextItems: false, error: '内容不是 JSON 对象' };
  }
  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch (e) {
    return { ok: false, draft: null, outOfBounds: false, hasTextItems: false, error: `JSON 解析失败：${e.message}` };
  }
  if (!parsed || typeof parsed !== 'object' || !parsed.summary?.caption || !Array.isArray(parsed.items) || !parsed.items.length) {
    return { ok: false, draft: null, outOfBounds: false, hasTextItems: false, error: '缺少 summary.caption 或 items' };
  }

  let outOfBounds = false;
  let sourceW = targetW;
  let sourceH = targetH;
  let items;

  if (parsed.bbox_coord_space === 'abs_centralized' && parsed.bbox_format === 'yxyx') {
    const halfW = Math.trunc(targetW / 2);
    const halfH = Math.trunc(targetH / 2);
    let maxAbsX = 0;
    let maxAbsY = 0;
    for (const item of parsed.items) {
      if (!Array.isArray(item.bbox) || item.bbox.length !== 4) {
        return { ok: false, draft: null, outOfBounds: false, hasTextItems: false, error: '存在格式错误的 bbox' };
      }
      const [cy1, cx1, cy2, cx2] = item.bbox.map(Number);
      maxAbsX = Math.max(maxAbsX, Math.abs(cx1), Math.abs(cx2));
      maxAbsY = Math.max(maxAbsY, Math.abs(cy1), Math.abs(cy2));
      if (cx1 < -halfW || cx2 > halfW || cy1 < -halfH || cy2 > halfH) {
        outOfBounds = true;
      }
    }
    if (outOfBounds) {
      sourceW = Math.max(targetW, snap16(Math.ceil(maxAbsX * 2)));
      sourceH = Math.max(targetH, snap16(Math.ceil(maxAbsY * 2)));
    }
    const useHalfW = Math.trunc(sourceW / 2);
    const useHalfH = Math.trunc(sourceH / 2);
    items = parsed.items.map(item => {
      const [cy1, cx1, cy2, cx2] = item.bbox.map(Number);
      return {
        label: item.label || 'object',
        caption: String(item.caption || ''),
        ...(typeof item.text === 'string' ? { text: item.text } : {}),
        ...(Array.isArray(item.tags) ? { tags: item.tags } : {}),
        ...(Array.isArray(item.palette) ? { palette: item.palette } : {}),
        bbox: [cx1 + useHalfW, cy1 + useHalfH, cx2 + useHalfW, cy2 + useHalfH],
      };
    });
  } else if (parsed.bbox_coord_space === 'abs' && parsed.bbox_format === 'xyxy') {
    sourceW = snap16(parsed.width, targetW);
    sourceH = snap16(parsed.height, targetH);
    outOfBounds = sourceW !== targetW || sourceH !== targetH;
    items = parsed.items.map(item => {
      const [x1, y1, x2, y2] = (item.bbox || [0, 0, 128, 128]).map(Number);
      if (x1 < 0 || y1 < 0 || x2 > targetW || y2 > targetH) outOfBounds = true;
      return {
        label: item.label || 'object',
        caption: String(item.caption || ''),
        ...(typeof item.text === 'string' ? { text: item.text } : {}),
        ...(Array.isArray(item.tags) ? { tags: item.tags } : {}),
        ...(Array.isArray(item.palette) ? { palette: item.palette } : {}),
        bbox: [x1, y1, x2, y2],
      };
    });
  } else {
    return { ok: false, draft: null, outOfBounds: false, hasTextItems: false, error: '仅支持 abs_centralized/yxyx 或 abs/xyxy 格式' };
  }

  const draft = {
    width: sourceW,
    height: sourceH,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: {
      meta: Array.isArray(parsed.summary.meta) ? parsed.summary.meta : [],
      canvas: ['single', 'multi_panel', 'collage'].includes(parsed.summary.canvas) ? parsed.summary.canvas : 'single',
      caption: String(parsed.summary.caption || ''),
      ...(Array.isArray(parsed.summary.palette) ? { palette: parsed.summary.palette } : {}),
    },
    items,
  };

  return {
    ok: true,
    draft,
    outOfBounds,
    hasTextItems: items.some(it => it.label === 'text'),
    sourceWidth: sourceW,
    sourceHeight: sourceH,
    error: '',
  };
}

export function fitMossLayoutToCanvas(draft, newWidth, newHeight) {
  const targetW = snap16(newWidth, 832);
  const targetH = snap16(newHeight, 1248);
  const oldW = Number(draft?.width) || targetW;
  const oldH = Number(draft?.height) || targetH;
  const sx = targetW / oldW;
  const sy = targetH / oldH;

  const items = (draft.items || []).map(item => {
    const [ox1, oy1, ox2, oy2] = item.bbox || [0, 0, oldW, oldH];
    let x1 = Math.max(0, Math.min(targetW - 16, Math.round(ox1 * sx)));
    let y1 = Math.max(0, Math.min(targetH - 16, Math.round(oy1 * sy)));
    let x2 = Math.max(x1 + 16, Math.min(targetW, Math.round(ox2 * sx)));
    let y2 = Math.max(y1 + 16, Math.min(targetH, Math.round(oy2 * sy)));
    if (x2 - x1 < 16) x1 = Math.max(0, x2 - 16);
    if (y2 - y1 < 16) y1 = Math.max(0, y2 - 16);
    return { ...item, bbox: [x1, y1, x2, y2] };
  });

  return {
    ...draft,
    width: targetW,
    height: targetH,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    items,
  };
}

export function rescaleCompiledMossLayout(promptText, targetWidth, targetHeight) {
  const parsed = parseMossLayoutPrompt(promptText, targetWidth, targetHeight);
  if (!parsed.ok || !parsed.draft) return promptText;
  const fitted = fitMossLayoutToCanvas(parsed.draft, targetWidth, targetHeight);
  return compileMossLayout(fitted);
}

export function injectTriggerIntoMossLayout(promptText, trigger) {
  const cleanTrigger = String(trigger || '').trim();
  if (!cleanTrigger) return promptText || '';
  if (!isMossLayoutPrompt(promptText)) {
    const cur = String(promptText || '');
    if (cur.includes(cleanTrigger)) return cur;
    return `${cur}${cur ? ', ' : ''}${cleanTrigger}`;
  }
  const parsed = JSON.parse(unwrapJsonString(promptText));
  const cap = String(parsed.summary?.caption || '').trim();
  if (cap.toLowerCase().includes(cleanTrigger.toLowerCase())) return promptText;
  parsed.summary.caption = `${cleanTrigger}, ${cap}`;
  return JSON.stringify(parsed);
}

export function stripNegativeTextConflicts(negativePrompt) {
  if (!negativePrompt || typeof negativePrompt !== 'string') return '';
  const conflictTokens = new Set(['text', 'words', 'letters', 'typography', 'speech bubble', 'speech bubbles', 'dialogue']);
  return negativePrompt
    .split(',')
    .map(part => part.trim())
    .filter(part => part && !conflictTokens.has(part.toLowerCase()))
    .join(', ');
}

export const MOSS_LAYOUT_PRESETS = [
  {
    id: 'poster-guochao',
    label: '国潮书法海报',
    canvas: 'single',
    meta: ['poster', 'editorial', 'guochao'],
    caption: 'A dramatic Chinese dark-fantasy key visual poster in lush ink-and-gouache illustration, glowing vermilion maple leaves drifting across warm obsidian shadows, bold layered depth typography.',
    items: [
      {
        label: 'text',
        caption: 'Massive vertical Chinese brush calligraphy headline in rich vermilion ink layered cleanly behind the heroine shoulders for editorial depth, crisp strokes.',
        text: '山海\n绘卷',
        box: [0.06, 0.05, 0.42, 0.44],
      },
      {
        label: 'character',
        caption: 'Full three-quarter hero portrait of a striking Chinese swordswoman in flowing crimson-and-black embroidered silk robes, chin turned over shoulder, razor-sharp eyes, loose strands caught in rim light, hair partially overlapping the large calligraphy behind her.',
        box: [0.16, 0.14, 0.90, 0.92],
      },
      {
        label: 'text',
        caption: 'Sleek cream serif vertical or stacked bilingual sub-headline in the upper-right negative space, crisp spacing.',
        text: 'SHANHAI SCROLL\nEPISODE I',
        box: [0.56, 0.06, 0.94, 0.17],
      },
      {
        label: 'text',
        caption: 'Small carved vermilion square seal stamp beside crisp gold issue caption near bottom-right corner.',
        text: '绘卷印',
        box: [0.76, 0.78, 0.92, 0.87],
      },
      {
        label: 'text',
        caption: 'Thin minimalist light-cream editorial billing bar centered along the very bottom margin.',
        text: 'SPECIAL EDITION · STUDIO ARCHIVE · 2026',
        box: [0.08, 0.91, 0.92, 0.97],
      },
    ],
  },
  {
    id: 'poster-movie',
    label: '电影主视觉海报',
    canvas: 'single',
    meta: ['movie poster', 'cinematic'],
    caption: 'A theatrical widescreen-inspired vertical cinema key visual poster with dramatic chiaroscuro rim lighting, volumetric mist, rich painterly texture, and razor-sharp film billing typography.',
    items: [
      {
        label: 'text',
        caption: 'Monumental metallic gold-and-ivory serif movie title centered across the upper sky negative space.',
        text: 'NIGHT PATROL\n夜巡者',
        box: [0.08, 0.05, 0.92, 0.21],
      },
      {
        label: 'character',
        caption: 'Dramatic low-angle medium hero silhouette standing amidst swirling mist and glowing embers, crisp rim light carving hair and silhouette edges.',
        box: [0.12, 0.18, 0.88, 0.86],
      },
      {
        label: 'text',
        caption: 'Subtle crisp uppercase film tagline tucked cleanly between chest light and bottom credits.',
        text: 'EVERY THREAD HAS A WITNESS',
        box: [0.14, 0.82, 0.86, 0.88],
      },
      {
        label: 'text',
        caption: 'Compact condensed studio credit billing block centered along bottom edge.',
        text: 'DIRECTED IN MOSS IMAGE · IMAX SPECIAL',
        box: [0.10, 0.90, 0.90, 0.97],
      },
    ],
  },
  {
    id: 'poster-magazine',
    label: '潮流杂志封面',
    canvas: 'single',
    meta: ['magazine cover', 'editorial'],
    caption: 'High-contrast modern art magazine cover editorial photography-meets-illustration aesthetic on deep charcoal satin background, sharp layered masthead typography behind hair.',
    items: [
      {
        label: 'text',
        caption: 'Ultra-bold condensed crimson sans-serif magazine masthead spanning full top width, partially occluded behind the character hair.',
        text: 'CRIMSON',
        box: [0.05, 0.04, 0.95, 0.20],
      },
      {
        label: 'character',
        caption: 'Confident fashion-editorial upper-body hero pose looking sharply into lens under crisp studio rim lighting, hair crown overlapping the masthead letterforms.',
        box: [0.16, 0.13, 0.88, 0.94],
      },
      {
        label: 'text',
        caption: 'Clean cream feature headline callout stacked on the left middle margin.',
        text: 'ISSUE 09\n光影重塑',
        box: [0.05, 0.44, 0.34, 0.59],
      },
      {
        label: 'text',
        caption: 'Crisp compact vertical issue number tag in upper right corner.',
        text: 'VOL. 36\n特刊',
        box: [0.74, 0.23, 0.94, 0.34],
      },
    ],
  },
  {
    id: 'comic-popout-3',
    label: '破框爆裂三格',
    canvas: 'multi_panel',
    meta: ['comic', 'manga'],
    caption: 'A dynamic vertical 3-panel graphic novel page with crisp black panel borders, bright white gutters, dramatic pop-out frame bleed, expressive ink lineart and rich gouache color.',
    items: [
      {
        label: 'character',
        caption: 'Top hero panel occupying upper 58 percent with diagonal dynamic perspective: heroine lunging forward with flowing crimson ribbons and weapon edge aggressively breaking past the black bottom panel border into the white gutter.',
        box: [0.04, 0.04, 0.96, 0.59],
      },
      {
        label: 'text',
        caption: 'Sharp tilted hand-lettered white-outlined crimson comic action sound effect bursting across the pop-out border.',
        text: '铿——！',
        box: [0.58, 0.47, 0.93, 0.61],
      },
      {
        label: 'character',
        caption: 'Bottom-left inset panel with bold tilted black frame and deep crimson backdrop: tight face close-up capturing sharp focused eyes and fluttering hair strands.',
        box: [0.04, 0.63, 0.48, 0.96],
      },
      {
        label: 'object',
        caption: 'Bottom-right detail panel: crisp extreme close-up of fingers tightly gripping sword hilt or fluttering silk sleeve dusted with glowing sparks.',
        box: [0.52, 0.63, 0.96, 0.96],
      },
    ],
  },
  {
    id: 'comic-tension-4',
    label: '左通栏四格张力',
    canvas: 'multi_panel',
    meta: ['comic', 'manga'],
    caption: 'Structured 4-panel illustrated comic page layout with crisp black panel borders, white gutters, deliberate asymmetric size hierarchy, rich ink-and-watercolor shading.',
    items: [
      {
        label: 'character',
        caption: 'Left tall hero panel occupying full height and 54 percent width: atmospheric three-quarter shot anchoring the character posture, weight and surroundings cleanly.',
        box: [0.04, 0.04, 0.56, 0.96],
      },
      {
        label: 'text',
        caption: 'Small clean white rounded speech bubble tucked inside the upper corner of the left hero panel with tail pointing toward the character.',
        text: '找到了。',
        box: [0.07, 0.08, 0.30, 0.17],
      },
      {
        label: 'object',
        caption: 'Top-right narrow detail panel: extreme close-up of key prop, lantern glow or emblem in crisp lighting.',
        box: [0.60, 0.04, 0.96, 0.32],
      },
      {
        label: 'object',
        caption: 'Middle-right detail panel: crisp close-up of gripping hands or fabric tension detail.',
        box: [0.60, 0.36, 0.96, 0.64],
      },
      {
        label: 'character',
        caption: 'Bottom-right reaction panel: tight eye-level facial close-up reacting with sharp side-eye gaze toward left.',
        box: [0.60, 0.68, 0.96, 0.96],
      },
    ],
  },
  {
    id: 'comic-dual-contrast',
    label: '撞色双格封面',
    canvas: 'multi_panel',
    meta: ['comic', 'graphic novel'],
    caption: 'Bold two-panel high-contrast graphic novel page contrasting a moody detailed top illustration with a flat vermilion-and-black color-blocked bottom close-up panel.',
    items: [
      {
        label: 'character',
        caption: 'Top 62 percent main panel with crisp black ink border: atmospheric medium full shot in cool lantern shadow and dramatic rim light.',
        box: [0.04, 0.04, 0.96, 0.62],
      },
      {
        label: 'object',
        caption: 'Bottom graphic color-blocked panel set against solid pure vermilion background with bold stark ink eye close-up and high contrast shadows.',
        box: [0.04, 0.66, 0.96, 0.96],
      },
      {
        label: 'text',
        caption: 'Bold slanted cream comic caption banner anchored cleanly inside top-right corner of the bottom crimson panel.',
        text: '夜色已深——',
        box: [0.56, 0.69, 0.92, 0.81],
      },
    ],
  },
];

export function buildMossLayoutPreset(presetId, width = 832, height = 1248, opts = {}) {
  const preset = MOSS_LAYOUT_PRESETS.find(p => p.id === presetId) || MOSS_LAYOUT_PRESETS[0];
  const W = snap16(width, 832);
  const H = snap16(height, 1248);
  const trigger = String(opts.triggerPrefix || '').trim();
  const baseCaption = preset.caption;
  const caption = trigger && !baseCaption.toLowerCase().includes(trigger.toLowerCase())
    ? `${trigger}, ${baseCaption}`
    : baseCaption;

  return {
    width: W,
    height: H,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: {
      meta: [...preset.meta],
      canvas: preset.canvas,
      caption,
    },
    items: preset.items.map(it => {
      const [rx1, ry1, rx2, ry2] = it.box;
      const x1 = Math.max(0, Math.min(W - 16, Math.round(rx1 * W)));
      const y1 = Math.max(0, Math.min(H - 16, Math.round(ry1 * H)));
      const x2 = Math.max(x1 + 16, Math.min(W, Math.round(rx2 * W)));
      const y2 = Math.max(y1 + 16, Math.min(H, Math.round(ry2 * H)));
      return {
        label: it.label,
        caption: it.caption,
        ...(it.text ? { text: it.text } : {}),
        bbox: [x1, y1, x2, y2],
      };
    }),
  };
}
