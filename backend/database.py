"""Small JSON record store backed by SQLite; no ORM or external queue."""
import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


_SLIM_PARAM_KEYS = (
    'prompt', 'negative_prompt', 'model', 'loras', 'width', 'height',
    'steps', 'cfg', 'shift', 'seed', 'sampler', 'scheduler', 'mode',
    'workflow', 'workflow_id', 'duration_seconds', 'video_crf',
    'source_video', 'source_video_id', 'parent_id', 'upscale',
    'project', 'album', 'resolution', 'frame_interp', 'output_fps', 'source',
    'denoise', 'n', 'batch_size', 'keyframe_frames', 'continue_frames',
)

_CARD_KEYS = (
    'id', 'task_id', 'filename', 'local_path', 'local_cached',
    'width', 'height', 'media_type', 'mime_type', 'duration',
    'parent_id', 'favorite', 'tags', 'project', 'album',
    'drive_file_id', 'metadata_file_id', 'drive_parent_id',
    'archive_status', 'archive_error', 'url', 'thumbnail_url', 'preview_url',
    'created_at', 'updated_at',
)


def _with_preview_url(item):
    if isinstance(item, dict) and item.get('id') and not item.get('preview_url'):
        item['preview_url'] = '/api/gallery/' + item['id'] + '/preview'
    return item


def resolve_record_project(record):
    raw = record.get('project')
    if raw is None:
        raw = record.get('album')
    if raw is None:
        raw = (record.get('params') or {}).get('project') or (record.get('params') or {}).get('album')
    return str(raw or '').strip()


def gallery_summary(record):
    """Return a lightweight gallery DTO without giant api_workflow/model_snapshot blobs."""
    project = resolve_record_project(record)
    raw_params = record.get('params') or {}
    slim_params = {}
    if isinstance(raw_params, dict):
        for key in _SLIM_PARAM_KEYS:
            if key in raw_params and raw_params[key] is not None:
                slim_params[key] = raw_params[key]
        if project:
            slim_params['project'] = project
            slim_params.pop('album', None)
    summary = {k: record[k] for k in _CARD_KEYS if k in record}
    summary['project'] = project
    summary['album'] = project
    summary['tags'] = list(record.get('tags') or [])
    summary['favorite'] = bool(record.get('favorite'))
    summary['params'] = slim_params
    return _with_preview_url(summary)


def _gallery_index_row(record):
    project = resolve_record_project(record)
    created_at = str(record.get('created_at') or '')
    updated_at = str(record.get('updated_at') or '')
    media_type = str(record.get('media_type') or 'image')
    favorite = 1 if record.get('favorite') else 0
    archive_status = str(record.get('archive_status') or '')
    drive_file_id = str(record.get('drive_file_id') or '')
    parent_id = str(record.get('parent_id') or '')
    date_prefix = created_at[:10]
    tags = [str(t) for t in (record.get('tags') or []) if t]

    params = record.get('params') or {}
    model_refs = []
    if params.get('model'):
        model_refs.append(str(params['model']))
    for snap in params.get('model_snapshot') or []:
        if isinstance(snap, dict):
            for key in ('id', 'name', 'filename', 'comfy_name'):
                val = snap.get(key)
                if val and str(val) not in model_refs:
                    model_refs.append(str(val))

    lora_refs = []
    for item in params.get('loras') or []:
        if isinstance(item, dict):
            for key in ('id', 'name'):
                val = item.get(key)
                if val and str(val) not in lora_refs:
                    lora_refs.append(str(val))

    search_parts = [
        record.get('id'),
        record.get('filename'),
        project,
        *tags,
        params.get('prompt'),
        params.get('negative_prompt'),
        params.get('model'),
        params.get('workflow'),
        params.get('workflow_id'),
        params.get('seed'),
        *model_refs,
        *lora_refs,
    ]
    search_text = ' '.join(str(x) for x in search_parts if x not in (None, '')).lower()
    summary = gallery_summary(record)
    return (
        record['id'],
        created_at,
        updated_at,
        project,
        media_type,
        favorite,
        archive_status,
        drive_file_id,
        parent_id,
        date_prefix,
        json.dumps(tags, ensure_ascii=False),
        json.dumps(model_refs, ensure_ascii=False),
        json.dumps(lora_refs, ensure_ascii=False),
        search_text,
        json.dumps(summary, ensure_ascii=False),
    )


