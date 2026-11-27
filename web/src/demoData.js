import demoWorkflows from './demoWorkflows.json' with { type: 'json' };

const PALETTES = {
  twilight: { bg1: '#14182b', bg2: '#2d2159', accent: '#f59e0b', sub: '#38bdf8', ink: '#f8fafc' },
  courtyard: { bg1: '#1b262c', bg2: '#2c3e50', accent: '#fb7185', sub: '#fda4af', ink: '#f8fafc' },
  emerald: { bg1: '#064e3b', bg2: '#0f766e', accent: '#a7f3d0', sub: '#fde68a', ink: '#ecfdf5' },
  amber: { bg1: '#2b1911', bg2: '#5c2d16', accent: '#fbbf24', sub: '#fb923c', ink: '#fff7ed' },
  arctic: { bg1: '#0f172a', bg2: '#1e3a8a', accent: '#60a5fa', sub: '#a5b4fc', ink: '#f1f5f9' },
  editorial: { bg1: '#18181b', bg2: '#3f3f46', accent: '#e4e4e7', sub: '#38bdf8', ink: '#fafafa' },
};

function escXml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function makeDemoArtworkSvg({
  title = 'Drawing Studio',
  subtitle = 'Preview Artwork',
  badge = 'DEMO',
  width = 896,
  height = 1280,
  palette = 'twilight',
  style = 'poster',
} = {}) {
  const pal = PALETTES[palette] || PALETTES.twilight;
  const w = Math.max(320, Number(width) || 896);
  const h = Math.max(240, Number(height) || 1280);
  const pad = Math.round(Math.min(w, h) * 0.06);

  let artwork = '';
  if (style === 'comic') {
    const gw = w - pad * 2;
    const gh = h - pad * 2 - 90;
    const gap = Math.round(pad * 0.4);
    const pw = Math.round((gw - gap) / 2);
    const ph1 = Math.round(gh * 0.44);
    const ph2 = gh - ph1 - gap;
    artwork = `
      <rect x="${pad}" y="${pad}" width="${pw}" height="${ph1}" rx="12" fill="#ffffff12" stroke="${pal.accent}" stroke-width="2.5"/>
      <rect x="${pad + pw + gap}" y="${pad}" width="${pw}" height="${ph1}" rx="12" fill="#ffffff0d" stroke="${pal.sub}" stroke-width="2.5"/>
      <rect x="${pad}" y="${pad + ph1 + gap}" width="${gw}" height="${ph2}" rx="14" fill="#ffffff14" stroke="${pal.ink}" stroke-width="2.5"/>
      <circle cx="${pad + pw * 0.55}" cy="${pad + ph1 * 0.42}" r="${Math.round(pw * 0.19)}" fill="${pal.accent}" opacity="0.72"/>
      <path d="M${pad + 20} ${pad + ph1 - 28} Q ${pad + pw * 0.5} ${pad + ph1 * 0.56} ${pad + pw - 20} ${pad + ph1 - 28}" fill="none" stroke="${pal.ink}" stroke-width="4" opacity="0.65"/>
      <circle cx="${pad + pw + gap + pw * 0.65}" cy="${pad + ph1 * 0.35}" r="${Math.round(pw * 0.14)}" fill="${pal.sub}" opacity="0.8"/>
      <rect x="${pad + pw + gap + 28}" y="${pad + ph1 * 0.58}" width="${Math.round(pw * 0.52)}" height="12" rx="6" fill="${pal.ink}" opacity="0.5"/>
      <ellipse cx="${pad + gw * 0.5}" cy="${pad + ph1 + gap + ph2 * 0.5}" rx="${Math.round(gw * 0.28)}" ry="${Math.round(ph2 * 0.24)}" fill="${pal.accent}" opacity="0.28"/>
      <rect x="${pad + 32}" y="${pad + ph1 + gap + 24}" width="170" height="36" rx="18" fill="${pal.ink}" opacity="0.88"/>
      <text x="${pad + 52}" y="${pad + ph1 + gap + 47}" fill="${pal.bg1}" font-size="18" font-weight="700" font-family="sans-serif">MOSS 4-PANEL</text>
    `;
  } else if (style === 'courtyard') {
    artwork = `
      <circle cx="${Math.round(w * 0.72)}" cy="${Math.round(h * 0.24)}" r="${Math.round(Math.min(w, h) * 0.16)}" fill="${pal.accent}" opacity="0.82"/>
      <path d="M0 ${Math.round(h * 0.68)} Q ${Math.round(w * 0.3)} ${Math.round(h * 0.55)} ${w} ${Math.round(h * 0.66)} L${w} ${h} L0 ${h} Z" fill="#090d16" opacity="0.72"/>
      <path d="M${Math.round(w * 0.14)} ${Math.round(h * 0.62)} L${Math.round(w * 0.38)} ${Math.round(h * 0.44)} L${Math.round(w * 0.62)} ${Math.round(h * 0.62)} Z" fill="${pal.sub}" opacity="0.28"/>
      <circle cx="${Math.round(w * 0.26)}" cy="${Math.round(h * 0.42)}" r="16" fill="${pal.accent}"/>
      <circle cx="${Math.round(w * 0.52)}" cy="${Math.round(h * 0.46)}" r="12" fill="${pal.sub}"/>
    `;
  } else if (style === 'video') {
    const cx = Math.round(w * 0.5);
    const cy = Math.round(h * 0.46);
    artwork = `
      <rect x="${pad}" y="${pad}" width="${w - pad * 2}" height="${h - pad * 2 - 70}" rx="18" fill="#ffffff0a" stroke="${pal.sub}" stroke-width="2" stroke-dasharray="10 8"/>
      <circle cx="${cx}" cy="${cy}" r="58" fill="${pal.accent}" opacity="0.22"/>
      <circle cx="${cx}" cy="${cy}" r="42" fill="${pal.ink}" opacity="0.9"/>
      <polygon points="${cx - 12},${cy - 18} ${cx + 22},${cy} ${cx - 12},${cy + 18}" fill="${pal.bg1}"/>
    `;
  } else {
    artwork = `
      <circle cx="${Math.round(w * 0.68)}" cy="${Math.round(h * 0.28)}" r="${Math.round(Math.min(w, h) * 0.24)}" fill="${pal.accent}" opacity="0.24"/>
      <circle cx="${Math.round(w * 0.35)}" cy="${Math.round(h * 0.48)}" r="${Math.round(Math.min(w, h) * 0.18)}" fill="${pal.sub}" opacity="0.22"/>
      <path d="M${pad} ${Math.round(h * 0.72)} C ${Math.round(w * 0.35)} ${Math.round(h * 0.58)}, ${Math.round(w * 0.65)} ${Math.round(h * 0.8)}, ${w - pad} ${Math.round(h * 0.64)}" fill="none" stroke="${pal.accent}" stroke-width="5" opacity="0.78"/>
      <path d="M${pad} ${Math.round(h * 0.77)} C ${Math.round(w * 0.4)} ${Math.round(h * 0.65)}, ${Math.round(w * 0.68)} ${Math.round(h * 0.85)}, ${w - pad} ${Math.round(h * 0.69)}" fill="none" stroke="${pal.sub}" stroke-width="3" opacity="0.6"/>
    `;
  }

  const titleY = h - pad - 42;
  const subY = h - pad - 12;
  const titleSize = Math.max(22, Math.round(w * 0.036));
  const subSize = Math.max(15, Math.round(w * 0.022));

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="${pal.bg1}"/>
      <stop offset="100%" stop-color="${pal.bg2}"/>
    </linearGradient>
  </defs>
  <rect width="${w}" height="${h}" fill="url(#bg)"/>
  ${artwork}
  <rect x="${pad}" y="${pad}" width="${Math.max(110, badge.length * 14 + 28)}" height="32" rx="16" fill="#00000055" stroke="#ffffff33"/>
  <text x="${pad + 14}" y="${pad + 21}" fill="${pal.ink}" font-size="14" font-weight="700" font-family="system-ui,sans-serif">${escXml(badge)}</text>
  <rect x="0" y="${h - pad - 88}" width="${w}" height="${pad + 88}" fill="#00000066"/>
  <text x="${pad}" y="${titleY}" fill="${pal.ink}" font-size="${titleSize}" font-weight="700" font-family="system-ui,sans-serif">${escXml(title)}</text>
  <text x="${pad}" y="${subY}" fill="${pal.sub}" font-size="${subSize}" font-family="system-ui,sans-serif">${escXml(subtitle)}</text>
</svg>`;

  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const nowIso = (offsetMinutes = 0) => new Date(Date.now() - offsetMinutes * 60000).toISOString();

function makeGalleryItem({
  id,
  filename,
  project,
  media_type = 'image',
  width = 896,
  height = 1280,
  duration = null,
  favorite = false,
  parent_id = null,
  tags = [],
  params = {},
  svgOpts = {},
  media_url = null,
  offsetMinutes = 15,
}) {
  const thumb = makeDemoArtworkSvg({ width, height, ...svgOpts });
  return {
    id,
    filename,
    project,
    album: project,
    media_type,
    width,
    height,
    duration,
    favorite,
    parent_id,
    tags,
    archive_status: 'saved',
    local_cached: true,
    created_at: nowIso(offsetMinutes),
    url: media_url || thumb,
    preview_url: thumb,
    thumbnail_url: thumb,
    ...(media_url ? { media_url } : {}),
    params: { ...params, project },
  };
}

export function createInitialDemoState(baseUrl = '/') {
  const cleanBase = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;

  const workflows = structuredClone(demoWorkflows);

  const models = [
    {
      id: 'moss-image-sft-int8',
      name: 'MOSS Image SFT (INT8)',
      filename: 'moss_image_sft_int8_convrot.safetensors',
      kind: 'diffusion_models',
      family: 'MOSS Image',
      size: 9850000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: true,
      tags: ['MOSS', 'SFT', 'INT8'],
      description: 'OpenMOSS 轻量量化生图底模，搭配 er_sde / shift 3.5 出图速度与构图兼顾。',
      defaults: { sampler: 'er_sde', scheduler: 'simple', cfg: 3.5, shift: 3.5, steps: 28 },
    },
    {
      id: 'moss-image-base-bf16',
      name: 'MOSS Image Base (BF16)',
      filename: 'moss_image_base_bf16.safetensors',
      kind: 'diffusion_models',
      family: 'MOSS Image',
      size: 18400000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: true,
      tags: ['MOSS', 'Base', 'BF16'],
      description: 'OpenMOSS 原生 BF16 全精度底模，推荐配合 euler + shift 5.0 追求精细光影。',
      defaults: { sampler: 'euler', scheduler: 'simple', cfg: 3.2, shift: 5.0, steps: 36 },
    },
    {
      id: 'krea2-turbo-fp8',
      name: 'Krea2 Turbo (FP8)',
      filename: 'krea2_turbo_fp8.safetensors',
      kind: 'diffusion_models',
      family: 'Flux / Krea',
      size: 11900000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: false,
      tags: ['Krea2', 'Turbo', 'Dual-Sample'],
      description: '用于 Krea2 + MOSS 双采工作流的极速构图阶段 UNET。',
    },
    {
      id: 'minimax-h3-fl2va-int8',
      name: 'MiniMax H3 FL2VA (INT8)',
      filename: 'minimax_h3_fl2va_pruned_int8_convrot.safetensors',
      kind: 'diffusion_models',
      family: 'MiniMax H3',
      size: 14200000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: true,
      tags: ['MiniMax-H3', 'I2V', 'Video'],
      description: '支持首尾/多关键帧时间轴与路线 B 尾段惯性续写的 H3 音视频生成模型。',
    },
    {
      id: 'minimax-h3-ref2va-int8',
      name: 'MiniMax H3 Ref2VA (INT8)',
      filename: 'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
      kind: 'diffusion_models',
      family: 'MiniMax H3',
      size: 14100000000,
      status: 'drive',
      installed_in_comfy: false,
      favorite: false,
      tags: ['MiniMax-H3', 'Ref2V', 'Video'],
      description: '支持多参考图主体一致性的 MiniMax H3 参考生成底模（存于 Google Drive，一键同步）。',
    },
    {
      id: 'animayume-v05',
      name: 'Animayume v0.5',
      filename: 'animayume_v05.safetensors',
      kind: 'diffusion_models',
      family: 'Anima',
      size: 6450000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: false,
      tags: ['Anima', 'Anime'],
      description: '高细腻二次元插画模型，搭配 SeedVR2 精修出图效果极佳。',
    },
    {
      id: 'wai-illustrious-v17',
      name: 'WAI Illustrious SDXL v17',
      filename: 'wai_illustrious_sdxl_v17.safetensors',
      kind: 'checkpoint',
      family: 'Illustrious / SDXL',
      size: 6930000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: true,
      tags: ['SDXL', 'Illustrious', 'Anime'],
      description: '经典 Illustrious 生态全能 Checkpoint，兼容多 LoRA 与 Latent 高清重绘。',
    },
    {
      id: 'lora-h3-fl2v-turbo',
      name: 'MiniMax H3 FL2V 8-Step Turbo',
      filename: 'minimax_h3_fl2v_turbo_8step_v1.2_768p_comfyui_bf16.safetensors',
      kind: 'lora',
      family: 'MiniMax H3',
      size: 468000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: true,
      recommended_weight: 1.0,
      tags: ['H3', 'Turbo', '8-Step'],
      notes: 'H3 8 步加速 LoRA，8 步即可完成高质量 768p 音视频生成。',
    },
    {
      id: 'lora-moss-cgi',
      name: 'Oblivion CGI Style (MOSS)',
      filename: 'moss_oblivion_cgi_v1.safetensors',
      kind: 'lora',
      family: 'MOSS',
      size: 295000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: false,
      recommended_weight: 0.85,
      trigger_words: ['oblivion cgi style', 'cinematic volumetric glow'],
      tags: ['MOSS', 'Civitai', 'CGI'],
      notes: '来自 Civitai 公开工作流 `moss-civitai-3415950` 的 CG 光影风格 LoRA。',
    },
    {
      id: 'lora-moss-blender3d',
      name: 'Blender 3D Stylize V2 (MOSS)',
      filename: 'moss_blender_3d_v2.safetensors',
      kind: 'lora',
      family: 'MOSS',
      size: 312000000,
      status: 'drive',
      installed_in_comfy: false,
      favorite: false,
      recommended_weight: 0.8,
      trigger_words: ['blender 3d style', 'clean clay shading'],
      tags: ['MOSS', 'Civitai', '3D'],
      notes: 'Civitai 3D 质感增强 LoRA，点击同步即可自动从 Drive 拉取至 Colab。',
    },
    {
      id: 'lora-anima-hsr-cg',
      name: 'Anima HSR CG Style',
      filename: 'anima_hsr_cg.safetensors',
      kind: 'lora',
      family: 'Anima',
      size: 224000000,
      status: 'ready',
      installed_in_comfy: true,
      favorite: false,
      recommended_weight: 0.75,
      trigger_words: ['hsr cg illustration', 'luminous rim light'],
      tags: ['Anima', 'Civitai', 'Illustration'],
    },
    {
      id: 'qwen35-9b-int8',
      name: 'Qwen 3.5 9B Text Encoder (INT8)',
      filename: 'qwen3.5_9b_int8_convrot.safetensors',
      kind: 'text_encoders',
      family: 'MOSS Image',
      size: 9200000000,
      status: 'ready',
      installed_in_comfy: true,
      tags: ['TextEncoder', 'MOSS'],
    },
    {
      id: 'flux2-vae',
      name: 'Flux2 VAE',
      filename: 'flux2-vae.safetensors',
      kind: 'vae',
      family: 'MOSS Image',
      size: 335000000,
      status: 'ready',
      installed_in_comfy: true,
      tags: ['VAE', 'MOSS'],
    },
    {
      id: 'seedvr2-ema-3b-fp8',
      name: 'SeedVR2 DiT 3B (FP8)',
      filename: 'seedvr2_ema_3b_fp8_e4m3fn.safetensors',
      kind: 'seedvr2',
      family: 'SeedVR2',
      size: 3380000000,
      status: 'ready',
      installed_in_comfy: true,
      tags: ['SeedVR2', 'Upscale'],
    },
    {
      id: 'rife49',
      name: 'RIFE 4.9 VFI Frame Interp',
      filename: 'rife49.pth',
      kind: 'vfi_models',
      family: 'Frame Interpolation',
      size: 21500000,
      status: 'ready',
      installed_in_comfy: true,
      tags: ['RIFE', '48fps', '60fps'],
    },
  ];

  const gallery = [
    makeGalleryItem({
      id: 'demo-gal-1',
      filename: 'moss_sft_courtyard_001.png',
      project: '角色设定',
      width: 896,
      height: 1280,
      favorite: true,
      tags: ['MOSS-SFT', '角色设定', '庭院'],
      svgOpts: { title: '林间庭院 · 日落斜照', subtitle: 'MOSS SFT · 896×1280 · er_sde · shift 3.5', badge: 'MOSS SFT', palette: 'courtyard', style: 'courtyard' },
      params: {
        workflow_id: 'moss-sft',
        workflow: 'moss-sft',
        mode: 'custom',
        model: 'moss-image-sft-int8',
        prompt: '1girl, ink wash anime editorial illustration, lantern courtyard at twilight, warm rim light, crisp lineart',
        negative_prompt: 'lowres, blurry, bad anatomy',
        width: 896,
        height: 1280,
        steps: 28,
        cfg: 3.5,
        shift: 3.5,
        sampler: 'er_sde',
        scheduler: 'simple',
        seed: 2026112701,
      },
      offsetMinutes: 18,
    }),
    makeGalleryItem({
      id: 'demo-gal-2',
      filename: 'moss_comic_4panel_002.png',
      project: '分镜漫画',
      width: 1024,
      height: 1536,
      favorite: true,
      tags: ['MOSS排版', '四格漫画', '分镜'],
      svgOpts: { title: '机房巡逻四格漫 · 坐标编译', subtitle: 'MOSS Visual Layout · 1024×1536 · steps 36', badge: 'MOSS LAYOUT', palette: 'twilight', style: 'comic' },
      params: {
        workflow_id: 'moss-base',
        workflow: 'moss-base',
        mode: 'custom',
        model: 'moss-image-base-bf16',
        prompt: '<layout><box yxyx="20,30,430,480">catgirl hacker inspecting glowing server rack</box><box yxyx="20,520,430,970">close-up smiling with terminal log reflection</box><box yxyx="470,30,960,970">wide shot rooftop night skyline silhouette</box></layout>',
        negative_prompt: 'text distortion, messy panel border',
        width: 1024,
        height: 1536,
        steps: 36,
        cfg: 3.2,
        shift: 5.0,
        sampler: 'euler',
        scheduler: 'simple',
        seed: 88421903,
      },
      offsetMinutes: 35,
    }),
    makeGalleryItem({
      id: 'demo-gal-3',
      filename: 'h3_keyframe_clip_003.mp4',
      project: '视频镜头',
      media_type: 'video',
      width: 720,
      height: 400,
      duration: 3.0,
      favorite: true,
      media_url: `${cleanBase}demo/h3-sample.mp4`,
      tags: ['MiniMax-H3', '路线A', '8步Turbo'],
      svgOpts: { title: 'MiniMax H3 路线 A 演示片段', subtitle: '可点击播放 MP4 样片 · 支持尾段续写(路线B) / 抽尾帧', badge: 'H3 VIDEO · MP4', palette: 'arctic', style: 'video' },
      params: {
        workflow_id: 'h3-fl2va-keyframes-8step-int8',
        workflow: 'h3-fl2va-keyframes-8step-int8',
        mode: 'custom',
        model: 'minimax-h3-fl2va-int8',
        prompt: 'camera slowly orbits lantern courtyard at night, floating embers, ambient wind and distant chimes',
        width: 1280,
        height: 736,
        duration_seconds: 5,
        fps: 24,
        length: 124,
        steps: 8,
        video_crf: 15,
        keyframe_frames: [0, 123],
        sampler: 'euler',
        scheduler: 'simple',
        seed: 5510942,
      },
      offsetMinutes: 52,
    }),
    makeGalleryItem({
      id: 'demo-gal-4-hd',
      filename: 'krea2_moss_editorial_seedvr2_004.png',
      project: '海报排版',
      width: 1792,
      height: 2560,
      parent_id: 'demo-gal-4-src',
      favorite: false,
      tags: ['Krea2+MOSS', 'SeedVR2', '2K精修'],
      svgOpts: { title: '星穹站台海报 · SeedVR2 精修', subtitle: '由原图 demo-gal-4-src 无损放大 · 可点底部「原图对比」', badge: 'SEEDVR2 2K', palette: 'emerald', style: 'poster' },
      params: {
        workflow_id: 'seedvr2',
        workflow: 'seedvr2',
        mode: 'seedvr2',
        model: 'seedvr2-ema-3b-fp8',
        prompt: 'architectural glass station platform under aurora sky, crisp editorial poster layout',
        width: 1792,
        height: 2560,
        steps: 20,
        cfg: 1.0,
        seed: 9123401,
      },
      offsetMinutes: 74,
    }),
    makeGalleryItem({
      id: 'demo-gal-4-src',
      filename: 'krea2_moss_editorial_raw_004.png',
      project: '海报排版',
      width: 896,
      height: 1280,
      favorite: false,
      tags: ['Krea2+MOSS', '双采样', '海报'],
      svgOpts: { title: '星穹站台海报 · 双采原图', subtitle: 'Krea2 Turbo 构图 + MOSS Base 精绘 (896×1280)', badge: 'KREA2+MOSS', palette: 'amber', style: 'poster' },
      params: {
        workflow_id: 'krea2-moss-dual',
        workflow: 'krea2-moss-dual',
        mode: 'custom',
        model: 'moss-image-base-bf16',
        prompt: 'architectural glass station platform under aurora sky, crisp editorial poster layout',
        width: 896,
        height: 1280,
        steps: 32,
        cfg: 3.0,
        shift: 4.5,
        sampler: 'euler',
        scheduler: 'simple',
        seed: 9123401,
      },
      offsetMinutes: 82,
    }),
    makeGalleryItem({
      id: 'demo-gal-5',
      filename: 'anima_hsr_style_005.png',
      project: '角色设定',
      width: 1024,
      height: 1024,
      favorite: true,
      tags: ['Anima', 'HSR', '方图'],
      svgOpts: { title: '逆光立绘 · Anima HSR CG', subtitle: 'pack-anima-seedvr2 · 1024×1024 · Civitai 公开 LoRA', badge: 'ANIMA CG', palette: 'editorial', style: 'courtyard' },
      params: {
        workflow_id: 'pack-anima-seedvr2',
        workflow: 'pack-anima-seedvr2',
        mode: 'custom',
        model: 'animayume-v05',
        loras: [{ id: 'lora-anima-hsr-cg', name: 'anima_hsr_cg.safetensors', strength: 0.75 }],
        prompt: 'hsr cg illustration, luminous rim light, white coat traveler overlooking orbital ring',
        width: 1024,
        height: 1024,
        steps: 28,
        cfg: 4.5,
        sampler: 'euler_ancestral',
        scheduler: 'karras',
        seed: 7310442,
      },
      offsetMinutes: 120,
    }),
    makeGalleryItem({
      id: 'demo-gal-6',
      filename: 'illustrious_sdxl_006.png',
      project: '',
      width: 896,
      height: 1152,
      favorite: false,
      tags: ['Illustrious', 'SDXL'],
      svgOpts: { title: '未分类测试草稿 · SDXL v17', subtitle: '可用于测试项目归类、标签编辑与批量筛选', badge: 'SDXL v17', palette: 'arctic', style: 'poster' },
      params: {
        workflow_id: 'sdxl-txt2img',
        workflow: 'sdxl-txt2img',
        mode: 'txt2img',
        model: 'wai-illustrious-v17',
        prompt: 'monochrome ink sketch study of clockwork observatory interior, clean hatching',
        width: 896,
        height: 1152,
        steps: 26,
        cfg: 5.5,
        sampler: 'euler_ancestral',
        scheduler: 'normal',
        seed: 4419201,
      },
      offsetMinutes: 190,
    }),
  ];

  const tasks = [
    {
      id: 'demo-task-1001',
      status: 'completed',
      stage: 'completed',
      gpu: 'G4',
      actual_gpu: 'NVIDIA G4 (96 GB)',
      created_at: nowIso(20),
      finished_at: nowIso(18),
      sync_seconds: 0,
      gen_seconds: 26.4,
      duration_seconds: 26.4,
      cu_cost: 0.04,
      cu_balance_end: 84.6,
      progress: { value: 28, max: 28 },
      params: gallery[0].params,
      results: [gallery[0]],
    },
    {
      id: 'demo-task-1002',
      status: 'completed',
      stage: 'completed',
      gpu: 'G4',
      actual_gpu: 'NVIDIA G4 (96 GB)',
      created_at: nowIso(38),
      finished_at: nowIso(35),
      sync_seconds: 0,
      gen_seconds: 41.8,
      duration_seconds: 41.8,
      cu_cost: 0.06,
      cu_balance_end: 84.64,
      progress: { value: 36, max: 36 },
      params: gallery[1].params,
      results: [gallery[1]],
    },
    {
      id: 'demo-task-1003',
      status: 'completed',
      stage: 'completed',
      gpu: 'G4',
      actual_gpu: 'NVIDIA G4 (96 GB)',
      created_at: nowIso(58),
      finished_at: nowIso(52),
      sync_seconds: 18.2,
      gen_seconds: 94.5,
      duration_seconds: 112.7,
      cu_cost: 0.17,
      cu_balance_end: 84.7,
      progress: { value: 8, max: 8 },
      params: gallery[2].params,
      results: [gallery[2]],
    },
    {
      id: 'demo-task-1004',
      status: 'completed',
      stage: 'completed',
      gpu: 'G4',
      actual_gpu: 'NVIDIA G4 (96 GB)',
      created_at: nowIso(78),
      finished_at: nowIso(74),
      sync_seconds: 0,
      gen_seconds: 34.1,
      duration_seconds: 34.1,
      cu_cost: 0.05,
      cu_balance_end: 84.87,
      progress: { value: 20, max: 20 },
      params: gallery[3].params,
      results: [gallery[3]],
    },
    {
      id: 'demo-task-1005',
      status: 'completed',
      stage: 'completed',
      gpu: 'A100',
      actual_gpu: 'NVIDIA A100-SXM4-40GB',
      created_at: nowIso(124),
      finished_at: nowIso(120),
      sync_seconds: 11.5,
      gen_seconds: 22.9,
      duration_seconds: 34.4,
      cu_cost: 0.05,
      cu_balance_end: 84.92,
      progress: { value: 28, max: 28 },
      params: gallery[5].params,
      results: [gallery[5]],
    },
  ];

  return {
    baseUrl: cleanBase,
    runtime: {
      state: 'ready',
      gpu: 'G4',
      actual_gpu: 'NVIDIA G4 (96 GB)',
      runtime_id: 'demo-colab-g4-01',
      mode: 'temporary',
      uptime: 4920,
      memory_used: 28.4 * 1073741824,
      memory_total: 96 * 1073741824,
      queue_count: 0,
      comfy_url: '',
      error: '',
      session_cu_used: 7.38,
      quota: {
        balance: 84.6,
        consumption_rate: 5.4,
      },
      logs: [
        { time: Math.floor(Date.now() / 1000) - 4800, message: '[Demo] Assigned Colab runtime NVIDIA G4 (96 GB VRAM)' },
        { time: Math.floor(Date.now() / 1000) - 4620, message: '[Demo] Mounted Drive cache DrawingStudio/models (11 models ready)' },
        { time: Math.floor(Date.now() / 1000) - 4550, message: '[Demo] ComfyUI listening on 127.0.0.1:8188 (worker=1)' },
        { time: Math.floor(Date.now() / 1000) - 180, message: '[Demo] Task demo-task-1001 archived to Drive folder 角色设定' },
      ],
    },
    gpus: {
      balance: 84.6,
      consumption_rate: 5.4,
      assignments_count: 1,
      gpus: [
        { id: 'G4', name: 'NVIDIA G4 (96 GB)', memory: 96, available: true, eligible: true },
        { id: 'H100', name: 'NVIDIA H100 (80 GB)', memory: 80, available: true, eligible: true },
        { id: 'A100', name: 'NVIDIA A100 (40 GB)', memory: 40, available: true, eligible: true },
        { id: 'L4', name: 'NVIDIA L4 (24 GB)', memory: 24, available: true, eligible: true },
        { id: 'T4', name: 'NVIDIA T4 (16 GB)', memory: 16, available: true, eligible: true },
      ],
    },
    settings: {
      theme: 'light',
      mode: 'temporary',
      comfy_workers: 1,
      idle_minutes: 60,
      comfy_url: '',
      drive_configured: true,
      colab_configured: true,
      prune_local_after_archive: true,
      drive_root_name: 'DrawingStudio',
      drive_root_id: 'demo-root-folder-id',
      drive_folder_url: 'https://drive.google.com/drive/folders/demo-drawing-studio',
      drive_folders: {
        checkpoints: 'DrawingStudio/models/checkpoints',
        diffusion_models: 'DrawingStudio/models/diffusion_models',
        loras: 'DrawingStudio/models/loras',
        vae: 'DrawingStudio/models/vae',
      },
    },
    models,
    workflows,
    gallery,
    customProjects: ['角色设定', '分镜漫画', '海报排版', '视频镜头'],
    tasks,
    presets: [
      {
        id: 'preset-moss-sft',
        name: 'MOSS SFT 竖版漫画',
        params: {
          workflow_id: 'moss-sft',
          workflow: 'moss-sft',
          mode: 'custom',
          model: 'moss-image-sft-int8',
          width: 896,
          height: 1280,
          steps: 28,
          cfg: 3.5,
          shift: 3.5,
          sampler: 'er_sde',
          scheduler: 'simple',
        },
      },
      {
        id: 'preset-krea2-moss',
        name: 'Krea2 + MOSS 双采精绘',
        params: {
          workflow_id: 'krea2-moss-dual',
          workflow: 'krea2-moss-dual',
          mode: 'custom',
          model: 'moss-image-base-bf16',
          width: 896,
          height: 1344,
          steps: 32,
          cfg: 3.0,
          shift: 4.5,
          sampler: 'euler',
          scheduler: 'simple',
        },
      },
      {
        id: 'preset-h3-turbo',
        name: 'MiniMax H3 8步视频',
        params: {
          workflow_id: 'h3-fl2va-keyframes-8step-int8',
          workflow: 'h3-fl2va-keyframes-8step-int8',
          mode: 'custom',
          model: 'minimax-h3-fl2va-int8',
          width: 1280,
          height: 736,
          duration_seconds: 5,
          steps: 8,
          video_crf: 15,
        },
      },
    ],
    keys: [
      { id: 'key-demo-1', name: 'OpenAI / Agent 示例密钥', prefix: 'ds-demo_8f', revoked: false },
    ],
    requests: [
      { path: '/v1/images/generations', status: 200, duration: 24.1 },
      { path: '/v1/models', status: 200, duration: 0.02 },
    ],
    uploads: new Map(),
    seq: 2000,
  };
}
