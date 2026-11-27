import assert from 'node:assert/strict';
import { containsMixedPrompt, parseMultiPrompts } from './promptParser.js';

const doc = `多一点兼容性行不行

### 关键帧：林间庭院 · 日落斜照

\`\`\`markdown
style_demo, 1girl, silver haired swordsman (original), masterpiece, best quality,

slender body, pale skin, short silver hair in twin tails, sharp crimson eyes, black choker, flowing cloak, standing beneath an old courtyard tree,

slanted sunlight through the leaves, quiet ancient stone courtyard, fallen autumn leaves, packed earth, dust motes floating in air, chiaroscuro lighting, deep shadow in background,

rich thick-paint texture
\`\`\`

### 负面提示词
\`\`\`markdown
lowres, blurry, bad anatomy, text, watermark, (decapitated, severed head, detached head, amputee, missing arms, extra limbs:1.5), fused fingers, extra fingers
\`\`\`

**896x1280 | Steps 32 | CFG 3.2 | euler / simple**



**Prompt**:
\`\`\`
style_demo, 1girl, silver haired swordsman, solo, slender body, pale skin, short silver hair in twin tails, sharp crimson eyes, black choker, standing on stone steps beneath a huge old tree in a quiet courtyard, dappled afternoon sunlight through the leaves, dust motes floating in the light, high contrast chiaroscuro, thick painterly texture, masterpiece
\`\`\`
**Negative Prompt**:
\`\`\`
lowres, blurry, bad anatomy, (decapitated, severed head, detached head, amputee, missing arms, extra arms, extra legs:1.5), text, watermark, modern elements
\`\`\`
**推荐尺寸**: \`896x1344\` | **Steps**: \`32\` | **CFG**: \`3.2\`

**Prompt**:
\`\`\`
style_demo, 1girl, silver haired swordsman, solo, close-up portrait beneath autumn foliage, wind blowing silver strands across her cheek, sharp gaze toward camera, dramatic rim light, deep shadow, high contrast chiaroscuro, thick painterly texture, masterpiece
\`\`\`
**Negative Prompt**:
\`\`\`
lowres, blurry, bad anatomy, (decapitated, severed head, detached head, amputee, missing arms, extra arms, extra legs:1.5), text, watermark, modern elements, calm expression
\`\`\`
**推荐尺寸**: \`896x1344\` | **Steps**: \`32\` | **CFG**: \`3.2\`


### 关键帧：古树下 · 逆光回眸
\`\`\`markdown
style_demo, silver haired swordsman (original), 1girl, solo, masterpiece, best quality, incredibly detailed,

a young swordsman with pale fair skin, short silver hair tied into twin tails, sharp crimson eyes, a dark choker around her throat, standing beneath a huge old tree with rough bark and exposed roots,

dry grass and fallen leaves on the packed earth, a low grey stone wall, a wooden fence, a stone well, eaves visible beyond, late afternoon sunlight coming through the leaves in broken patches, dust motes in the light, deep dappled shadow, thick painterly texture,

full body shot from a low three-quarter front angle, high contrast,
\`\`\`

### 负面提示词
\`\`\`markdown
lowres, blurry, bad anatomy, text, watermark, (decapitated, severed head, detached head, amputee, missing arms, extra limbs:1.5), cropped head, cropped feet, modern elements
\`\`\`

## 参数

| 项 | 值 |
|---|---|
| 尺寸 | \`896x1280\` |
| Steps | 32 |
| CFG | 3.2 |
| Sampler、Scheduler | \`euler\`、\`simple\` |
| 流调度 | \`ModelSamplingAuraFlow\`（shift 5.0） |
| LoRA | 通用画风 0.75–0.8 |

## 备注

**光影与构图**是这张的核心，写成：\`dappled afternoon sunlight\`、\`dust motes\`、\`deep shadow\`、\`low three-quarter front angle\`，再在负面里排掉 \`cropped head, cropped feet, modern elements\`。

如果画面偏平，可以把 \`chiaroscuro\` 和 \`dramatic rim light\` 放到提示词前半段再跑一次。`;

const list = parseMultiPrompts(doc);
console.log('Total identified images:', list.length);

for (let i = 0; i < list.length; i++) {
  console.log(`[${i}] Title: "${list[i].title}" | Prompt: ${list[i].prompt.length} chars | Neg: ${list[i].negative_prompt.length} chars | Size: ${list[i].params.width}x${list[i].params.height}`);
}

assert.equal(list.length, 4, 'Should identify all 4 images from the mixed document');

for (let i = 0; i < list.length; i++) {
  const item = list[i];
  assert.ok(item.prompt.length > 50, `Image ${i} should have valid prompt`);
  assert.ok(!item.prompt.includes('```'), `Image ${i} prompt should not contain codeblocks`);
  assert.ok(!item.negative_prompt.includes('```'), `Image ${i} neg should not contain codeblocks`);
  assert.ok(!item.negative_prompt.includes('## 备注'), `Image ${i} neg should not contain notes`);
  assert.ok(!item.negative_prompt.includes('## 参数'), `Image ${i} neg should not contain param headers`);
  assert.ok(!item.negative_prompt.includes('光影与构图'), `Image ${i} neg should not contain thinking notes`);
}

console.log('✔ All 4 mixed format images perfectly parsed without any thinking/note noise!');

const mossTemplateWithTrailingParamHeader = `正向提示词：
style_demo, a sunlit courtyard portrait

负向提示词：
lowres, blurry, bad anatomy

推荐参数：`;

const parsedTrailing = parseMultiPrompts(mossTemplateWithTrailingParamHeader);
assert.equal(parsedTrailing.length, 1);
assert.equal(parsedTrailing[0].prompt, 'style_demo, a sunlit courtyard portrait');
assert.equal(parsedTrailing[0].negative_prompt, 'lowres, blurry, bad anatomy');

const mossJsonWithSceneWord = JSON.stringify({
  summary: { meta: ['poster'], canvas: 'single', caption: 'A dark fantasy scene shot in rim light.' },
  items: [{ label: 'text', caption: 'Title', text: 'NIGHT', bbox: [-300, -200, -180, 200] }],
  bbox_coord_space: 'abs_centralized',
  bbox_format: 'yxyx',
});
assert.equal(containsMixedPrompt(mossJsonWithSceneWord), false);