def _escape_like(value):
    return str(value).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


_TASK_CARD_KEYS = (
    'id', 'status', 'stage', 'progress', 'media_kind', 'error', 'error_details',
    'retryable', 'cancel_requested', 'cancel_error', 'worker_port', 'prompt_id',
    'instance_id', 'gpu', 'actual_gpu', 'current_node', 'node_type', 'node_label',
    'execution_phase', 'sampling_step', 'total_steps', 'model_id', 'model_name',
    'filename', 'download_stage', 'downloaded_bytes', 'total_bytes',
    'download_progress', 'created_at', 'updated_at', 'started_at', 'finished_at',
    'sync_started_at', 'generating_started_at', 'archiving_started_at',
    'sync_seconds', 'gen_seconds', 'total_seconds', 'cu_balance_start',
    'cu_balance_end', 'cu_rate_hourly', 'cu_estimated', 'cu_actual_delta',
)

_TASK_RESULT_KEYS = (
    'id', 'filename', 'width', 'height', 'media_type', 'mime_type', 'duration',
    'project', 'album', 'parent_id', 'url', 'thumbnail_url', 'preview_url',
    'archive_status', 'created_at', 'comfy_output_key',
)

_ACTIVE_TASK_STAGES = {
    'queued', 'queued_sync', 'syncing', 'preparing', 'submitting',
    'running', 'generating', 'archiving', 'downloading', 'verifying',
}
_TERMINAL_TASK_STATUSES = {'completed', 'failed', 'cancelled', 'interrupted', 'error'}


def _safe_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def slim_task_result(item):
    if isinstance(item, str):
        rid = item
        return {
            'id': rid,
            'url': '/api/gallery/' + rid + '/download',
            'thumbnail_url': '/api/gallery/' + rid + '/thumbnail',
            'preview_url': '/api/gallery/' + rid + '/preview',
        }
    if not isinstance(item, dict):
        return item
    out = {k: item[k] for k in _TASK_RESULT_KEYS if k in item}
    rid = out.get('id')
    if rid:
        out.setdefault('url', '/api/gallery/' + str(rid) + '/download')
        out.setdefault('thumbnail_url', '/api/gallery/' + str(rid) + '/thumbnail')
        out.setdefault('preview_url', '/api/gallery/' + str(rid) + '/preview')
    return out


def _classify_task_kind(record):
    p = record.get('params') or {}
    wf = str(p.get('workflow_id') or p.get('workflow') or '').lower()
    mode = str(p.get('mode') or '')
    if mode == 'video_seedvr2' or wf == 'video-seedvr2':
        return 'video_refine'
    first = (record.get('results') or [None])[0]
    first_fname = str(first.get('filename') or '') if isinstance(first, dict) else ''
    first_mtype = first.get('media_type') if isinstance(first, dict) else None
    has_video_node = any(
        isinstance(n, dict)
        and n.get('class_type') in {'MiniMaxH3ReferenceToVideo', 'MiniMaxH3ImageToVideo', 'CreateVideo', 'SaveVideo', 'VHS_VideoCombine'}
        for n in ((record.get('submitted_prompt') or record.get('prompt') or {}).values() if isinstance(record.get('submitted_prompt') or record.get('prompt'), dict) else ())
    )
    if (
        record.get('media_kind') == 'video'
        or first_mtype == 'video'
        or first_fname.lower().endswith(('.mp4', '.webm', '.mov', '.mkv'))
        or wf.startswith('h3-')
        or 'video' in wf
        or p.get('duration_seconds') is not None
        or (p.get('length') is not None and _safe_float(p.get('fps')) == 24.0)
        or has_video_node
    ):
        return 'video'
    if mode in {'seedvr2', 'supir', 'upscale'} or wf == 'seedvr2':
        return 'image_refine'
    return 'image'


