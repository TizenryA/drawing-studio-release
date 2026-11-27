import { test } from 'node:test';
import assert from 'node:assert/strict';
import { demoApi, resetDemoState } from './demoApi.js';

test('demoApi exposes all 38 public/builtin workflows and fetches details', async () => {
  resetDemoState('/');
  const workflows = await demoApi('/api/workflows');
  assert.equal(workflows.length, 38);
  const ids = new Set(workflows.map(w => w.id));
  assert.ok(ids.has('sdxl-txt2img'));
  assert.ok(ids.has('moss-sft'));
  assert.ok(ids.has('h3-fl2va-keyframes-8step-int8'));

  const sft = await demoApi('/api/workflows/moss-sft');
  assert.equal(sft.id, 'moss-sft');
  assert.ok(sft.workflow);
});

test('demoApi manages runtime GPUs, models sync, and keys cleanly', async () => {
  resetDemoState('/');
  const runtime = await demoApi('/api/runtime');
  assert.equal(runtime.state, 'ready');
  assert.equal(runtime.gpu, 'G4');

  const stopped = await demoApi('/api/runtime/stop', { method: 'POST' });
  assert.equal(stopped.state, 'off');

  const restarted = await demoApi('/api/runtime/start', {
    method: 'POST',
    body: JSON.stringify({ gpu: 'A100', mode: 'persistent' }),
  });
  assert.equal(restarted.state, 'ready');
  assert.equal(restarted.gpu, 'A100');

  const models = await demoApi('/api/models');
  assert.ok(models.length >= 10);
  const driveOnly = models.find(m => m.status === 'drive');
  assert.ok(driveOnly);
  const synced = await demoApi(`/api/models/${encodeURIComponent(driveOnly.id)}/sync`, { method: 'POST' });
  assert.equal(synced.status, 'ready');

  const createdKey = await demoApi('/api/keys', { method: 'POST', body: JSON.stringify({ name: 'test-key' }) });
  assert.ok(createdKey.key.startsWith('ds-demo_'));
  await demoApi(`/api/keys/${encodeURIComponent(createdKey.id)}`, { method: 'DELETE' });
  const keys = await demoApi('/api/keys');
  assert.ok(!keys.some(k => k.id === createdKey.id));
});

test('demoApi handles gallery filters, projects, upscaled parent compare, and task submission', async () => {
  resetDemoState('/');
  const projects = await demoApi('/api/gallery/projects');
  assert.ok(projects.total >= 7);
  assert.ok(projects.projects.some(p => p.name === '视频镜头'));

  const videos = await demoApi('/api/gallery?media_type=video&page=1&limit=20&paginated=true');
  assert.equal(videos.items.length, 1);
  assert.equal(videos.items[0].media_url, '/demo/h3-sample.mp4');

  const hd = await demoApi('/api/gallery/demo-gal-4-hd');
  assert.equal(hd.parent_id, 'demo-gal-4-src');
  const src = await demoApi(`/api/gallery/${hd.parent_id}`);
  assert.equal(src.id, 'demo-gal-4-src');

  const submitted = await demoApi('/api/tasks', {
    method: 'POST',
    body: JSON.stringify({
      workflow_id: 'moss-sft',
      prompt: 'demo test illustration',
      width: 896,
      height: 1280,
      steps: 28,
      project: '演示新建项目',
    }),
  });
  assert.equal(submitted.status, 'queued');

  await new Promise(resolve => setTimeout(resolve, 2350));
  const finished = await demoApi(`/api/tasks/${submitted.id}`);
  assert.equal(finished.status, 'completed');
  assert.equal(finished.results.length, 1);

  const afterGallery = await demoApi('/api/gallery?project=%E6%BC%94%E7%A4%BA%E6%96%B0%E5%BB%BA%E9%A1%B9%E7%9B%AE');
  assert.equal(afterGallery.total, 1);
  resetDemoState('/');
});

test('demoApi simulates OAuth status, manual PKCE start/exchange, import, test, and disconnect', async () => {
  resetDemoState('/');
  const st0 = await demoApi('/api/oauth/status');
  assert.equal(st0.colab.configured, true);
  assert.equal(st0.drive.configured, true);

  await demoApi('/api/oauth/drive', { method: 'DELETE' });
  const st1 = await demoApi('/api/oauth/status');
  assert.equal(st1.drive.configured, false);

  const started = await demoApi('/api/oauth/drive/start', {
    method: 'POST',
    body: JSON.stringify({ client_id: 'custom-cid.apps.googleusercontent.com', flow_mode: 'manual' }),
  });
  assert.ok(started.auth_url.includes('code_challenge_method=S256'));

  const exchanged = await demoApi('/api/oauth/drive/exchange', {
    method: 'POST',
    body: JSON.stringify({ flow_id: started.flow_id, code_or_url: 'http://127.0.0.1:8765/callback?code=demo-code' }),
  });
  assert.equal(exchanged.ok, true);
  assert.equal(exchanged.status.configured, true);

  const probed = await demoApi('/api/oauth/colab/test', { method: 'POST' });
  assert.equal(probed.ok, true);
  assert.ok(probed.message.includes('Colab'));
  resetDemoState('/');
});
