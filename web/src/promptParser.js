/**
 * 绘图提示词文本识别与解析模块 (v1.0.18 超强泛化版)
 * 能够自动容忍 Markdown、自然语言、代码块、加粗标题、混合格式及尾部思维链与说明
 */

import { isMossLayoutPrompt } from './mossLayout.js';

export const SAMPLER_MAP = {
  'euler a': 'euler_ancestral',
  'euler_a': 'euler_ancestral',
  'euler ancestral': 'euler_ancestral',
  'euler_ancestral': 'euler_ancestral',
  'euler': 'euler',
  'er_sde': 'er_sde',
  'er sde': 'er_sde',
  'ersde': 'er_sde',
  'res_multistep': 'res_multistep',
  'res multistep': 'res_multistep',
  'heun': 'heun',
  'dpmpp_2s_ancestral': 'dpmpp_2s_ancestral',
  'dpm++ 2s ancestral': 'dpmpp_2s_ancestral',
  'dpm++ 2m': 'dpmpp_2m',
  'dpmpp_2m': 'dpmpp_2m',
  'dpm++ 2m karras': 'dpmpp_2m',
  'dpm++ 2m sde': 'dpmpp_2m_sde',
  'dpmpp_2m_sde': 'dpmpp_2m_sde',
  'dpm++ 2m sde karras': 'dpmpp_2m_sde',
  'dpm++ 3m sde': 'dpmpp_3m_sde',
  'dpmpp_3m_sde': 'dpmpp_3m_sde',
  'uni_pc': 'uni_pc',
  'unipc': 'uni_pc',
  'ddim': 'ddim'
};

export const SCHEDULER_MAP = {
  'normal': 'normal',
  'karras': 'karras',
  'exponential': 'exponential',
  'sgm_uniform': 'sgm_uniform',
  'simple': 'simple',
  'ddim_uniform': 'ddim_uniform',
  'beta': 'beta',
  'linear_quadratic': 'linear_quadratic',
  'linear quadratic': 'linear_quadratic',
  'kl_optimal': 'kl_optimal',
  'kl optimal': 'kl_optimal'
};

export function extractLoras(text) {
  if (!text || typeof text !== 'string') return [];
  const loras = [];
  const regex = /<lora:([^:>]+)(?::([0-9.]+))?>/gi;
  let match;
  while ((match = regex.exec(text)) !== null) {
    loras.push({
      name: match[1].trim(),
      strength: match[2] ? parseFloat(match[2]) : 1.0
    });
  }
  return loras;
}

export function containsMixedPrompt(text) {
  if (!text || typeof text !== 'string') return false;
  if (isMossLayoutPrompt(text)) return false;
  return /(?:negative\s+prompt|uc\s*[:：]|undesired\s+content|负向|反向|负面|关键帧|分镜|scene|shot|prompt\s*[:：]|\*\*prompt\*\*)/i.test(text);
}

function unwrapCodeBlocks(text) {
  if (!text || typeof text !== 'string') return '';
  return text.replace(/```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```/g, '$1').trim();
}

/**
 * 从一行或多行中抽取各种格式的参数
 */