def _is_task_active_row(record):
    if record.get('status') in _TERMINAL_TASK_STATUSES:
        return False
    stage = record.get('stage') or record.get('status')
    return stage in _ACTIVE_TASK_STAGES


def task_summary(record, *, live_gallery_ids=None):
    """Return lightweight task DTO without giant workflow graphs/snapshots or full gallery blobs."""
    if not isinstance(record, dict):
        return record
    summary = {k: record[k] for k in _TASK_CARD_KEYS if k in record}
    raw_params = record.get('params') or {}
    slim_params = dict(raw_params) if isinstance(raw_params, dict) else {}
    slim_params.pop('api_workflow', None)
    slim_params.pop('model_snapshot', None)
    slim_params.pop('workflow_hash', None)
    summary['params'] = slim_params

    slim_results = []
    for item in record.get('results') or []:
        rid = item if isinstance(item, str) else (item.get('id') if isinstance(item, dict) else None)
        if live_gallery_ids is not None and rid not in live_gallery_ids:
            continue
        slim_results.append(slim_task_result(item))
    summary['results'] = slim_results

    if not slim_params.get('model'):
        for m in record.get('required_models') or []:
            if isinstance(m, dict) and m.get('kind') in {'checkpoints', 'checkpoint', 'diffusion_models', 'seedvr2'}:
                summary['required_models'] = [{'kind': m.get('kind'), 'name': m.get('name') or m.get('filename')}]
                break

    kind = _classify_task_kind(record)
    if kind in {'video', 'video_refine'}:
        summary['media_kind'] = 'video'
    else:
        summary['media_kind'] = record.get('media_kind') or 'image'
    return summary


