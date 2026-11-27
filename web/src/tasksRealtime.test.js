import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTasksResponse, upsertTaskList } from './taskData.js';

test('normalizeTasksResponse parses paginated task envelopes and legacy arrays', () => {
  const paginated = normalizeTasksResponse({
    items: [{ id: 't1', status: 'completed' }],
    active_items: [{ id: 't0', status: 'running' }],
    total: 42,
    page: 1,
    limit: 20,
    total_pages: 3,
    has_more: true,
    stats: { active: 1, completed: 38, failed: 3, total_cu: 12.5, has_cu: true },
    models: ['moss-sft'],
  });
  assert.equal(paginated.items.length, 1);
  assert.equal(paginated.activeItems.length, 1);
  assert.equal(paginated.totalPages, 3);
  assert.equal(paginated.stats.completed, 38);
  assert.deepEqual(paginated.models, ['moss-sft']);

  const legacy = normalizeTasksResponse([
    { id: 'a', status: 'running' },
    { id: 'b', status: 'completed' },
  ], 1, 20);
  assert.equal(legacy.total, 2);
  assert.equal(legacy.activeItems.length, 1);
});

test('upsertTaskList merges SSE progress updates, keeps all active tasks, and bounds history', () => {
  const existing = [
    { id: 't-old-1', status: 'completed', updated_at: '2026-11-26T01:00:00Z' },
    { id: 't-old-2', status: 'completed', updated_at: '2026-11-26T02:00:00Z' },
    { id: 't-run', status: 'running', sampling_step: 2, total_steps: 28, updated_at: '2026-11-26T03:00:00Z' },
  ];
  const updated = upsertTaskList(existing, {
    id: 't-run',
    status: 'running',
    sampling_step: 14,
    total_steps: 28,
    updated_at: '2026-11-26T03:00:05Z',
  }, 2);
  assert.equal(updated.length, 2);
  assert.equal(updated[0].id, 't-run');
  assert.equal(updated[0].sampling_step, 14);
  assert.equal(updated[1].id, 't-old-2');
});
