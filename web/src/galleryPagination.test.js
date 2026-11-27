import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildPageNumbers,
  normalizeGalleryResponse,
  projectLabel,
  formatBytes,
  resolveMediaSources,
  UNASSIGNED_PROJECT,
} from './galleryPagination.js';

test('buildPageNumbers renders compact ranges with ellipsis', () => {
  assert.deepEqual(buildPageNumbers(1, 1), [1]);
  assert.deepEqual(buildPageNumbers(3, 7), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(buildPageNumbers(1, 12), [1, 2, '...', 12]);
  assert.deepEqual(buildPageNumbers(6, 12), [1, '...', 5, 6, 7, '...', 12]);
  assert.deepEqual(buildPageNumbers(12, 12), [1, '...', 11, 12]);
});

test('normalizeGalleryResponse handles both paginated objects and legacy arrays', () => {
  const paginated = normalizeGalleryResponse({
    items: [{ id: 'a' }, { id: 'b' }],
    total: 50,
    page: 2,
    limit: 24,
    total_pages: 3,
    has_more: true,
  });
  assert.equal(paginated.items.length, 2);
  assert.equal(paginated.total, 50);
  assert.equal(paginated.page, 2);
  assert.equal(paginated.limit, 24);
  assert.equal(paginated.totalPages, 3);
  assert.equal(paginated.hasMore, true);

  const legacy = normalizeGalleryResponse([{ id: 'x' }, { id: 'y' }], 1, 36);
  assert.equal(legacy.items.length, 2);
  assert.equal(legacy.total, 2);
  assert.equal(legacy.totalPages, 1);
  assert.equal(legacy.hasMore, false);
});

test('projectLabel and formatBytes format cleanly', () => {
  assert.equal(projectLabel(''), '未分类');
  assert.equal(projectLabel(UNASSIGNED_PROJECT), '未分类');
  assert.equal(projectLabel('角色合集'), '角色合集');
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB');
});

test('resolveMediaSources selects progressive layers for modal preview vs fullscreen original', () => {
  const modal = resolveMediaSources({
    id: 'img 1',
    url: '/api/gallery/img%201/download',
    thumbnail_url: '/api/gallery/img%201/thumbnail',
    preview_url: '/api/gallery/img%201/preview',
  });
  assert.deepEqual(modal, {
    baseSrc: '/api/gallery/img%201/thumbnail',
    targetSrc: '/api/gallery/img%201/preview',
    fallbackSrc: '/api/gallery/img%201/download',
  });

  const fullscreen = resolveMediaSources(
    {
      id: 'legacy-1',
      url: '/api/gallery/legacy-1/download',
      thumbnail_url: '/api/gallery/legacy-1/thumbnail',
    },
    { preferOriginal: true },
  );
  assert.deepEqual(fullscreen, {
    baseSrc: '/api/gallery/legacy-1/preview',
    targetSrc: '/api/gallery/legacy-1/download',
    fallbackSrc: '/api/gallery/legacy-1/preview',
  });

  const fallbackModal = resolveMediaSources({ id: 'old-2' });
  assert.deepEqual(fallbackModal, {
    baseSrc: '/api/gallery/old-2/thumbnail',
    targetSrc: '/api/gallery/old-2/preview',
    fallbackSrc: '/api/gallery/old-2/download',
  });
});
