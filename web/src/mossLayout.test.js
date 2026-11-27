import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  MOSS_LAYOUT_PRESETS,
  snap16,
  compileMossLayout,
  isMossLayoutPrompt,
  parseMossLayoutPrompt,
  fitMossLayoutToCanvas,
  rescaleCompiledMossLayout,
  injectTriggerIntoMossLayout,
  stripNegativeTextConflicts,
  buildMossLayoutPreset,
} from './mossLayout.js';

test('compileMossLayout matches exact OpenMOSS layout.md centered yxyx example', () => {
  const draft = {
    width: 1536,
    height: 1024,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: {
      meta: ['product campaign', 'studio photography'],
      canvas: 'single',
      caption: 'A warm studio campaign for a handmade ceramic cup.',
    },
    items: [
      {
        label: 'object',
        caption: 'An ivory ceramic cup.',
        bbox: [176, 224, 800, 864],
      },
      {
        label: 'text',
        caption: 'Large dark-green Chinese title.',
        text: '把日常，过成诗。',
        bbox: [928, 272, 1408, 448],
      },
    ],
  };

  const compiledStr = compileMossLayout(draft);
  assert.ok(!compiledStr.includes('\n'), 'Compiled JSON must be a single physical line');
  const compiled = JSON.parse(compiledStr);
  assert.equal(compiled.width, undefined);
  assert.equal(compiled.height, undefined);
  assert.equal(compiled.bbox_coord_space, 'abs_centralized');
  assert.equal(compiled.bbox_format, 'yxyx');
  assert.deepEqual(compiled.items[0].bbox, [-288, -592, 352, 32]);
  assert.deepEqual(compiled.items[1].bbox, [-240, 160, -64, 640]);
  assert.equal(isMossLayoutPrompt(compiledStr), true);
});

test('compileMossLayout encloses fractional edges and rejects invalid dimensions', () => {
  const compiled = JSON.parse(compileMossLayout({
    width: 1024,
    height: 1024,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: { caption: 'Minimal poster' },
    items: [{ label: 'object', caption: 'Center block', bbox: [100.7, 200.9, 300.2, 400.1] }],
  }));
  // floor(200.9)-512 = -312, floor(100.7)-512 = -412, ceil(400.1)-512 = -111, ceil(300.2)-512 = -211
  assert.deepEqual(compiled.items[0].bbox, [-312, -412, -111, -211]);

  assert.throws(() => compileMossLayout({
    width: 1000,
    height: 1024,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: { caption: 'Bad width' },
    items: [{ label: 'object', caption: 'x', bbox: [0, 0, 100, 100] }],
  }), /16 的正整数倍/);

  assert.throws(() => compileMossLayout({
    width: 1024,
    height: 1024,
    bbox_coord_space: 'abs',
    bbox_format: 'xyxy',
    summary: { caption: 'Missing text' },
    items: [{ label: 'text', caption: 'Heading', bbox: [10, 10, 200, 100] }],
  }), /准确文字/);
});

test('round-trip parseMossLayoutPrompt, out-of-bounds detection, and rescale work across aspect ratios', () => {
  const draft = buildMossLayoutPreset('poster-guochao', 832, 1248, { triggerPrefix: 'custom_style' });
  const compiled = compileMossLayout(draft);
  const parsed = parseMossLayoutPrompt(compiled, 832, 1248);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.outOfBounds, false);
  assert.equal(parsed.hasTextItems, true);
  assert.equal(compileMossLayout(parsed.draft), compiled);

  // Larger canvas layout parsed on smaller 512x512 triggers outOfBounds and rescales cleanly
  const smallCheck = parseMossLayoutPrompt(compiled, 512, 512);
  assert.equal(smallCheck.ok, true);
  assert.equal(smallCheck.outOfBounds, true);
  const rescaled = rescaleCompiledMossLayout(compiled, 1664, 2496);
  const after = parseMossLayoutPrompt(rescaled, 1664, 2496);
  assert.equal(after.ok, true);
  assert.equal(after.outOfBounds, false);
});

test('all 6 MOSS layout presets compile cleanly at 1K and 2K canvas sizes', () => {
  const sizes = [[832, 1248], [896, 1344], [1664, 2496], [2496, 1664]];
  for (const preset of MOSS_LAYOUT_PRESETS) {
    for (const [w, h] of sizes) {
      const draft = buildMossLayoutPreset(preset.id, w, h, { triggerPrefix: 'custom_style' });
      const out = compileMossLayout(draft);
      assert.equal(isMossLayoutPrompt(out), true, `${preset.id} at ${w}x${h}`);
      const parsed = JSON.parse(out);
      assert.equal(parsed.summary.canvas, preset.canvas);
    }
  }
});

test('injectTriggerIntoMossLayout, snap16, and stripNegativeTextConflicts behave cleanly', () => {
  assert.equal(snap16(1000), 1008);
  assert.equal(snap16(999), 992);
  const compiled = compileMossLayout(buildMossLayoutPreset('comic-popout-3', 832, 1248));
  const withTrigger = injectTriggerIntoMossLayout(compiled, 'custom_style');
  assert.equal(isMossLayoutPrompt(withTrigger), true);
  assert.ok(JSON.parse(withTrigger).summary.caption.startsWith('custom_style, '));
  // Idempotent when already present
  assert.equal(injectTriggerIntoMossLayout(withTrigger, 'custom_style'), withTrigger);

  // Plain text fallback
  assert.equal(injectTriggerIntoMossLayout('1girl', 'a wooden submission horse'), '1girl, a wooden submission horse');

  // Negative conflict stripping removes bare text/speech bubbles while preserving gibberish text/watermark
  assert.equal(
    stripNegativeTextConflicts('lowres, text, speech bubbles, gibberish text, garbled letters, watermark'),
    'lowres, gibberish text, garbled letters, watermark',
  );
  assert.deepEqual(fitMossLayoutToCanvas(buildMossLayoutPreset('poster-movie', 832, 1248), 1024, 1024).width, 1024);
});