def _tasks_index_row(record):
    status = str(record.get('status') or '')
    stage = str(record.get('stage') or '')
    task_kind = _classify_task_kind(record)
    media_kind = 'video' if task_kind in {'video', 'video_refine'} else str(record.get('media_kind') or 'image')
    params = record.get('params') or {}
    model_id = str(params.get('model') or '')
    created_at = str(record.get('created_at') or '')
    finished_at = str(record.get('finished_at') or '')
    sort_ts = str(record.get('updated_at') or finished_at or created_at)
    date_prefix = (finished_at or created_at)[:10]
    is_active = 1 if _is_task_active_row(record) else 0
    actual_cu = _safe_float(record.get('cu_actual_delta'))
    est_cu = _safe_float(record.get('cu_estimated'))
    cu_cost = actual_cu if (actual_cu is not None and actual_cu > 0) else (est_cu if (est_cu is not None and est_cu > 0) else 0.0)
    result_ids = [
        str(r if isinstance(r, str) else r['id'])
        for r in (record.get('results') or [])
        if (isinstance(r, str) and r) or (isinstance(r, dict) and r.get('id'))
    ]
    summary = task_summary(record)
    return (
        record['id'],
        status,
        stage,
        media_kind,
        task_kind,
        model_id,
        created_at,
        finished_at,
        sort_ts,
        date_prefix,
        is_active,
        cu_cost,
        json.dumps(result_ids, ensure_ascii=False),
        json.dumps(summary, ensure_ascii=False),
    )


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')
            self._init_gallery_index(c)
            self._init_tasks_index(c)

    def _init_gallery_index(self, c):
        cols = {r['name'] for r in c.execute('PRAGMA table_info(gallery_index)')}
        expected = {
            'id', 'created_at', 'updated_at', 'project', 'media_type', 'favorite',
            'archive_status', 'drive_file_id', 'parent_id', 'date_prefix',
            'tags_json', 'model_refs_json', 'lora_refs_json', 'search_text', 'summary_json',
        }
        if cols and not expected.issubset(cols):
            c.execute('DROP TABLE gallery_index')
        c.execute('''
            CREATE TABLE IF NOT EXISTS gallery_index (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                project TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT 'image',
                favorite INTEGER NOT NULL DEFAULT 0,
                archive_status TEXT NOT NULL DEFAULT '',
                drive_file_id TEXT NOT NULL DEFAULT '',
                parent_id TEXT NOT NULL DEFAULT '',
                date_prefix TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                model_refs_json TEXT NOT NULL DEFAULT '[]',
                lora_refs_json TEXT NOT NULL DEFAULT '[]',
                search_text TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_gallery_project_created ON gallery_index(project, created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_gallery_created ON gallery_index(created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_gallery_drive_id ON gallery_index(drive_file_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_gallery_parent ON gallery_index(parent_id)')
        total_records = c.execute("SELECT COUNT(*) FROM records WHERE kind='gallery'").fetchone()[0]
        total_indexed = c.execute('SELECT COUNT(*) FROM gallery_index').fetchone()[0]
        missing = c.execute(
            "SELECT COUNT(*) FROM records r WHERE r.kind='gallery' AND NOT EXISTS (SELECT 1 FROM gallery_index g WHERE g.id=r.id)"
        ).fetchone()[0]
        if total_records != total_indexed or missing > 0:
            c.execute("DELETE FROM gallery_index WHERE id NOT IN (SELECT id FROM records WHERE kind='gallery')")
            rows = c.execute("SELECT payload FROM records WHERE kind='gallery'").fetchall()
            for row in rows:
                rec = json.loads(row['payload'])
                self._upsert_gallery_index(c, rec)

    @staticmethod
    def _upsert_gallery_index(c, record):
        c.execute(
            '''
            INSERT INTO gallery_index(
                id, created_at, updated_at, project, media_type, favorite,
                archive_status, drive_file_id, parent_id, date_prefix,
                tags_json, model_refs_json, lora_refs_json, search_text, summary_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                project=excluded.project,
                media_type=excluded.media_type,
                favorite=excluded.favorite,
                archive_status=excluded.archive_status,
                drive_file_id=excluded.drive_file_id,
                parent_id=excluded.parent_id,
                date_prefix=excluded.date_prefix,
                tags_json=excluded.tags_json,
                model_refs_json=excluded.model_refs_json,
                lora_refs_json=excluded.lora_refs_json,
                search_text=excluded.search_text,
                summary_json=excluded.summary_json
            ''',
            _gallery_index_row(record),
        )

    def _init_tasks_index(self, c):
        cols = {r['name'] for r in c.execute('PRAGMA table_info(tasks_index)')}
        expected = {
            'id', 'status', 'stage', 'media_kind', 'task_kind', 'model_id',
            'created_at', 'finished_at', 'sort_ts', 'date_prefix', 'is_active',
            'cu_cost', 'result_ids_json', 'summary_json',
        }
        if cols and not expected.issubset(cols):
            c.execute('DROP TABLE tasks_index')
        c.execute('''
            CREATE TABLE IF NOT EXISTS tasks_index (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                media_kind TEXT NOT NULL DEFAULT 'image',
                task_kind TEXT NOT NULL DEFAULT 'image',
                model_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                sort_ts TEXT NOT NULL DEFAULT '',
                date_prefix TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 0,
                cu_cost REAL NOT NULL DEFAULT 0,
                result_ids_json TEXT NOT NULL DEFAULT '[]',
                summary_json TEXT NOT NULL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_active_sort ON tasks_index(is_active, sort_ts)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status_sort ON tasks_index(status, sort_ts)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_sort ON tasks_index(sort_ts)')
        total_records = c.execute("SELECT COUNT(*) FROM records WHERE kind='tasks'").fetchone()[0]
        total_indexed = c.execute('SELECT COUNT(*) FROM tasks_index').fetchone()[0]
        missing = c.execute(
            "SELECT COUNT(*) FROM records r WHERE r.kind='tasks' AND NOT EXISTS (SELECT 1 FROM tasks_index t WHERE t.id=r.id)"
        ).fetchone()[0]
        if total_records != total_indexed or missing > 0:
            c.execute("DELETE FROM tasks_index WHERE id NOT IN (SELECT id FROM records WHERE kind='tasks')")
            rows = c.execute("SELECT payload FROM records WHERE kind='tasks'").fetchall()
            for row in rows:
                rec = json.loads(row['payload'])
                self._upsert_tasks_index(c, rec)

    @staticmethod
    def _upsert_tasks_index(c, record):
        c.execute(
            '''
            INSERT INTO tasks_index(
                id, status, stage, media_kind, task_kind, model_id,
                created_at, finished_at, sort_ts, date_prefix, is_active,
                cu_cost, result_ids_json, summary_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                stage=excluded.stage,
                media_kind=excluded.media_kind,
                task_kind=excluded.task_kind,
                model_id=excluded.model_id,
                created_at=excluded.created_at,
                finished_at=excluded.finished_at,
                sort_ts=excluded.sort_ts,
                date_prefix=excluded.date_prefix,
                is_active=excluded.is_active,
                cu_cost=excluded.cu_cost,
                result_ids_json=excluded.result_ids_json,
                summary_json=excluded.summary_json
            ''',
            _tasks_index_row(record),
        )

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def list(self, kind):
        with self.lock, self.connect() as c:
            return [json.loads(r['payload']) for r in c.execute('SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC', (kind,))]

    def ids(self, kind):
        with self.lock, self.connect() as c:
            return {r['id'] for r in c.execute('SELECT id FROM records WHERE kind=?', (kind,))}

    def get(self, kind, id):
        with self.lock, self.connect() as c:
            r = c.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, str(id))).fetchone()
            return json.loads(r['payload']) if r else None

    def save(self, kind, record):
        record = dict(record)
        record.setdefault('id', str(uuid.uuid4()))
        with self.lock, self.connect() as c:
            row = c.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, record['id'])).fetchone()
            previous = json.loads(row['payload']) if row else {}
            previous.update(record)
            previous.setdefault('created_at', now())
            previous['updated_at'] = now()
            c.execute(
                'INSERT INTO records(kind,id,payload) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                (kind, previous['id'], json.dumps(previous, ensure_ascii=False)),
            )
            if kind == 'gallery':
                self._upsert_gallery_index(c, previous)
            elif kind == 'tasks':
                self._upsert_tasks_index(c, previous)
            return previous

    def delete(self, kind, id):
        with self.lock, self.connect() as c:
            deleted = c.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, str(id))).rowcount > 0
            if kind == 'gallery':
                c.execute('DELETE FROM gallery_index WHERE id=?', (str(id),))
            elif kind == 'tasks':
                c.execute('DELETE FROM tasks_index WHERE id=?', (str(id),))
            return deleted

    def query_gallery(
        self,
        *,
        q='',
        favorite=False,
        project='',
        media_type='',
        model_ids=None,
        lora='',
        date='',
        tag='',
        order='desc',
        page=None,
        limit=None,
        include_workflow=False,
    ):
        where = ['1=1']
        params = []
        if project == '__none__':
            where.append("project = ''")
        elif project:
            where.append('project = ?')
            params.append(project)
        if favorite:
            where.append('favorite = 1')
        if media_type:
            where.append('media_type = ?')
            params.append(media_type)
        if date:
            where.append("date_prefix = ? OR created_at LIKE ? ESCAPE '\\'")
            # Wrap compound OR safely
            where[-1] = "(date_prefix = ? OR created_at LIKE ? ESCAPE '\\')"
            params.extend([date, _escape_like(date) + '%'])
        if q:
            where.append("search_text LIKE ? ESCAPE '\\'")
            params.append('%' + _escape_like(q.lower()) + '%')

        where_sql = ' AND '.join(where)
        order_sql = 'created_at ASC, id ASC' if order == 'asc' else 'created_at DESC, id DESC'

        with self.lock, self.connect() as c:
            if not tag and not model_ids and not lora:
                total = c.execute(f'SELECT COUNT(*) FROM gallery_index WHERE {where_sql}', params).fetchone()[0]
                sql = f'SELECT id, summary_json FROM gallery_index WHERE {where_sql} ORDER BY {order_sql}'
                sql_params = list(params)
                if limit is not None:
                    page_num = max(1, int(page or 1))
                    page_size = max(1, int(limit))
                    sql += ' LIMIT ? OFFSET ?'
                    sql_params.extend([page_size, (page_num - 1) * page_size])
                rows = c.execute(sql, sql_params).fetchall()
                matched_ids = [r['id'] for r in rows]
                summaries = [_with_preview_url(json.loads(r['summary_json'])) for r in rows]
            else:
                candidates = c.execute(
                    f'SELECT id, tags_json, model_refs_json, lora_refs_json FROM gallery_index WHERE {where_sql} ORDER BY {order_sql}',
                    params,
                ).fetchall()
                filtered_ids = []
                target_models = set(model_ids or ())
                for row in candidates:
                    if tag and tag not in json.loads(row['tags_json']):
                        continue
                    if target_models and not (target_models & set(json.loads(row['model_refs_json']))):
                        continue
                    if lora and lora not in json.loads(row['lora_refs_json']):
                        continue
                    filtered_ids.append(row['id'])
                total = len(filtered_ids)
                if limit is not None:
                    page_num = max(1, int(page or 1))
                    page_size = max(1, int(limit))
                    start = (page_num - 1) * page_size
                    matched_ids = filtered_ids[start:start + page_size]
                else:
                    matched_ids = filtered_ids
                if matched_ids:
                    placeholders = ','.join('?' for _ in matched_ids)
                    by_id = {
                        r['id']: _with_preview_url(json.loads(r['summary_json']))
                        for r in c.execute(f'SELECT id, summary_json FROM gallery_index WHERE id IN ({placeholders})', matched_ids)
                    }
                    summaries = [by_id[mid] for mid in matched_ids if mid in by_id]
                else:
                    summaries = []

            if include_workflow and matched_ids:
                placeholders = ','.join('?' for _ in matched_ids)
                full_by_id = {
                    r['id']: json.loads(r['payload'])
                    for r in c.execute(
                        f"SELECT id, payload FROM records WHERE kind='gallery' AND id IN ({placeholders})",
                        matched_ids,
                    )
                }
                items = []
                for mid in matched_ids:
                    rec = full_by_id.get(mid)
                    if rec:
                        proj = resolve_record_project(rec)
                        rec['project'] = proj
                        rec['album'] = proj
                        items.append(_with_preview_url(rec))
                return {'items': items, 'total': total}

            return {'items': summaries, 'total': total}

    def gallery_project_stats(self):
        with self.lock, self.connect() as c:
            rows = c.execute(
                'SELECT project, COUNT(*) AS cnt, MAX(created_at) AS latest_at FROM gallery_index GROUP BY project'
            ).fetchall()
            total = 0
            unassigned_count = 0
            by_project = {}
            for r in rows:
                cnt = int(r['cnt'] or 0)
                total += cnt
                proj = r['project'] or ''
                if not proj:
                    unassigned_count += cnt
                else:
                    by_project[proj] = {'name': proj, 'count': cnt, 'latest_at': r['latest_at'] or '', 'cover_thumbnail_url': None}
            if by_project:
                cover_rows = c.execute(
                    "SELECT project, id FROM gallery_index WHERE project != '' ORDER BY created_at DESC, id DESC"
                ).fetchall()
                for r in cover_rows:
                    entry = by_project.get(r['project'])
                    if entry and entry['cover_thumbnail_url'] is None:
                        entry['cover_thumbnail_url'] = '/api/gallery/' + r['id'] + '/thumbnail'
            return {'total': total, 'unassigned_count': unassigned_count, 'by_project': by_project}

    def gallery_related(self, image_id, parent_id=None):
        with self.lock, self.connect() as c:
            if parent_id is None:
                row = c.execute('SELECT parent_id FROM gallery_index WHERE id=?', (str(image_id),)).fetchone()
                parent_id = row['parent_id'] if row else ''
            parent_id = str(parent_id or '')
            if parent_id:
                rows = c.execute(
                    '''
                    SELECT summary_json FROM gallery_index
                    WHERE id != ? AND (parent_id = ? OR id = ? OR parent_id = ?)
                    ORDER BY created_at DESC, id DESC
                    LIMIT 24
                    ''',
                    (str(image_id), str(image_id), parent_id, parent_id),
                ).fetchall()
            else:
                rows = c.execute(
                    'SELECT summary_json FROM gallery_index WHERE id != ? AND parent_id = ? ORDER BY created_at DESC, id DESC LIMIT 24',
                    (str(image_id), str(image_id)),
                ).fetchall()
            return [_with_preview_url(json.loads(r['summary_json'])) for r in rows]

    def active_task_count(self):
        with self.lock, self.connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM tasks_index WHERE status IN ('queued', 'running')").fetchone()[0])

    def query_tasks(
        self,
        *,
        status='',
        kind='',
        model='',
        date='',
        page=None,
        limit=None,
        paginated=False,
        include_active=False,
    ):
        use_pagination = paginated or page is not None or limit is not None
        page_num = max(1, int(page or 1)) if use_pagination else None
        page_size = max(1, min(200, int(limit or 20))) if use_pagination else None

        with self.lock, self.connect() as c:
            live_gallery_ids = {r['id'] for r in c.execute("SELECT id FROM records WHERE kind='gallery'")}
            meta_rows = c.execute(
                '''
                SELECT id, status, stage, task_kind, model_id, date_prefix, is_active, cu_cost, result_ids_json
                FROM tasks_index
                ORDER BY is_active DESC, sort_ts DESC, id DESC
                '''
            ).fetchall()

            active_count = 0
            completed_count = 0
            failed_count = 0
            total_cu = 0.0
            has_cu = False
            visible_models = set()
            active_ids = []
            matched_ids = []

            for r in meta_rows:
                row_status = r['status']
                result_ids = json.loads(r['result_ids_json']) if r['result_ids_json'] else []
                has_live_results = any(rid in live_gallery_ids for rid in result_ids)
                if row_status == 'completed' and not has_live_results:
                    continue

                is_active = bool(r['is_active'])
                if is_active:
                    active_count += 1
                    active_ids.append(r['id'])
                if row_status == 'completed':
                    completed_count += 1
                elif row_status == 'failed':
                    failed_count += 1
                cu = float(r['cu_cost'] or 0.0)
                if cu > 0:
                    total_cu += cu
                    has_cu = True
                if r['model_id']:
                    visible_models.add(r['model_id'])

                if status == 'active':
                    if not is_active:
                        continue
                elif status and row_status != status:
                    continue

                tkind = r['task_kind']
                if kind == 'video' and tkind != 'video':
                    continue
                if kind == 'image' and tkind != 'image':
                    continue
                if kind == 'refine' and tkind not in {'video_refine', 'image_refine'}:
                    continue

                if model and r['model_id'] != model:
                    continue
                if date and not str(r['date_prefix'] or '').startswith(date):
                    continue

                matched_ids.append(r['id'])

            total = len(matched_ids)
            if use_pagination:
                total_pages = max(1, math.ceil(total / page_size))
                if page_num > total_pages:
                    page_num = total_pages
                start = (page_num - 1) * page_size
                page_ids = matched_ids[start:start + page_size]
            else:
                page_ids = matched_ids

            fetch_ids = list(dict.fromkeys([*page_ids, *(active_ids if (use_pagination and include_active) else [])]))
            by_id = {}
            if fetch_ids:
                # SQLite host parameter limit guard for very large legacy responses
                for chunk_start in range(0, len(fetch_ids), 800):
                    chunk = fetch_ids[chunk_start:chunk_start + 800]
                    placeholders = ','.join('?' for _ in chunk)
                    for row in c.execute(f'SELECT id, summary_json FROM tasks_index WHERE id IN ({placeholders})', chunk):
                        obj = json.loads(row['summary_json'])
                        if obj.get('results'):
                            obj['results'] = [
                                item for item in obj['results']
                                if (item if isinstance(item, str) else item.get('id')) in live_gallery_ids
                            ]
                        by_id[row['id']] = obj

            items = [by_id[tid] for tid in page_ids if tid in by_id]
            if not use_pagination:
                return items

            payload = {
                'items': items,
                'total': total,
                'page': page_num,
                'limit': page_size,
                'total_pages': total_pages,
                'has_more': page_num < total_pages,
                'stats': {
                    'active': active_count,
                    'completed': completed_count,
                    'failed': failed_count,
                    'total_cu': round(total_cu, 4) if has_cu else 0,
                    'has_cu': has_cu,
                },
                'models': sorted(visible_models),
            }
            if include_active:
                payload['active_items'] = [by_id[tid] for tid in active_ids if tid in by_id]
            return payload
