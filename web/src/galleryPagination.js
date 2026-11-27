export const UNASSIGNED_PROJECT = '__none__';

export function buildPageNumbers(currentPage, totalPages) {
  const total = Math.max(1, Number(totalPages) || 1);
  const current = Math.min(total, Math.max(1, Number(currentPage) || 1));
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) pages.push('...');
  for (let i = start; i <= end; i++) pages.push(i);
  if (end < total - 1) pages.push('...');
  pages.push(total);
  return pages;
}

export function normalizeGalleryResponse(payload, fallbackPage = 1, fallbackLimit = 36) {
  if (Array.isArray(payload)) {
    const total = payload.length;
    const limit = Math.max(1, Number(fallbackLimit) || 36);
    const totalPages = Math.max(1, Math.ceil(total / limit));
    const page = Math.min(totalPages, Math.max(1, Number(fallbackPage) || 1));
    return {
      items: payload,
      total,
      page,
      limit,
      totalPages,
      hasMore: page < totalPages,
    };
  }
  const items = Array.isArray(payload?.items) ? payload.items : Array.isArray(payload?.data) ? payload.data : [];
  const limit = Math.max(1, Number(payload?.limit ?? fallbackLimit) || 36);
  const total = Math.max(items.length, Number(payload?.total ?? items.length) || 0);
  const totalPages = Math.max(1, Number(payload?.total_pages ?? payload?.totalPages ?? Math.ceil(total / limit)) || 1);
  const page = Math.min(totalPages, Math.max(1, Number(payload?.page ?? fallbackPage) || 1));
  return {
    items,
    total,
    page,
    limit,
    totalPages,
    hasMore: Boolean(payload?.has_more ?? payload?.hasMore ?? page < totalPages),
  };
}

export function projectLabel(project) {
  if (!project || project === UNASSIGNED_PROJECT) return '未分类';
  return String(project);
}

export function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function resolveMediaSources(item, { preferOriginal = false } = {}) {
  const rawId = item?.id != null ? String(item.id) : '';
  const encId = rawId ? encodeURIComponent(rawId) : '';
  const originalSrc = item?.url || (encId ? `/api/gallery/${encId}/download` : '');
  const thumbSrc = item?.thumbnail_url || (encId ? `/api/gallery/${encId}/thumbnail` : originalSrc);
  const previewSrc = item?.preview_url || (encId ? `/api/gallery/${encId}/preview` : originalSrc);
  if (preferOriginal) {
    return {
      baseSrc: previewSrc || thumbSrc,
      targetSrc: originalSrc || previewSrc,
      fallbackSrc: previewSrc || thumbSrc,
    };
  }
  return {
    baseSrc: thumbSrc,
    targetSrc: previewSrc || originalSrc,
    fallbackSrc: originalSrc,
  };
}