function extractAndStripParams(text, paramsTarget) {
  if (!text) return '';
  let cleaned = text;

  // 1. Markdown 表格行：| 尺寸 | 896x1280 |
  const tableRowRegex = /\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|/g;
  let match;
  while ((match = tableRowRegex.exec(cleaned)) !== null) {
    const k = match[1].trim().toLowerCase();
    const v = match[2].trim().replace(/[`*]/g, '');
    if (k.includes('尺寸') || k.includes('size') || k.includes('分辨率')) {
      const dims = v.split(/[x*×]/i).map(s => parseInt(s.trim(), 10));
      if (dims.length === 2 && !isNaN(dims[0]) && !isNaN(dims[1])) {
        paramsTarget.width = dims[0];
        paramsTarget.height = dims[1];
      }
    } else if (k.includes('step') || k.includes('步数')) {
      const n = parseInt(v, 10);
      if (!isNaN(n)) paramsTarget.steps = n;
    } else if (k.includes('cfg')) {
      const n = parseFloat(v);
      if (!isNaN(n)) paramsTarget.cfg = n;
    } else if (k.includes('sampler') || k.includes('采样器')) {
      const sParts = v.split(/[\/\s,、]+/);
      const s1 = (sParts[0] || '').toLowerCase().trim();
      const s2 = (sParts[1] || '').toLowerCase().trim();
      if (SAMPLER_MAP[s1]) paramsTarget.sampler = SAMPLER_MAP[s1];
      if (SCHEDULER_MAP[s2]) paramsTarget.scheduler = SCHEDULER_MAP[s2];
    } else if (k.includes('scheduler') || k.includes('调度器')) {
      const sc = v.toLowerCase().trim();
      if (SCHEDULER_MAP[sc]) paramsTarget.scheduler = SCHEDULER_MAP[sc];
    }
  }

  // 2. 独立参数行：**896x1280 | Steps 32 | CFG 3.2 | euler / simple** 或 **推荐尺寸**: `896x1344` | **Steps**: `32`
  const inlineParamRegex = /(?:^|\n)\s*(?:\*\*)?(?:(?:推荐尺寸|尺寸|size|steps|步数|cfg|sampler|scheduler|调度器|采样器)\s*[:：]?\s*[`*]?[^|\n`]+[`*]?\s*(?:\||\s{2,}))+[\s\S]*?(?=\n|$)/gi;

  // 全文通用参数扫描
  const generalParamTokens = [
    { regex: /(?:尺寸|size|推荐尺寸)\s*[:：]?\s*[`*]?([0-9]{3,4})\s*[x*×]\s*([0-9]{3,4})[`*]?/i, fn: m => { paramsTarget.width = parseInt(m[1], 10); paramsTarget.height = parseInt(m[2], 10); } },
    { regex: /(?:^|[|\s*`])([0-9]{3,4})\s*[x*×]\s*([0-9]{3,4})(?:[|\s*`]|$)/, fn: m => { if (!paramsTarget.width) { paramsTarget.width = parseInt(m[1], 10); paramsTarget.height = parseInt(m[2], 10); } } },
    { regex: /(?:steps|步数)\s*[:：]?\s*[`*]?([0-9]+)[`*]?/i, fn: m => { paramsTarget.steps = parseInt(m[1], 10); } },
    { regex: /(?:cfg(?:\s+scale)?)\s*[:：]?\s*[`*]?([0-9.]+)[`*]?/i, fn: m => { paramsTarget.cfg = parseFloat(m[1]); } },
    { regex: /(?:shift|偏移)\s*[:：=]?\s*[`*]?([0-9]+(?:\.[0-9]+)?)[`*]?/i, fn: m => { paramsTarget.shift = parseFloat(m[1]); } },
    { regex: /(?:seed|种子)\s*[:：]?\s*[`*]?([0-9\-]+)[`*]?/i, fn: m => { paramsTarget.seed = parseInt(m[1], 10); } },
    { regex: /(?:sampler|采样器)\s*[:：]?\s*[`*]?([A-Za-z0-9_+\s]+?)(?:[`*]|\/|\s*,\s*|\||$)/i, fn: m => { const k = m[1].toLowerCase().trim(); if (SAMPLER_MAP[k]) paramsTarget.sampler = SAMPLER_MAP[k]; } },
    { regex: /(?:scheduler|调度器)\s*[:：]?\s*[`*]?([A-Za-z0-9_]+)[`*]?/i, fn: m => { const k = m[1].toLowerCase().trim(); if (SCHEDULER_MAP[k]) paramsTarget.scheduler = SCHEDULER_MAP[k]; } },
    { regex: /\b(euler_ancestral|euler|er_sde|res_multistep|heun|dpmpp_2s_ancestral|dpm\+\+\s*2m(?:\s*sde)?|uni_pc|ddim)\s*\/\s*(simple|normal|karras|sgm_uniform|beta|exponential|ddim_uniform|linear_quadratic|kl_optimal)\b/i, fn: m => { paramsTarget.sampler = SAMPLER_MAP[m[1].toLowerCase().trim()] || m[1].toLowerCase(); paramsTarget.scheduler = SCHEDULER_MAP[m[2].toLowerCase().trim()] || m[2].toLowerCase(); } }
  ];

  for (const item of generalParamTokens) {
    const m = item.regex.exec(cleaned);
    if (m) item.fn(m);
  }

  // 清洗参数表格与行
  cleaned = cleaned.replace(/\|[^\n]+\|\n\s*\|[-:\s|]+\|\n(?:\s*\|[^\n]+\|\n?)*/g, '');
  cleaned = cleaned.replace(inlineParamRegex, '');
  cleaned = cleaned.replace(/(?:^|\n)\s*\*\*[0-9xX×\s|A-Za-z0-9_./:：`\-—]+\*\*\s*(?=\n|$)/g, '');
  cleaned = cleaned.replace(/(?:^|\n)\s*(?:推荐尺寸|尺寸|Steps|CFG|Sampler)[^\n]+(?=\n|$)/gi, '');
  cleaned = cleaned.replace(/(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*{1,2})?(?:(?:推荐参数|生成参数|参数配置|Recommended\s+Parameters?)\s*[:：]?|参数\s*[:：])(?:\*{1,2})?\s*(?=\n|$)/gi, '');

  return cleaned.trim();
}

/**
 * 剥离大模型的思维链、设计分析及文末备注
 */
function stripNotesAndThinking(text) {
  if (!text || typeof text !== 'string') return '';
  let cleaned = text;

  // 1. 标签：<think> ... </think>
  cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>/gi, '');

  // 2. 尾部说明段落/备注段落（包括 ## 备注、## 参数、## 说明 直到文末）
  cleaned = cleaned.replace(/(?:^|\n)\s*(?:---|___|\*\*\*)\s*(?:\n+\s*#{1,4}\s*(?:参数|备注|说明|设计思路|思维链|提示词结构|分析|注意|注意事项|解释)[\s\S]*)?$/gi, '');
  cleaned = cleaned.replace(/(?:^|\n)#{1,4}\s*(?:参数|备注|说明|设计思路|思维链|提示词结构|分析|注意|注意事项|解释)\b[\s\S]*$/gi, '');

  // 3. 剥离纯列表式分析段落（如 **蚂蚁的数量**是这张的核心... 或 - **触发词**... 直到文末）
  cleaned = cleaned.replace(/(?:^|\n)\s*(?:-\s*)?\*\*(?:触发词|受力关系|蚂蚁|反向提示词|负面|结构|参数|人物|姿态|单脚吊起|她直接高潮|动作|光影)[^\n]*\*\*[\s\S]*$/gi, '');

  return cleaned.trim();
}

/**
 * 剥离标题行
 */
function stripTitleLines(text) {
  if (!text) return '';
  return text
    .replace(/^(?:\*{1,3}|#{1,4}\s*)?(?:关键帧|图|分镜|Shot|Scene)\s*[0-9a-zA-Z一二三四五六七八九十]*[：:\s\-—_]*[^\n]*/i, '')
    .replace(/^(?:\*{1,3}|#{1,4}\s*)?(?:负面提示词|负向提示词|反向提示词|Negative(?:\s+prompt)?|UC|Undesired\s+Content)\s*[0-9a-zA-Z一二三四五六七八九十]*[：:\s\-—_]*/i, '')
    .replace(/^(?:\*{1,3}|#{1,4}\s*)?(?:Prompt|Positive(?:\s+prompt)?|正向提示词|正面提示词)\s*[0-9a-zA-Z一二三四五六七八九十]*[：:\s\-—_]*/i, '')
    .trim();
}

/**
 * 解析单块正负词内容
 */
export function parseSingleBlock(raw, title = '') {
  if (!raw || typeof raw !== 'string') {
    return { title: title || '图 1', prompt: '', negative_prompt: '', params: {}, loras: [] };
  }

  let processed = stripNotesAndThinking(raw);
  const params = {};
  processed = extractAndStripParams(processed, params);

  let prompt = '';
  let negative_prompt = '';

  // 负向指示标：**Negative Prompt**: 或 ### 负面提示词 等
  const negMarkerRegex = /(?:^|\n|\r\n)\s*(?:(?:---|___|\*\*\*)\s*[\r\n]+)?\s*(?:\*{1,3}|#{1,4}\s*)?(?:Negative\s+Prompt|Negative|UC|Undesired\s+Content|负面提示词|负向提示词|反向提示词|负面|负向|反向|【负面提示词】|【负向提示词】|【反向提示词】)\s*[0-9a-zA-Z一二三四五六七八九十]*[：:\s\-—_*]*\s*/i;

  if (negMarkerRegex.test(processed)) {
    const parts = processed.split(negMarkerRegex);
    let rawPos = parts[0] || '';
    let rawNeg = parts.slice(1).join('\n') || '';

    rawNeg = stripNotesAndThinking(rawNeg);
    prompt = stripTitleLines(unwrapCodeBlocks(rawPos));
    negative_prompt = stripTitleLines(unwrapCodeBlocks(rawNeg));
  } else {
    // 检查带标题分段
    const mdParts = processed.split(/(?:^|\n)#{1,4}\s*(?:负向|反向|负面|Negative|UC)\b[^\n]*/i);
    if (mdParts.length > 1) {
      let rawPos = mdParts[0].replace(/(?:^|\n)#{1,4}\s*(?:正向|正面|Positive|Prompt|关键帧)[^\n]*/i, '').trim();
      let rawNeg = mdParts.slice(1).join('\n').trim();
      prompt = stripTitleLines(unwrapCodeBlocks(rawPos));
      negative_prompt = stripTitleLines(unwrapCodeBlocks(rawNeg));
    } else {
      prompt = stripTitleLines(unwrapCodeBlocks(processed));
    }
  }

  negative_prompt = extractAndStripParams(negative_prompt, params);
  negative_prompt = stripNotesAndThinking(negative_prompt);

  // 清洗首尾残留的 Markdown 粗体符、代码符与标点
  prompt = prompt.replace(/^[`*~,，\s\-_—]+|[`*~,，\s\-_—]+$/g, '').trim();
  negative_prompt = negative_prompt.replace(/^[`*~,，\s\-_—]+|[`*~,，\s\-_—]+$/g, '').trim();

  return {
    title: title || '图 1',
    prompt,
    negative_prompt,
    params,
    loras: extractLoras(prompt)
  };
}

/**
 * 多图切分与识别（全面覆盖：### 关键帧、**Prompt**:、图1、Scene 1、数字列表等任意混合排版）
 */
export function parseMultiPrompts(raw) {
  if (!raw || typeof raw !== 'string') return [];
  const text = raw.trim();
  if (!text) return [];

  // 1. JSON 数组
  if (text.startsWith('[') && text.endsWith(']')) {
    try {
      const arr = JSON.parse(text);
      if (Array.isArray(arr) && arr.length > 0) {
        return arr.map((item, idx) => {
          if (typeof item === 'string') return parseSingleBlock(item, `图 ${idx + 1}`);
          return parseSingleBlock(JSON.stringify(item), item.title || `图 ${idx + 1}`);
        });
      }
    } catch (_) {}
  }

  const globalParams = {};
  extractAndStripParams(text, globalParams);
  const globalCleaned = stripNotesAndThinking(text);

  // 截断到非图片段落（## 参数 / ## 备注）
  const endMarkerMatch = /(?:^|\n)\s*(?:---|___|\*\*\*)\s*(?:\n+\s*#{1,4}\s*(?:参数|备注|说明))|(?:^|\n)#{1,4}\s*(?:参数|备注|说明)/i.exec(globalCleaned);
  const effectiveLength = endMarkerMatch ? endMarkerMatch.index : globalCleaned.length;

  // 2. 超强多图正则匹配：
  // 识别各类多图分段标头：
  // (a) ### 关键帧：... / ### 图1：... / ### Scene 1...
  // (b) **Prompt**: ...
  // (c) 图 1: ... / 图 A ... / 图1：...
  // (d) Scene 1 / Shot 1 ...
  const itemSplitRegex = /(?:^|\n)\s*(?:(?:#{1,4}\s*(?:关键帧|分镜|Scene|Shot|Image|图|图片)\s*([0-9a-zA-Z一二三四五六七八九十]*)[：:\s\-—_]*([^\n`]*))|(?:\*\*(?:Prompt|Positive(?:\s+Prompt)?)\*\*\s*[:：]?)|(?:(?:第\s*[0-9一二三四五六七八九十]+\s*(?:张|图|镜)|图|图片)\s*([0-9a-zA-Z一二三四五六七八九十]+)[：:\s\-—_]*([^\n`]*)))/gi;

  const matches = [];
  let m;
  let counter = 1;
  while ((m = itemSplitRegex.exec(globalCleaned)) !== null) {
    if (m.index >= effectiveLength) break;

    const matchedStr = m[0] || '';
    // 排除负面提示词等伪关键帧
    if (/负面|负向|反向|negative/i.test(matchedStr)) {
      continue;
    }

    const tagId = m[1] || m[3] || `${counter}`;
    const tagDesc = (m[2] || m[4] || '').trim().replace(/[*`#]/g, '');

    let title = `图 ${tagId || counter}`;
    if (tagDesc && !tagDesc.startsWith('`')) {
      title += ` · ${tagDesc.slice(0, 15)}`;
    }

    matches.push({
      index: m.index,
      length: m[0].length,
      title
    });
    counter++;
  }

  if (matches.length >= 2) {
    const results = [];
    for (let i = 0; i < matches.length; i++) {
      const start = matches[i].index + matches[i].length;
      const end = (i + 1 < matches.length) ? matches[i + 1].index : effectiveLength;
      const chunk = globalCleaned.slice(start, end).trim();
      if (chunk) {
        const item = parseSingleBlock(chunk, matches[i].title);
        item.params = { ...globalParams, ...item.params };
        results.push(item);
      }
    }
    if (results.length > 0) return results;
  }

  // 3. 兜底数字编号列表 (1. ... 2. ...)
  const numSplitRegex = /(?:^|\n)\s*([0-9]{1,2})[\.、]\s+/g;
  const numMatches = [];
  while ((m = numSplitRegex.exec(globalCleaned)) !== null) {
    if (m.index >= effectiveLength) break;
    numMatches.push({ index: m.index, length: m[0].length, title: `图 ${m[1]}` });
  }

  if (numMatches.length >= 2) {
    const results = [];
    for (let i = 0; i < numMatches.length; i++) {
      const start = numMatches[i].index + numMatches[i].length;
      const end = (i + 1 < numMatches.length) ? numMatches[i + 1].index : effectiveLength;
      const chunk = globalCleaned.slice(start, end).trim();
      if (chunk) {
        const item = parseSingleBlock(chunk, numMatches[i].title);
        item.params = { ...globalParams, ...item.params };
        results.push(item);
      }
    }
    if (results.length > 0) return results;
  }

  // 4. 单图降级
  const single = parseSingleBlock(globalCleaned, '图 1');
  single.params = { ...globalParams, ...single.params };
  return [single];
}

export function parsePromptText(raw) {
  const list = parseMultiPrompts(raw);
  return list[0] || { prompt: '', negative_prompt: '', params: {}, loras: [] };
}
