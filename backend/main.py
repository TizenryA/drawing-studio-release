"""Drawing Studio: independent, single-user ComfyUI control plane."""
import asyncio
import base64
import html
import io
import json
import logging
import mimetypes
import os
import secrets
import time
import uuid
import weakref
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from .database import Database
from .auth import Auth, password_hash
from .gallery import Gallery, normalize_project_name, record_project

mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('video/webm', '.webm')

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('DRAWING_DATA', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
db = Database(DATA / 'studio.sqlite3')
initial = db.get('settings', 'main')
if not initial:
    private = DATA / 'admin-password'
    password = os.environ.get('DRAWING_PASSWORD') or secrets.token_urlsafe(18)
    private.write_text(password)
    private.chmod(0o600)
    db.save('settings', {'id': 'main', 'password_hash': password_hash(password), 'theme': 'system', 'mode': 'temporary', 'comfy_workers': 1, 'idle_minutes': 60, 'drive_root_name': 'DrawingStudio', 'prune_local_after_archive': True})
auth = Auth(db)
listeners = set()
runtime = drive = comfy = workflows = tasks = gallery = oauth_flows = None
background = set()
log = logging.getLogger(__name__)
last_sync_activity = 0.0


def _touch_runtime_activity(at=None, *, sync=False):
    global last_sync_activity
    ts = float(at if at is not None else time.time())
    if sync:
        last_sync_activity = ts if at is not None else max(last_sync_activity, ts)
    if runtime is not None and hasattr(runtime, 'touch_activity'):
        return runtime.touch_activity(at)
    return ts


def _oauth_manager():
    global oauth_flows
    if oauth_flows is None:
        from .oauth_flow import OAuthFlowManager
        oauth_flows = OAuthFlowManager(runtime, drive)
    return oauth_flows


def settings():
    return db.get('settings', 'main') or {}


def emit(event, payload=None):
    message = {'event': event, 'data': payload}
    for queue in tuple(listeners):
        queue.put_nowait(message)


def spawn(coroutine):
    task = asyncio.create_task(coroutine)
    background.add(task)
    def finished(done):
        background.discard(done)
        if not done.cancelled() and done.exception():
            emit('error', {'message': str(done.exception())})
    task.add_done_callback(finished)
    return task


@asynccontextmanager
async def lifespan(app):
    global runtime, drive, comfy, workflows, tasks, gallery, oauth_flows
    from .runtime import Runtime
    from .drive import Drive
    from .comfy import Comfy
    from .workflows import WorkflowStore
    from .tasks import Tasks
    from .oauth_flow import OAuthFlowManager
    runtime = Runtime(settings, emit)
    drive = Drive(settings)
    comfy = Comfy(runtime)
    workflows = WorkflowStore(DATA)
    gallery = Gallery(db, drive, DATA, settings=settings)
    oauth_flows = OAuthFlowManager(runtime, drive)
    tasks = Tasks(db, runtime, comfy, drive, workflows, emit, gallery=gallery, sync_model=sync_model)
    await tasks.start()
    watchdog = spawn(idle_watchdog())
    yield
    watchdog.cancel()
    for task in tuple(sync_tasks.values()):
        task.cancel()
    if sync_tasks:
        await asyncio.gather(*sync_tasks.values(), return_exceptions=True)
    await tasks.stop()
    if gallery and hasattr(gallery, 'close'):
        await gallery.close()
    for task in background:
        task.cancel()


app = FastAPI(title='Drawing Studio', version='1.0.43', lifespan=lifespan)


@app.middleware('http')
async def authenticate(request: Request, call_next):
    protected = request.url.path.startswith(('/api/', '/v1/', '/comfy/'))
    public_paths = {
        '/api/auth/login',
        '/api/auth/status',
        '/api/health',
        '/api/oauth/colab/callback',
        '/api/oauth/drive/callback',
    }
    if protected and request.url.path not in public_paths:
        try:
            request.state.identity = auth.check(request)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    started = time.monotonic()
    response = await call_next(request)
    if request.url.path.startswith('/v1/'):
        db.save('requests', {'path': request.url.path, 'status': response.status_code, 'duration': round(time.monotonic() - started, 3), 'key_id': getattr(request.state, 'identity', '')})
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.exception_handler(ValueError)
async def invalid_value(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def runtime_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=503)


@app.get('/api/health')
async def health():
    return {'ok': True, 'version': '1.0.43'}


@app.get('/api/auth/status')
async def auth_status(request: Request):
    try:
        auth.check(request)
        return {'authenticated': True}
    except HTTPException:
        return {'authenticated': False}


@app.post('/api/auth/login')
async def login(request: Request):
    body = await request.json()
    token = auth.login(body.get('password', ''))
    response = JSONResponse({'ok': True})
    response.set_cookie('drawing_session', token, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=86400 * 30)
    return response


@app.post('/api/auth/logout')
async def logout(request: Request):
    from .auth import digest
    auth.sessions.pop(digest(request.cookies.get('drawing_session', '')), None)
    response = JSONResponse({'ok': True})
    response.delete_cookie('drawing_session')
    return response


PRIVATE_FIELDS = {'password_hash', 'colab_credentials', 'drive_credentials', 'colab_refresh_token', 'drive_refresh_token', 'client_secret', 'proxy_token'}


def _invalidate_task_caches():
    if tasks and hasattr(tasks, 'invalidate_object_info'):
        tasks.invalidate_object_info()


@app.get('/api/settings')
async def get_settings():
    result = {k: v for k, v in settings().items() if k not in PRIVATE_FIELDS and 'token' not in k and 'secret' not in k and 'credential' not in k}
    if runtime and drive:
        st = _oauth_manager().all_status()
        result['drive_configured'] = st['drive']['configured']
        result['colab_configured'] = st['colab']['configured']
        result['drive_account_email'] = st['drive']['account_email']
        result['colab_account_email'] = st['colab']['account_email']
    else:
        result['drive_configured'] = bool(drive and drive.oauth.path.is_file())
        result['colab_configured'] = bool(runtime and runtime.oauth.path.is_file())
        result['drive_account_email'] = ''
        result['colab_account_email'] = ''
    if result.get('prune_local_after_archive') is None:
        result['prune_local_after_archive'] = True
    if result.get('comfy_workers') is None:
        result['comfy_workers'] = 1
    if drive and not result.get('drive_folder_url'):
        try:
            cached = drive._folders or {}
            if cached.get('root'):
                result['drive_folder_url'] = 'https://drive.google.com/drive/folders/' + cached['root']
        except AttributeError:
            pass
    return result


@app.patch('/api/settings')
async def update_settings(request: Request):
    patch = await request.json()
    if patch.get('password'):
        patch['password_hash'] = password_hash(patch.pop('password'))
    patch.pop('password', None)
    if 'comfy_workers' in patch:
        raw_workers = patch['comfy_workers']
        if isinstance(raw_workers, bool):
            raise HTTPException(400, 'ComfyUI 并发数须为 1 或 2')
        try:
            workers = int(raw_workers)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, 'ComfyUI 并发数须为 1 或 2') from exc
        patch['comfy_workers'] = max(1, min(2, workers))
    patch['id'] = 'main'
    db.save('settings', patch)
    if 'comfy_workers' in patch and tasks is not None:
        async with tasks._cond:
            tasks._cond.notify_all()
    if drive and any(k.startswith('drive_') for k in patch):
        if hasattr(drive, 'reset_folder_cache'):
            drive.reset_folder_cache()
        else:
            drive._folders = None
    return await get_settings()


@app.get('/api/keys')
async def list_keys():
    return [{k: v for k, v in r.items() if k != 'hash'} for r in db.list('keys')]


@app.post('/api/keys')
async def create_key(request: Request):
    return auth.key((await request.json()).get('name', ''))


@app.delete('/api/keys/{key_id}')
async def revoke_key(key_id: str):
    db.save('keys', {'id': key_id, 'revoked': True})
    return {'ok': True}


@app.get('/api/requests')
async def request_log():
    return db.list('requests')


@app.get('/api/oauth/status')
async def oauth_status(request: Request):
    return _oauth_manager().all_status(request)


@app.post('/api/oauth/{purpose}/start')
async def oauth_start(purpose: str, request: Request):
    from .oauth_flow import resolve_callback_url
    raw = await request.body()
    body = json.loads(raw) if raw else {}
    return _oauth_manager().start(purpose, body, callback_url=resolve_callback_url(request, purpose))


@app.post('/api/oauth/{purpose}/exchange')
async def oauth_exchange(purpose: str, request: Request):
    body = await request.json()
    return await _oauth_manager().exchange(
        purpose,
        flow_id=body.get('flow_id'),
        state=body.get('state'),
        code_or_url=body.get('code_or_url') or body.get('code') or '',
    )


def _oauth_callback_html(*, purpose: str, ok: bool, account_email: str = '', error: str = '') -> str:
    label = 'Google Colab' if purpose == 'colab' else 'Google Drive'
    title = f'{label} 授权完成' if ok else f'{label} 授权未完成'
    heading = html.escape(title)
    detail = (
        f'已绑定账号：{html.escape(account_email)}。你可以关闭此窗口并返回 Drawing Studio。'
        if ok and account_email
        else '授权凭据已安全保存至服务端，请关闭此窗口返回 Drawing Studio。'
        if ok
        else html.escape(error or '授权失败')
    )
    payload_json = json.dumps(
        {
            'type': 'studio:oauth-complete',
            'purpose': purpose,
            'ok': ok,
            'account_email': account_email,
            'error': error,
        },
        ensure_ascii=False,
    ).replace('<', '\\u003c')
    badge_bg = '#dcfce7' if ok else '#fee2e2'
    badge_fg = '#15803d' if ok else '#b91c1c'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{heading}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f7f9; color: #15171c; padding: 20px; }}
    .card {{ max-width: 460px; width: 100%; background: #fff; border: 1px solid #e2e6ed; border-radius: 14px; padding: 24px; box-shadow: 0 8px 28px rgba(0,0,0,.06); }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: {badge_bg}; color: {badge_fg}; margin-bottom: 12px; }}
    h1 {{ font-size: 18px; margin: 0 0 10px; }}
    p {{ font-size: 14px; line-height: 1.6; color: #4b5563; margin: 0 0 16px; word-break: break-word; }}
    button {{ border: 1px solid #d1d5db; background: #14171f; color: #fff; border-radius: 9px; padding: 8px 14px; font-size: 13px; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="card">
    <span class="badge">{'SUCCESS' if ok else 'ERROR'}</span>
    <h1>{heading}</h1>
    <p>{detail}</p>
    <button type="button" onclick="window.close()">关闭窗口</button>
  </div>
  <script>
    (function () {{
      var msg = {payload_json};
      try {{
        if (window.opener && window.location && window.location.origin) {{
          window.opener.postMessage(msg, window.location.origin);
        }}
      }} catch (e) {{}}
      if (msg.ok) {{
        setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 1200);
      }}
    }})();
  </script>
</body>
</html>"""


@app.get('/api/oauth/{purpose}/callback')
async def oauth_callback(purpose: str, request: Request, code: str = '', state: str = '', error: str = '', error_description: str = ''):
    from .oauth_flow import validate_purpose
    from .runtime import AdapterError
    safe_purpose = purpose
    try:
        safe_purpose = validate_purpose(purpose)
        if error:
            detail = f'{error}: {error_description}' if error_description else error
            raise ValueError(f'Google 授权被拒绝或取消（{detail}）')
        res = await _oauth_manager().exchange(safe_purpose, state=state, code_or_url=code)
        return HTMLResponse(_oauth_callback_html(purpose=safe_purpose, ok=True, account_email=res.get('account_email', '')))
    except (ValueError, AdapterError) as exc:
        return HTMLResponse(_oauth_callback_html(purpose=safe_purpose, ok=False, error=str(exc)), status_code=400)


@app.post('/api/oauth/{purpose}/import')
async def oauth_import(purpose: str, request: Request):
    return await _oauth_manager().import_credentials(purpose, await request.json())


@app.post('/api/oauth/{purpose}/test')
async def oauth_test(purpose: str):
    return await _oauth_manager().test_connection(purpose)


@app.delete('/api/oauth/{purpose}')
async def oauth_disconnect(purpose: str):
    return _oauth_manager().disconnect(purpose)


@app.get('/api/runtime')
async def runtime_status():
    state = await runtime.status()
    state['queue_count'] = db.active_task_count() if hasattr(db, 'active_task_count') else len([t for t in db.list('tasks') if t['status'] in {'queued', 'running'}])
    state['comfy_url'] = '/comfy/' if state.get('state') == 'ready' else None
    return state


@app.get('/api/runtime/gpus')
async def gpu_list():
    return await runtime.gpus()


@app.get('/api/runtime/logs')
async def runtime_logs():
    return (await runtime.status()).get('logs', [])


@app.post('/api/runtime/start', status_code=202)
async def start_runtime(request: Request):
    body = await request.json()
    gpu, mode = body.get('gpu', 'G4'), body.get('mode', settings().get('mode', 'temporary'))
    try:
        accepted = runtime.reserve_start(gpu, mode)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    spawn(runtime.start(gpu, mode, reserved=True))
    return accepted


@app.post('/api/runtime/stop')
async def stop_runtime():
    res = await runtime.stop(reason='手动关机', trigger='manual')
    _invalidate_task_caches()
    return res


@app.post('/api/runtime/reset')
async def reset_runtime():
    res = await runtime.reset()
    _invalidate_task_caches()
    return res


@app.post('/api/runtime/recover')
async def recover_runtime():
    res = await runtime.reset()
    _invalidate_task_caches()
    return res


@app.post('/api/runtime/restart')
async def restart_comfy():
    res = await runtime.restart_comfy()
    _invalidate_task_caches()
    return res


def _has_active_gpu_work():
    has_active = db.active_task_count() > 0 if hasattr(db, 'active_task_count') else any(t['status'] in {'queued', 'running'} for t in db.list('tasks'))
    return has_active or any(lock.locked() for lock in sync_locks.values()) or any(not t.done() for t in sync_tasks.values())


def _effective_runtime_mode(state, config):
    mode = (state or {}).get('mode') or (config or {}).get('mode') or 'temporary'
    return str(mode).strip().lower()


def _compute_idle_anchor(state, *, now=None):
    cur = float(now if now is not None else time.time())
    ready_at = (state or {}).get('ready_at')
    floor = float(ready_at) if ready_at is not None else float((state or {}).get('last_activity') or (state or {}).get('started_at') or cur)
    candidates = [floor]
    for raw in (
        (state or {}).get('last_activity'),
        getattr(runtime, 'last_activity', None),
        getattr(tasks, 'last_activity', None),
        last_sync_activity,
    ):
        if raw is None:
            continue
        val = float(raw)
        if floor <= val <= cur + 5.0:
            candidates.append(val)
    return max(candidates)


async def idle_watchdog_tick(*, now=None):
    if runtime is None:
        return False
    state = await runtime.status()
    config = settings()
    if state.get('state') != 'ready' or _effective_runtime_mode(state, config) == 'persistent':
        return False
    if _has_active_gpu_work():
        return False
    try:
        idle_minutes = float(config.get('idle_minutes', 60))
    except (TypeError, ValueError):
        idle_minutes = 60.0
    threshold_s = max(1.0, idle_minutes) * 60.0
    cur = float(now if now is not None else time.time())
    last = _compute_idle_anchor(state, now=cur)
    idle_s = cur - last
    if idle_s <= threshold_s:
        return False
    # Re-check live queue, syncs, mode, and runtime activity right before stopping to prevent race shutdowns.
    latest_handle = runtime.handle if isinstance(getattr(runtime, 'handle', None), dict) else {}
    latest_state = {**state, **latest_handle}
    if 'last_activity' in state and state.get('last_activity') is not None:
        h_act = latest_handle.get('last_activity')
        s_act = float(state['last_activity'])
        latest_state['last_activity'] = max(s_act, float(h_act)) if h_act is not None else s_act
    latest_config = settings()
    if latest_state.get('state') != 'ready' or _effective_runtime_mode(latest_state, latest_config) == 'persistent':
        return False
    if _has_active_gpu_work():
        return False
    if now is None:
        cur = time.time()
    latest_last = _compute_idle_anchor(latest_state, now=cur)
    idle_s = cur - latest_last
    if idle_s <= threshold_s:
        return False
    idle_min = idle_s / 60.0
    reason = f'空闲超时自动关机（已空闲 {idle_min:.1f} 分钟 > 阈值 {idle_minutes:g} 分钟）'
    log.info('Idle watchdog stopping runtime: %s (mode=%s, ready_at=%s, anchor=%s)',
             reason, _effective_runtime_mode(latest_state, latest_config), latest_state.get('ready_at'), latest_last)
    await runtime.stop(reason=reason, trigger='idle_watchdog', require_ready=True)
    _invalidate_task_caches()
    return True


async def idle_watchdog():
    while True:
        try:
            await asyncio.sleep(30)
            await idle_watchdog_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Idle watchdog tick failed')


@app.get('/api/models')
async def list_models(kind: str = '', q: str = '', favorite: bool = False, status: str = ''):
    rows = db.list('models')
    if kind:
        rows = [r for r in rows if r.get('kind') == kind]
    if q:
        rows = [r for r in rows if q.lower() in json.dumps(r, ensure_ascii=False).lower()]
    if favorite:
        rows = [r for r in rows if r.get('favorite')]
    if status:
        rows = [r for r in rows if r.get('status') == status]
    return rows


@app.post('/api/models/scan')
async def scan_models():
    rows = await drive.scan()
    existing = {r.get('drive_file_id', r['id']): r for r in db.list('models')}
    result = []
    for row in rows:
        file_id = row.get('drive_file_id') or row['id']
        old = existing.get(file_id, {})
        row['id'] = old.get('id', file_id)
        row.setdefault('status', 'drive')
        row['modified_time'] = row.get('modified_time') or row.get('modified_at') or row.get('modifiedTime')
        if old.get('modified_time') == row.get('modified_time') and old.get('size') == row.get('size'):
            row['status'] = old.get('status', row['status'])
        for field in ('favorite', 'tags', 'trigger_words', 'recommended_weight', 'family', 'cover', 'notes', 'defaults'):
            if field in old:
                row[field] = old[field]
        result.append(db.save('models', row))
    emit('models', result)
    return result


@app.patch('/api/models/{model_id}')
async def update_model(model_id: str, request: Request):
    if not db.get('models', model_id):
        raise HTTPException(404, '模型不存在')
    patch = await request.json()
    patch['id'] = model_id
    return db.save('models', patch)


MODEL_SYNC_CONCURRENCY = 2
sync_locks = {}
sync_tasks = {}
_sync_semaphores = weakref.WeakKeyDictionary()


def _sync_semaphore():
    loop = asyncio.get_running_loop()
    sem = _sync_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MODEL_SYNC_CONCURRENCY)
        _sync_semaphores[loop] = sem
    return sem


async def sync_model(model_or_id, on_progress=None, *, use_semaphore=False):
    import inspect
    from contextlib import nullcontext
    from .tasks import TaskCancelled
    row = model_or_id if isinstance(model_or_id, dict) else db.get('models', model_or_id)
    if not row:
        raise RuntimeError('模型不在 Drive 索引中，请先扫描')
    model_id = row['id']
    sem = _sync_semaphore() if use_semaphore else None
    lock = sync_locks.setdefault(model_id, asyncio.Lock())
    if sem is not None and sem.locked() and not lock.locked():
        queued_row = db.save('models', {
            'id': model_id, 'status': 'queued_sync', 'stage': 'queued_sync', 'error': None,
            'filename': row.get('filename') or row.get('name'),
            'downloaded_bytes': 0, 'total_bytes': int(row.get('size') or 0), 'download_progress': None,
        })
        emit('models', queued_row)
    guard = sem if sem is not None else nullcontext()
    async with guard:
        # A queued task can observe an already-running manual sync without issuing
        # another GPU job or losing cancellation while waiting for the owner.
        while lock.locked():
            if on_progress:
                value = on_progress(db.get('models', model_id) or row)
                if inspect.isawaitable(value):
                    await value
            await asyncio.sleep(1)
        async with lock:
            async def report(progress):
                _touch_runtime_activity(sync=True)
                saved = db.save('models', {'id': model_id, **progress})
                emit('models', saved)
                if on_progress:
                    value = on_progress(saved)
                    if inspect.isawaitable(value):
                        await value

            try:
                _touch_runtime_activity(sync=True)
                await report({'status': 'downloading', 'stage': 'preparing', 'error': None,
                              'filename': row.get('filename') or row.get('name'),
                              'downloaded_bytes': 0, 'total_bytes': int(row.get('size') or 0),
                              'download_progress': None})
                result = await comfy.sync_model(row, drive, on_progress=report)
                _touch_runtime_activity(sync=True)
                await report({'status': 'ready', 'stage': 'ready',
                              'gpu_path': result.get('path', row.get('path')), 'error': None})
                _invalidate_task_caches()
                return db.get('models', model_id)
            except (asyncio.CancelledError, TaskCancelled):
                saved = db.save('models', {'id': model_id, 'status': 'cancelled', 'stage': 'cancelled',
                                          'error': '模型同步已取消，保留.part；远端job结束前不会重复下载'})
                emit('models', saved)
                raise
            except Exception as exc:
                await report({'status': 'error', 'stage': 'error', 'error': str(exc)})
                raise


def _start_background_sync(model_id):
    row = db.get('models', model_id)
    if not row:
        raise HTTPException(404, '模型不存在')
    existing = sync_tasks.get(model_id)
    if existing and not existing.done():
        return row
    saved = db.save('models', {
        'id': model_id, 'status': 'queued_sync', 'stage': 'queued_sync', 'error': None,
        'filename': row.get('filename') or row.get('name'),
        'downloaded_bytes': 0, 'total_bytes': int(row.get('size') or 0), 'download_progress': None,
    })
    emit('models', saved)

    async def runner():
        try:
            await sync_model(model_id, use_semaphore=True)
        except asyncio.CancelledError:
            cur = db.get('models', model_id)
            if cur and cur.get('status') in {'queued_sync', 'downloading', 'verifying'}:
                cancelled = db.save('models', {
                    'id': model_id, 'status': 'cancelled', 'stage': 'cancelled',
                    'error': '模型同步已取消，保留.part；远端job结束前不会重复下载',
                })
                emit('models', cancelled)
        except Exception:
            pass
        finally:
            if sync_tasks.get(model_id) is asyncio.current_task():
                sync_tasks.pop(model_id, None)

    _touch_runtime_activity(sync=True)
    sync_tasks[model_id] = asyncio.create_task(runner(), name=f'model-sync-{model_id[:8]}')
    return saved


@app.post('/api/models/{model_id}/sync')
async def sync_model_route(model_id: str, wait: bool = False):
    if not db.get('models', model_id):
        raise HTTPException(404, '模型不存在')
    if runtime is None or (await runtime.status()).get('state') != 'ready':
        raise HTTPException(503, 'GPU/ComfyUI 未就绪，请先开机后再同步模型')
    if wait:
        return await sync_model(model_id, use_semaphore=True)
    return JSONResponse(_start_background_sync(model_id), status_code=202)


@app.post('/api/models/sync-batch')
async def sync_models_batch(request: Request):
    body = await request.json()
    ids = [str(x) for x in (body.get('ids') or []) if x]
    if not ids:
        raise HTTPException(400, '请选择至少 1 个模型进行同步')
    if runtime is None or (await runtime.status()).get('state') != 'ready':
        raise HTTPException(503, 'GPU/ComfyUI 未就绪，请先开机后再同步模型')
    started, skipped, rows = [], [], []
    seen = set()
    for model_id in ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        if not db.get('models', model_id):
            skipped.append(model_id)
            continue
        rows.append(_start_background_sync(model_id))
        started.append(model_id)
    return JSONResponse({'started': started, 'skipped': skipped, 'concurrency': MODEL_SYNC_CONCURRENCY, 'models': rows}, status_code=202)


@app.post('/api/models/{model_id}/sync/cancel')
async def cancel_model_sync(model_id: str):
    row = db.get('models', model_id)
    if not row:
        raise HTTPException(404, '模型不存在')
    task = sync_tasks.get(model_id)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    cur = db.get('models', model_id) or row
    if cur.get('status') in {'queued_sync', 'downloading', 'verifying'}:
        cur = db.save('models', {
            'id': model_id, 'status': 'cancelled', 'stage': 'cancelled',
            'error': '模型同步已取消，保留.part；远端job结束前不会重复下载',
        })
        emit('models', cur)
    return cur


@app.delete('/api/models/{model_id}/cache')
async def remove_cache(model_id: str):
    model = db.get('models', model_id)
    if not model:
        raise HTTPException(404, '模型不存在')
    await comfy.remove_model(model)
    _invalidate_task_caches()
    return db.save('models', {'id': model_id, 'status': 'drive', 'gpu_path': None})


@app.delete('/api/models/{model_id}')
async def remove_model(model_id: str):
    db.delete('models', model_id)
    return {'ok': True}


@app.get('/api/presets')
async def presets():
    return db.list('presets')


@app.post('/api/presets')
async def save_preset(request: Request):
    return db.save('presets', await request.json())


@app.delete('/api/presets/{preset_id}')
async def remove_preset(preset_id: str):
    return {'ok': db.delete('presets', preset_id)}


@app.get('/api/workflows')
async def list_workflows():
    return workflows.list()


@app.get('/api/workflows/{workflow_id}')
async def get_workflow(workflow_id: str):
    result = workflows.get(workflow_id)
    if not result:
        raise HTTPException(404, '工作流不存在')
    return result


@app.post('/api/workflows')
async def save_workflow(request: Request):
    body = await request.json()
    raw = body.get('workflow') or body.get('prompt') or {}
    info = await comfy.request('GET', '/object_info') if isinstance(raw, dict) and isinstance(raw.get('nodes'), list) else None
    return workflows.save(body, object_info=info)


@app.delete('/api/workflows/{workflow_id}')
async def delete_workflow(workflow_id: str):
    return {'ok': workflows.delete(workflow_id)}


@app.get('/api/tasks')
async def task_list(
    status: str = '',
    kind: str = '',
    model: str = '',
    date: str = '',
    page: int | None = None,
    limit: int | None = None,
    paginated: bool = False,
    include_active: bool = False,
):
    if hasattr(db, 'query_tasks'):
        return db.query_tasks(
            status=status,
            kind=kind,
            model=model,
            date=date,
            page=page,
            limit=limit,
            paginated=paginated,
            include_active=include_active,
        )
    rows = db.list('tasks')
    images = db.ids('gallery') if hasattr(db, 'ids') else {r['id'] for r in db.list('gallery')}
    visible = []
    for row in rows:
        results = [r for r in row.get('results', []) if (r if isinstance(r, str) else r.get('id')) in images]
        if row.get('status') == 'completed' and not results:
            continue
        visible.append({**row, 'results': results})
    return visible


@app.post('/api/tasks', status_code=202)
async def submit_task(request: Request):
    return await tasks.submit(await request.json())


@app.get('/api/tasks/{task_id}')
async def task_status(task_id: str):
    row = db.get('tasks', task_id)
    if not row:
        raise HTTPException(404, '任务不存在')
    return row


@app.post('/api/tasks/{task_id}/cancel')
async def cancel_task(task_id: str):
    return await tasks.cancel(task_id)


@app.post('/api/uploads')
async def upload_image(file: UploadFile = File(...)):
    content = await file.read()
    suffix = Path(file.filename or '').suffix.lower()
    is_video_file = suffix in {'.mp4', '.webm', '.mov', '.mkv'} or (file.content_type or '').startswith('video/')
    if is_video_file:
        if not content:
            raise HTTPException(400, '视频文件为空')
        if suffix not in {'.mp4', '.webm', '.mov', '.mkv'}:
            suffix = '.mp4'
    else:
        from PIL import Image
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception:
            raise HTTPException(400, '无法读取图片')
        if not suffix:
            suffix = '.png'
    folder = DATA / 'uploads'
    folder.mkdir(exist_ok=True)
    filename = uuid.uuid4().hex + suffix
    path = folder / filename
    path.write_bytes(content)
    return {'id': filename, 'path': filename, 'url': '/api/uploads/' + filename, 'media_type': 'video' if is_video_file else 'image'}


@app.get('/api/uploads/{filename}')
async def uploaded_file(filename: str):
    path = DATA / 'uploads' / Path(filename).name
    if not path.is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(path)


@app.get('/api/gallery')
async def list_gallery(
    q: str = '',
    favorite: bool = False,
    album: str = '',
    project: str = '',
    model: str = '',
    lora: str = '',
    media_type: str = '',
    date: str = '',
    tag: str = '',
    order: str = 'desc',
    page: int | None = None,
    limit: int | None = None,
    paginated: bool = False,
    include_workflow: bool = False,
):
    models = db.list('models')
    return gallery.filtered(
        q, favorite, album, model, date, tag,
        order=order, models=models, project=project, media_type=media_type,
        lora=lora, page=page, limit=limit, paginated=paginated, include_workflow=include_workflow,
    )


@app.get('/api/gallery/projects')
async def list_gallery_projects():
    return gallery.list_projects()


@app.post('/api/gallery/projects', status_code=201)
async def create_gallery_project(request: Request):
    body = await request.json()
    return await gallery.create_project((body or {}).get('name', ''))


@app.patch('/api/gallery/projects/{project_name}')
async def rename_gallery_project(project_name: str, request: Request):
    body = await request.json()
    try:
        return await gallery.rename_project(project_name, (body or {}).get('name', ''))
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0]) if exc.args else '项目不存在') from None


@app.delete('/api/gallery/projects/{project_name}')
async def delete_gallery_project(project_name: str, unassign: bool = False):
    try:
        return await gallery.delete_project(project_name, unassign=unassign)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0]) if exc.args else '项目不存在') from None


@app.post('/api/gallery/sync')
async def sync_gallery_drive(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await gallery.sync_from_drive(project=(body or {}).get('project'))


@app.post('/api/gallery/prune-local')
async def prune_gallery_local():
    return await gallery.prune_local_originals()


@app.post('/api/gallery/import')
async def import_image(request: Request):
    form = await request.form()
    file = form.get('file') or form.get('image')
    if not file:
        raise HTTPException(400, '请选择图片或视频')
    params = json.loads(str(form.get('metadata', '{}')))
    raw_project = form.get('project') if form.get('project') is not None else form.get('album')
    project_name = normalize_project_name(str(raw_project)) if raw_project is not None else None
    if project_name is not None:
        if project_name:
            params['project'] = project_name
        else:
            params.pop('project', None)
    return await gallery.archive('import', await file.read(), file.filename, params, project=project_name)


@app.post('/api/gallery/batch')
async def gallery_batch(request: Request):
    body = await request.json()
    ids = body.get('ids', [])
    action = body.get('action')
    if action == 'download':
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as z:
            for image_id in ids:
                row = db.get('gallery', image_id)
                if row:
                    path = await image_path(row)
                    z.write(path, row['filename'])
                    z.writestr(image_id + '.json', json.dumps(row.get('params') or {}, ensure_ascii=False))
        return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="drawing-studio.zip"'})
    if action in {'project', 'album'}:
        target_project = normalize_project_name(body.get('value', ''))
        for image_id in ids:
            try:
                await gallery.move_to_project(image_id, target_project)
            except KeyError:
                continue
        return {'ok': True, 'project': target_project}
    for image_id in ids:
        if action == 'delete':
            await delete_image(image_id)
        elif action in {'tag', 'favorite'}:
            row = db.get('gallery', image_id)
            if row:
                field = 'tags' if action == 'tag' else action
                db.save('gallery', {'id': image_id, field: body.get('value')})
    return {'ok': True}


@app.post('/api/gallery/concat')
async def concat_gallery_videos(request: Request):
    from .workflows import parse_video_crf
    body = await request.json()
    ids = body.get('ids') or []
    if not isinstance(ids, list) or len(ids) < 2:
        raise HTTPException(400, '请按顺序选择至少 2 段视频进行拼接')
    trim = int(body.get('trim_overlap_frames') or 0)
    crf = parse_video_crf(body.get('video_crf'), default=15)
    rows, paths = [], []
    for video_id in ids:
        row = await image_info(video_id)
        if row.get('media_type') != 'video':
            raise HTTPException(400, f'作品 {video_id} 不是视频，无法拼接')
        rows.append(row)
        paths.append(await image_path(row))
    tmp_dir = DATA / 'uploads'
    tmp_dir.mkdir(exist_ok=True)
    tmp_out = tmp_dir / f'concat-{uuid.uuid4().hex}.mp4'
    try:
        await asyncio.to_thread(gallery.concat_videos, paths, tmp_out, trim, crf)
        content = await asyncio.to_thread(tmp_out.read_bytes)
    finally:
        tmp_out.unlink(missing_ok=True)
    first_params = rows[0].get('params') or {}
    if 'project' in body or 'album' in body:
        project_name = normalize_project_name(body.get('project') if 'project' in body else body.get('album'))
    else:
        project_name = record_project(rows[0])
    metadata = {
        'mode': 'concat',
        'workflow': 'h3-concat',
        'workflow_id': 'h3-concat',
        'prompt': body.get('title') or f"拼接视频（{len(rows)} 段 · 裁剪重叠 {trim} 帧）",
        'concat_source_ids': ids,
        'trim_overlap_frames': trim,
        'video_crf': crf,
        'model': first_params.get('model'),
    }
    if project_name:
        metadata['project'] = project_name
    return await gallery.archive('concat', content, f'concat-{len(rows)}clips.mp4', metadata, parent_id=ids[0], project=project_name)


MEDIA_CACHE_HEADERS = {'Cache-Control': 'private, max-age=31536000, immutable'}


def _require_gallery_row(image_id: str):
    row = db.get('gallery', image_id)
    if not row:
        raise HTTPException(404, '作品不存在')
    return row


@app.get('/api/gallery/{image_id}')
async def image_info(image_id: str):
    row = _require_gallery_row(image_id)
    project = record_project(row)
    related = db.gallery_related(image_id, row.get('parent_id')) if hasattr(db, 'gallery_related') else []
    preview_url = row.get('preview_url') or ('/api/gallery/' + image_id + '/preview')
    return {**row, 'project': project, 'album': project, 'preview_url': preview_url, 'related': related}


@app.patch('/api/gallery/{image_id}')
async def update_image(image_id: str, request: Request):
    _require_gallery_row(image_id)
    body = await request.json()
    if 'project' in body or 'album' in body:
        target_project = body['project'] if 'project' in body else body['album']
        try:
            await gallery.move_to_project(image_id, target_project)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]) if exc.args else '作品不存在') from None
    updates = {k: v for k, v in body.items() if k in {'favorite', 'tags'}}
    saved = db.save('gallery', {'id': image_id, **updates}) if updates else db.get('gallery', image_id)
    project = record_project(saved)
    return {**saved, 'project': project, 'album': project}


async def image_path(row, *, trim=True):
    if gallery and hasattr(gallery, 'ensure_local_file'):
        try:
            return await gallery.ensure_local_file(row, trim=trim)
        except FileNotFoundError:
            raise HTTPException(404, '原图不可用') from None
    path = Path(row['local_path'])
    if not path.is_file() and row.get('drive_file_id'):
        await drive.download(row['drive_file_id'], path)
    if not path.is_file():
        raise HTTPException(404, '原图不可用')
    return path


@app.get('/api/gallery/{image_id}/download')
async def download_image(image_id: str):
    row = _require_gallery_row(image_id)
    return FileResponse(
        await image_path(row),
        filename=row['filename'],
        media_type=row.get('mime_type') or mimetypes.guess_type(row['filename'])[0],
        headers=MEDIA_CACHE_HEADERS,
    )


@app.get('/api/gallery/{image_id}/media')
async def preview_media(image_id: str):
    row = _require_gallery_row(image_id)
    return FileResponse(
        await image_path(row),
        media_type=row.get('mime_type') or mimetypes.guess_type(row['filename'])[0],
        headers=MEDIA_CACHE_HEADERS,
    )


@app.get('/api/gallery/{image_id}/preview')
async def image_hd_preview(image_id: str):
    row = _require_gallery_row(image_id)
    if row.get('media_type') == 'video':
        return FileResponse(
            await image_path(row),
            media_type=row.get('mime_type') or mimetypes.guess_type(row['filename'])[0],
            headers=MEDIA_CACHE_HEADERS,
        )
    path = DATA / 'previews' / (image_id + '.webp')
    if path.is_file():
        return FileResponse(path, media_type='image/webp', headers=MEDIA_CACHE_HEADERS)
    if gallery and hasattr(gallery, 'ensure_preview'):
        try:
            built = await gallery.ensure_preview(row)
        except FileNotFoundError:
            raise HTTPException(404, '原图不可用') from None
        if built and built.is_file():
            return FileResponse(built, media_type='image/webp', headers=MEDIA_CACHE_HEADERS)
    return FileResponse(await image_path(row), headers=MEDIA_CACHE_HEADERS)


@app.get('/api/gallery/{image_id}/thumbnail')
async def image_thumbnail(image_id: str):
    row = _require_gallery_row(image_id)
    path = DATA / 'thumbnails' / (image_id + '.webp')
    if path.is_file():
        return FileResponse(path, media_type='image/webp', headers=MEDIA_CACHE_HEADERS)
    if gallery and hasattr(gallery, 'ensure_thumbnail'):
        try:
            built = await gallery.ensure_thumbnail(row)
        except FileNotFoundError:
            raise HTTPException(404, '原图不可用') from None
        if built and built.is_file():
            return FileResponse(built, media_type='image/webp', headers=MEDIA_CACHE_HEADERS)
    elif row.get('media_type') == 'video':
        source = await image_path(row, trim=False)
        await asyncio.to_thread(gallery.video_preview, source, path)
        if path.is_file():
            return FileResponse(path, media_type='image/webp', headers=MEDIA_CACHE_HEADERS)
    return FileResponse(await image_path(row), headers=MEDIA_CACHE_HEADERS)


@app.post('/api/gallery/{image_id}/extract-frame')
async def extract_gallery_frame(image_id: str, request: Request):
    row = _require_gallery_row(image_id)
    if row.get('media_type') != 'video':
        raise HTTPException(400, '只有视频作品支持抽帧')
    try:
        body = await request.json()
    except Exception:
        body = {}
    position = (body or {}).get('position', 'last')
    source = await image_path(row)
    folder = DATA / 'uploads'
    folder.mkdir(exist_ok=True)
    filename = f'frame-{image_id[:8]}-{position}-{uuid.uuid4().hex[:8]}.png'
    dest = folder / filename
    await asyncio.to_thread(gallery.extract_frame, source, dest, position)
    return {'id': filename, 'path': filename, 'url': '/api/uploads/' + filename, 'position': position, 'source_video_id': image_id}


@app.post('/api/gallery/{image_id}/archive')
async def retry_archive(image_id: str):
    return await gallery.retry_archive(_require_gallery_row(image_id))


@app.delete('/api/gallery/{image_id}')
async def delete_image(image_id: str):
    row = _require_gallery_row(image_id)
    if row.get('drive_file_id') and drive:
        await drive.trash(row['drive_file_id'])
    if row.get('metadata_file_id') and drive:
        await drive.trash(row['metadata_file_id'])
    if row.get('local_path'):
        Path(row['local_path']).unlink(missing_ok=True)
    (DATA / 'images' / (image_id + '.json')).unlink(missing_ok=True)
    (DATA / 'thumbnails' / (image_id + '.webp')).unlink(missing_ok=True)
    (DATA / 'previews' / (image_id + '.webp')).unlink(missing_ok=True)
    db.delete('gallery', image_id)
    gallery.detach_output(row)
    return {'ok': True}


@app.get('/api/events')
async def events(request: Request):
    queue = asyncio.Queue()
    listeners.add(queue)
    async def stream():
        try:
            yield ': connected\n\n'
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), 20)
                    yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            listeners.discard(queue)
    return StreamingResponse(
        stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'},
    )


@app.get('/v1/models')
async def openai_models():
    return {'object': 'list', 'data': [{'id': r['id'], 'object': 'model', 'created': int(time.time()), 'owned_by': 'drawing-studio'} for r in db.list('models') if r.get('kind') in {'checkpoint', 'checkpoints'}]}


def api_error(message, code, status):
    return JSONResponse({'error': {'message': message, 'type': 'invalid_request_error', 'code': code}}, status_code=status)


async def image_generation(body, request):
    if (await runtime.status()).get('state') != 'ready':
        return api_error('GPU 离线，请在机器页开机并等待就绪', 'gpu_not_ready', 503)
    params = dict(body.get('comfy') or {})
    params.update({k: body[k] for k in ('model', 'prompt', 'n', 'size') if k in body})
    for k in ('input_image', 'mask', 'mode', 'project', 'album'):
        if body.get(k):
            params[k] = body[k]
    task = await tasks.submit(params)
    while task['status'] in {'queued', 'running'}:
        await asyncio.sleep(1)
        task = db.get('tasks', task['id'])
    if task['status'] != 'completed':
        return api_error(task.get('error') or task['status'], 'generation_failed', 500)
    data = []
    for result in task.get('results', []):
        row = db.get('gallery', result if isinstance(result, str) else result['id'])
        path = await image_path(row)
        # URL responses retain the same authentication as the private gallery.
        if body.get('response_format') == 'url':
            data.append({'url': str(request.base_url).rstrip('/') + row['url']})
        else:
            data.append({'b64_json': base64.b64encode(path.read_bytes()).decode()})
    return {'created': int(time.time()), 'data': data}


@app.post('/v1/images/generations')
async def openai_generate(request: Request):
    return await image_generation(await request.json(), request)


@app.post('/v1/images/edits')
async def openai_edit(request: Request):
    if 'application/json' in request.headers.get('content-type', ''):
        body = await request.json()
    else:
        form = await request.form()
        body = {k: str(v) for k, v in form.items() if not hasattr(v, 'read')}
        body['comfy'] = json.loads(body.get('comfy', '{}'))
        file = form.get('image') or form.get('image[]')
        if not file:
            return api_error('缺少 image', 'missing_image', 400)
        body['input_image'] = (await upload_image(file))['path']
        mask = form.get('mask')
        if mask:
            body['mask'] = (await upload_image(mask))['path']
        if 'n' in body:
            body['n'] = int(body['n'])
    body['mode'] = 'inpaint' if body.get('mask') else 'img2img'
    return await image_generation(body, request)


@app.api_route('/comfy/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
async def comfy_proxy(path: str, request: Request):
    _touch_runtime_activity()
    res = await comfy.proxy(path, request)
    _touch_runtime_activity()
    return res


@app.websocket('/comfy/ws')
async def comfy_websocket(websocket: WebSocket):
    try:
        auth.check(websocket)
    except HTTPException:
        await websocket.close(code=4401)
        return
    _touch_runtime_activity()
    await comfy.websocket_proxy(websocket)


DIST = ROOT / 'web' / 'dist'
if DIST.is_dir():
    from starlette.middleware.base import BaseHTTPMiddleware
    class NoCacheStaticMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith('/assets/') or request.url.path in {'/', '/index.html'}:
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
            return response
    app.add_middleware(NoCacheStaticMiddleware)
    app.mount('/', StaticFiles(directory=DIST, html=True), name='web')
