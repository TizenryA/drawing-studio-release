"""ComfyUI HTTP, files and native WebSocket via the authenticated Colab kernel."""
from __future__ import annotations

import asyncio
import base64
import json as jsonlib
import inspect
import mimetypes
import os
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import urlencode, urlsplit

from .runtime import AdapterError
from .drive import file_id, KINDS

PREFIX = 'DRAWING_RESULT:'
COMFY = '/content/DrawingStudio/ComfyUI'
ALLOWED_PORTS = (8188, 8189, 8190, 8191)

# Runs in the authenticated kernel. No HTTP port or public tunnel is opened.
HTTP_CODE = '''
import base64 as _b64, json as _json, requests as _requests
_d = _json.loads(_b64.b64decode(PAYLOAD))
_p = int(_d.get('port') or 8188)
assert 8188 <= _p <= 8191
_u = f'http://127.0.0.1:{_p}/' + _d['path'].lstrip('/')
_h = _d.get('headers') or {}
_r = _requests.request(_d['method'], _u, params=_d.get('params'), json=_d.get('json'),
    data=_b64.b64decode(_d['data']) if _d.get('data') is not None else None,
    headers=_h, timeout=_d['timeout'], allow_redirects=False)
print('DRAWING_RESULT:' + _json.dumps({'status': _r.status_code,
    'headers': {k:v for k,v in _r.headers.items() if k.lower() in ('content-type','content-disposition','cache-control','etag','last-modified','location')},
    'body': _b64.b64encode(_r.content).decode()}))
'''

WS_INIT = '''
import threading as _threading, queue as _queue, json as _json, base64 as _b64
import websocket as _websocket
if '_drawing_ws' not in globals():
    _drawing_ws = {}
_d = _json.loads(_b64.b64decode(PAYLOAD))
_p = int(_d.get('port') or 8188)
assert 8188 <= _p <= 8191
_socket = _websocket.create_connection(f'ws://127.0.0.1:{_p}/ws?' + _d['query'], timeout=20)
_socket.settimeout(1)
_entry = {'socket': _socket, 'queue': _queue.Queue(), 'closed': False}
_drawing_ws[_d['id']] = _entry
def _drawing_read(entry):
    try:
        while not entry['closed']:
            try:
                opcode, frame = entry['socket'].recv_data(control_frame=True)
            except _websocket.WebSocketTimeoutException:
                continue
            if opcode == 8:
                break
            if opcode == 1:
                entry['queue'].put({'text': frame.decode() if isinstance(frame,bytes) else frame})
            elif opcode == 2:
                entry['queue'].put({'bytes': _b64.b64encode(frame).decode()})
    except Exception:
        pass
    finally:
        entry['closed'] = True
        entry['socket'].close()
_threading.Thread(target=_drawing_read,args=(_entry,),daemon=True).start()
print('DRAWING_RESULT:' + _json.dumps({'ok':True}))
'''

WS_POLL = '''
import json as _json,base64 as _b64,queue as _queue
_d = _json.loads(_b64.b64decode(PAYLOAD))
_e = _drawing_ws[_d['id']]
for _m in _d.get('send',[]):
    if 'text' in _m:
        _e['socket'].send(_m['text'])
    elif 'bytes' in _m:
        _e['socket'].send_binary(_b64.b64decode(_m['bytes']))
_messages = []
while True:
    try:
        _messages.append(_e['queue'].get_nowait())
    except _queue.Empty:
        break
print('DRAWING_RESULT:' + _json.dumps({'messages':_messages,'closed':_e['closed']}))
'''

# Each worker and each control cell has its own globals; only jobs are shared.
SYNC_WORKER = '''
import time
class DownloadCancelled(Exception):
    pass
def run(_d, entry):
    def check():
        if entry['cancel'].is_set():
            raise DownloadCancelled('模型同步已取消，保留.part供重试')
        if time.monotonic() - entry['lease'] > 300:
            raise DownloadCancelled('模型同步控制连接中断，保留.part供重试')
    def update(**values):
        with entry['lock']:
            entry['progress'].update(values)
    try:
        check()
        import json as _json,requests as _requests,hashlib as _hashlib
        from pathlib import Path as _Path
        _p = _Path(_d['path'])
        _p.parent.mkdir(parents=True,exist_ok=True)
        _part = _p.with_name(_p.name+'.part')
        _marker = _p.with_name(_p.name+'.drive.json')
        _fingerprint = {k:_d.get(k) for k in ('file_id','size','modified_at','md5')}
        def _drawing_md5(p):
            h = _hashlib.md5()
            with p.open('rb') as f:
                while chunk := f.read(8*1024*1024):
                    check()
                    h.update(chunk)
            return h.hexdigest()
        _cached = _p.exists() and _marker.exists() and _json.loads(_marker.read_text()) == _fingerprint
        if not _cached:
            _partmeta = _part.with_name(_part.name+'.json')
            if not _partmeta.exists() or _json.loads(_partmeta.read_text()) != _fingerprint:
                _part.unlink(missing_ok=True)
            _partmeta.write_text(_json.dumps(_fingerprint))
            update(stage='downloading', downloaded_bytes=_part.stat().st_size if _part.exists() else 0)
            for _attempt in range(4):
                check()
                _offset = _part.stat().st_size if _part.exists() else 0
                if _d.get('size') and _offset == int(_d['size']):
                    break
                _headers = {'Authorization':'Bearer '+_d['token']}
                if _offset: _headers['Range'] = 'bytes='+str(_offset)+'-'
                try:
                    with _requests.get('https://www.googleapis.com/drive/v3/files/'+_d['file_id'], params={'alt':'media','supportsAllDrives':'true'}, headers=_headers,stream=True,timeout=(30,180)) as _r:
                        if _r.status_code not in (200,206):
                            raise RuntimeError('Drive模型下载HTTP '+str(_r.status_code))
                        if _r.status_code == 206 and not _r.headers.get('content-range','').startswith('bytes '+str(_offset)+'-'):
                            raise RuntimeError('Drive模型断点偏移不符')
                        check()
                        if not _d.get('size'):
                            try:
                                total = int(_r.headers.get('content-range','').rsplit('/',1)[-1]) if _r.status_code == 206 else int(_r.headers.get('content-length') or 0)
                                update(total_bytes=total)
                            except ValueError:
                                pass
                        with _part.open('ab' if _r.status_code==206 and _offset else 'wb') as _f:
                            update(downloaded_bytes=_part.stat().st_size)
                            for _chunk in _r.iter_content(8*1024*1024):
                                check()
                                _f.write(_chunk)
                                _f.flush()
                                update(downloaded_bytes=_part.stat().st_size)
                    if not _d.get('size') or _part.stat().st_size == int(_d['size']): break
                except _requests.RequestException:
                    check()
                    if _attempt==3: raise RuntimeError('Drive模型下载中断，保留part')
            if _d.get('size') and _part.stat().st_size != int(_d['size']):
                raise RuntimeError('Drive模型文件大小不符')
            check()
            update(stage='verifying', downloaded_bytes=_part.stat().st_size)
            if _d.get('md5') and _drawing_md5(_part) != _d['md5']:
                _part.unlink(missing_ok=True)
                update(downloaded_bytes=0)
                raise RuntimeError('Drive模型校验和不符')
            check()
            _part.replace(_p)
            _marker.write_text(_json.dumps(_fingerprint))
            _partmeta.unlink(missing_ok=True)
        check()
        update(stage='ready', downloaded_bytes=_p.stat().st_size, result={'path':_d['relative'],'cached':_cached})
    except DownloadCancelled as exc:
        update(stage='cancelled', error=str(exc))
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else 'GPU模型同步失败（'+type(exc).__name__+'），保留已有.part供重试'
        update(stage='error', error=message)
    finally:
        _d.pop('token',None)
        if '_headers' in locals(): _headers.clear()
'''

SYNC_CONTROL = '''
import base64, json, threading, time
d = json.loads(base64.b64decode(_payload))
if time.time() > d['expires_at']:
    raise RuntimeError('模型同步控制请求已过期，未重放操作')
entry = _jobs.get(d['path'])
if d.get('action') == 'start':
    fingerprint = {k:d.get(k) for k in ('file_id','size','modified_at','md5')}
    if entry and entry['thread'].is_alive():
        # Keep the existing writer, including a cancellation still in flight.
        if entry['fingerprint'] != fingerprint:
            raise RuntimeError('该模型路径有未结束的下载，请等待结束后重试')
    else:
        entry = {'id':d['job_id'], 'fingerprint':fingerprint, 'cancel':threading.Event(),
                 'lock':threading.Lock(), 'lease':time.monotonic(),
                 'progress':{'stage':'preparing','filename':d['relative'].rsplit('/',1)[-1],
                             'downloaded_bytes':0,'total_bytes':int(d.get('size') or 0),'error':None}}
        namespace = {}
        exec(WORKER_SOURCE, namespace)
        entry['thread'] = threading.Thread(target=namespace['run'],args=(d,entry),daemon=True, name='drawing-model-'+entry['id'])
        _jobs[d['path']] = entry
        entry['thread'].start()
elif not entry or entry['id'] != d['job_id']:
    raise RuntimeError('GPU模型下载job丢失，未确认同步完成')
if d.get('action') == 'cancel':
    entry['cancel'].set()
else:
    entry['lease'] = time.monotonic()
with entry['lock']:
    snapshot = dict(entry['progress'])
snapshot['job_id'] = entry['id']
total = snapshot['total_bytes']
snapshot['download_progress'] = min(1.0, snapshot['downloaded_bytes']/total) if total else None
print('DRAWING_RESULT:'+json.dumps(snapshot))
'''

SYNC_CODE = 'exec(' + repr(SYNC_CONTROL.replace('WORKER_SOURCE', repr(SYNC_WORKER))) + ", {'_jobs':globals().setdefault('_drawing_model_jobs',{}), '_payload':PAYLOAD})"


ACTIVE_PORTS_CODE = '''
import base64 as _b64, json as _json, requests as _requests
_d = _json.loads(_b64.b64decode(PAYLOAD))
_alive = []
for _p in _d.get('ports', [8188]):
    _p = int(_p)
    if not 8188 <= _p <= 8191:
        continue
    try:
        if _requests.get(f'http://127.0.0.1:{_p}/system_stats', timeout=2).status_code == 200:
            _alive.append(_p)
    except Exception:
        pass
print('DRAWING_RESULT:' + _json.dumps({'ports': _alive}))
'''

POLL_TASK_STATE_CODE = '''
import base64 as _b64, json as _json, queue as _queue, requests as _requests, time as _time
_d = _json.loads(_b64.b64decode(PAYLOAD))
_p = int(_d.get('port') or 8188)
assert 8188 <= _p <= 8191
_pid = str(_d['prompt_id'])
_e = _drawing_ws.get(_d.get('ws_id'))
_messages = []
_closed = True
if _e:
    _e['lease'] = _time.monotonic()
    while True:
        try:
            _messages.append(_e['queue'].get_nowait())
        except _queue.Empty:
            break
    _closed = bool(_e.get('closed'))
_hr = _requests.get(f'http://127.0.0.1:{_p}/history/{_pid}', timeout=10)
if _hr.status_code >= 400:
    print('DRAWING_RESULT:' + _json.dumps({'http_error': f'ComfyUI HTTP {_hr.status_code}', 'messages': _messages, 'closed': _closed}))
else:
    _history = _hr.json() if _hr.content else {}
    _item = _history.get(_pid) if isinstance(_history, dict) else None
    _state = _item.get('status', {}) if isinstance(_item, dict) else {}
    _queue_data = {}
    if not (_state.get('completed') or _state.get('status_str') == 'error'):
        _qr = _requests.get(f'http://127.0.0.1:{_p}/queue', timeout=10)
        if _qr.status_code >= 400:
            print('DRAWING_RESULT:' + _json.dumps({'http_error': f'ComfyUI HTTP {_qr.status_code}', 'messages': _messages, 'closed': _closed}))
            _history = None
        else:
            _queue_data = _qr.json() if _qr.content else {}
            _active = (_queue_data.get('queue_running', []) if isinstance(_queue_data, dict) else []) + (_queue_data.get('queue_pending', []) if isinstance(_queue_data, dict) else [])
            if not _item and not any(str(_q[1]) == _pid for _q in _active):
                _hr2 = _requests.get(f'http://127.0.0.1:{_p}/history/{_pid}', timeout=10)
                if _hr2.status_code == 200 and _hr2.content:
                    _history = _hr2.json()
    if _history is not None:
        print('DRAWING_RESULT:' + _json.dumps({'messages': _messages, 'closed': _closed, 'history': _history, 'queue': _queue_data}))
'''

OBJECT_INFO_SUBSET_CODE = '''
import base64 as _b64, json as _json, requests as _requests
from urllib.parse import quote as _quote
_d = _json.loads(_b64.b64decode(PAYLOAD))
_p = int(_d.get('port') or 8188)
assert 8188 <= _p <= 8191
_out = {}
_fallback = False
for _cls in _d.get('classes') or []:
    _r = _requests.get(f'http://127.0.0.1:{_p}/object_info/' + _quote(str(_cls), safe=''), timeout=15)
    if _r.status_code == 200 and _r.content:
        _data = _r.json()
        if isinstance(_data, dict):
            if str(_cls) in _data or 'input' not in _data:
                _out.update(_data)
            else:
                _out[str(_cls)] = _data
    elif _r.status_code != 404:
        _fallback = True
        break
print('DRAWING_RESULT:' + _json.dumps({'nodes': _out, 'fallback': _fallback}))
'''

class Comfy:
    def __init__(self, runtime):
        self.runtime = runtime
        self.sync_locks = {}
        self.execution_sockets = {}
        self.execution_ports = {}

    async def _run(self, source, payload, timeout=120, *, interrupt_on_timeout=True):
        encoded = base64.b64encode(jsonlib.dumps(payload).encode()).decode()
        options = {} if interrupt_on_timeout else {'interrupt_on_timeout': False}
        result = await self.runtime.execute(source.replace('PAYLOAD', repr(encoded)), timeout=timeout, **options)
        if result.get('executionError'):
            # Do not reflect code/input/token-containing remote tracebacks to HTTP clients.
            raise AdapterError('GPU侧操作失败：' + str(result['executionError'].get('name') or 'ExecutionError'))
        lines = [v[len(PREFIX):] for v in result.get('stdout', '').splitlines() if v.startswith(PREFIX)]
        if not lines:
            raise AdapterError('GPU侧操作缺少完成结果')
        return jsonlib.loads(lines[-1])

    @staticmethod
    def _port(port=8188):
        p = int(port or 8188)
        if p not in ALLOWED_PORTS:
            raise AdapterError('ComfyUI端口无效')
        return p

    @staticmethod
    def _path(path):
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or '\\' in path or '..' in PurePosixPath(parsed.path).parts:
            raise AdapterError('ComfyUI代理路径无效')
        return path.lstrip('/')

    async def raw_request(self, method, path, json=None, data=None, params=None, headers=None, timeout=120, files=None, port=8188):
        headers = dict(headers or {})
        if files:
            import httpx
            # Encode multipart locally without making an outbound request; binary preserved.
            request = httpx.Request(method, 'http://127.0.0.1/', data=data, files=files)
            data = request.read()
            headers['Content-Type'] = request.headers['content-type']
        elif isinstance(data, dict):
            data = urlencode(data).encode()
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
        if isinstance(data, str):
            data = data.encode()
        result = await self._run(HTTP_CODE, {'port': self._port(port), 'method': method.upper(), 'path': self._path(path), 'json': json,
            'data': base64.b64encode(data).decode() if data is not None else None,
            'params': params, 'headers': headers, 'timeout': timeout}, timeout=timeout + 30)
        result['body'] = base64.b64decode(result['body'])
        return result

    async def request(self, method, path, json=None, data=None, **kwargs):
        r = await self.raw_request(method, path, json=json, data=data, **kwargs)
        if r['status'] >= 400:
            # Comfy validation details contain useful node diagnostics, no transport secrets.
            detail = r['body'].decode(errors='replace')[:3000] if 'json' in r['headers'].get('Content-Type', r['headers'].get('content-type', '')) else ''
            raise AdapterError(f"ComfyUI HTTP {r['status']}" + (': ' + detail if detail else ''))
        content_type = next((v for k, v in r['headers'].items() if k.lower() == 'content-type'), '')
        return jsonlib.loads(r['body']) if 'json' in content_type and r['body'] else r['body']

    async def proxy(self, path, request):
        from starlette.responses import Response
        query = list(request.query_params.multi_items())
        safe_headers = {k: v for k, v in request.headers.items() if k.lower() in {'content-type', 'accept', 'if-none-match', 'if-modified-since', 'range'}}
        r = await self.raw_request(request.method, path, data=await request.body(), params=query, headers=safe_headers)
        headers = r['headers']
        for key in list(headers):
            if key.lower() == 'location' and headers[key].startswith('/'):
                headers[key] = '/comfy' + headers[key]
        content_type = next((v for k, v in headers.items() if k.lower() == 'content-type'), '')
        body = r['body']
        if 'text/html' in content_type:
            # Base href keeps official frontend assets/API under the authenticated mount.
            body = body.replace(b'<head>', b'<head><base href="/comfy/">', 1)
            body = body.replace(b'="/assets/', b'="/comfy/assets/').replace(b"='/assets/", b"='/comfy/assets/")
            # Official frontend commonly constructs absolute /ws and /api URLs. Rewrite
            # same-origin paths centrally while leaving external extension URLs untouched.
            shim = b'''<script>(()=>{const fix=u=>{try{const x=new URL(u,location.href);if(x.origin===location.origin&&!x.pathname.startsWith('/comfy/')){x.pathname='/comfy'+x.pathname;return x.href}}catch{}return u};const F=window.fetch;window.fetch=(u,o)=>F(u instanceof Request?new Request(fix(u.url),u):fix(u),o);const W=window.WebSocket;window.WebSocket=class extends W{constructor(u,p){const x=new URL(u,location.href);if(x.host===location.host&&!x.pathname.startsWith('/comfy/'))x.pathname='/comfy'+x.pathname;super(x.href,p)}};const O=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u,...a){return O.call(this,m,fix(u),...a)}})();</script>'''
            body = body.replace(b'<head>', b'<head>' + shim, 1)
        return Response(body, status_code=r['status'], headers=headers)

    async def _execution_control(self, source, payload, timeout=25):
        # Isolate reader globals from later cells and native UI connections.
        wrapped = 'exec(' + repr(source.replace('PAYLOAD', '_payload')) + ", {'_drawing_ws':globals().setdefault('_drawing_task_ws',{}), '_payload':PAYLOAD})"
        try:
            async with asyncio.timeout(timeout):
                return await self._run(wrapped, payload, timeout=timeout, interrupt_on_timeout=False)
        except TimeoutError:
            raise AdapterError('GPU控制通道轮询超时，将自动重试') from None

    async def object_info_for(self, classes, port=8188):
        """Fetch /object_info schemas only for the specified node classes."""
        unique = list(dict.fromkeys(str(c) for c in (classes or []) if c))
        if not unique:
            return {}
        res = await self._run(
            OBJECT_INFO_SUBSET_CODE,
            {'port': self._port(port), 'classes': unique},
            timeout=45,
        )
        if res.get('fallback'):
            return await self.request('GET', '/object_info', port=port)
        return res.get('nodes') or {}

    async def active_ports(self, ports=(8188, 8189)):
        res = await self._run(ACTIVE_PORTS_CODE, {'ports': [self._port(p) for p in ports]}, timeout=15)
        return [int(p) for p in res.get('ports', [])]

    async def open_execution(self, client_id, port=8188):
        port = self._port(port)
        self.execution_ports[client_id] = port
        ident = self.execution_sockets.setdefault(client_id, uuid.uuid4().hex)
        source = WS_INIT.replace('import websocket as _websocket', 'import websocket as _websocket\nimport time as _time')
        source = source.replace("_socket = _websocket.create_connection", "if _time.time() > _d['expires_at']:\n    raise RuntimeError('Expired subscription')\n_old = _drawing_ws.pop(_d['id'], None)\nif _old:\n    _old['closed'] = True\n    _old['socket'].close()\n_socket = _websocket.create_connection")
        source = source.replace('timeout=20', 'timeout=5')
        source = source.replace('_queue.Queue()', '_queue.Queue(maxsize=512)')
        source = source.replace("'closed': False", "'closed': False, 'lease': _time.monotonic()")
        source = source.replace("while not entry['closed']:", "while not entry['closed']:\n            if _time.monotonic() - entry['lease'] > 90:\n                break")
        source = source.replace("entry['queue'].put({'text': frame.decode() if isinstance(frame,bytes) else frame})", "text = frame.decode() if isinstance(frame,bytes) else frame\n                try:\n                    event = _json.loads(text)\n                except ValueError:\n                    continue\n                if event.get('type') not in {'executing','progress','execution_start','execution_success','execution_error','execution_interrupted'}:\n                    continue\n                if entry['queue'].full():\n                    try: entry['queue'].get_nowait()\n                    except _queue.Empty: pass\n                entry['queue'].put({'text': text})")
        source = source.replace('elif opcode == 2:', 'elif False:')
        return await self._execution_control(source, {'id': ident, 'port': port, 'query': urlencode({'clientId': client_id}), 'expires_at': time.time()+15})

    async def poll_execution(self, client_id):
        if client_id not in self.execution_sockets:
            await self.open_execution(client_id, port=self.execution_ports.get(client_id, 8188))
        source = WS_POLL.replace("_e = _drawing_ws[_d['id']]", "_e = _drawing_ws[_d['id']]\n_e['lease'] = __import__('time').monotonic()")
        result = await self._execution_control(source, {'id': self.execution_sockets[client_id]})
        events = []
        for message in result.get('messages', []):
            try:
                event = jsonlib.loads(message.get('text', ''))
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        if result.get('closed'):
            await self.open_execution(client_id, port=self.execution_ports.get(client_id, 8188))
        return events

    async def poll_task_state(self, client_id, prompt_id, port=8188):
        """Fetch buffered WS progress events plus /history and /queue in one Jupyter round-trip."""
        port = self._port(port)
        if client_id not in self.execution_sockets:
            try:
                await self.open_execution(client_id, port=port)
            except Exception:
                pass
        ws_id = self.execution_sockets.get(client_id)
        result = await self._execution_control(
            POLL_TASK_STATE_CODE,
            {'ws_id': ws_id, 'port': port, 'prompt_id': str(prompt_id)},
            timeout=25,
        )
        if result.get('http_error'):
            raise AdapterError(result['http_error'])
        events = []
        for message in result.get('messages', []):
            try:
                event = jsonlib.loads(message.get('text', ''))
            except (ValueError, TypeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        if result.get('closed'):
            try:
                await self.open_execution(client_id, port=self.execution_ports.get(client_id, port))
            except Exception:
                pass
        return {
            'events': events,
            'history': result.get('history') or {},
            'queue': result.get('queue') or {},
        }

    async def close_execution(self, client_id):
        self.execution_ports.pop(client_id, None)
        ident = self.execution_sockets.pop(client_id, None)
        if ident is None:
            return
        source = "import json as _json,base64 as _b64\n_d=_json.loads(_b64.b64decode(PAYLOAD))\n_e=_drawing_ws.pop(_d['id'],None)\nif _e:\n _e['closed']=True\n _e['socket'].close()\nprint('DRAWING_RESULT:'+_json.dumps({'closed':True}))"
        try:
            await self._execution_control(source, {'id': ident})
        except (Exception, asyncio.CancelledError):
            pass  # Orphaned readers also expire through their lease.

    async def websocket_proxy(self, ws):
        # The caller MUST authenticate before invoking. Browser credentials never enter GPU.
        await ws.accept()
        ident = uuid.uuid4().hex
        queue = asyncio.Queue()
        client = ws.query_params.get('clientId') or uuid.uuid4().hex
        await self._run(WS_INIT, {'id': ident, 'query': urlencode({'clientId': client})}, timeout=40)
        async def receive():
            try:
                while True:
                    m = await ws.receive()
                    if m['type'] == 'websocket.disconnect':
                        break
                    if m.get('text') is not None:
                        await queue.put({'text': m['text']})
                    elif m.get('bytes') is not None:
                        await queue.put({'bytes': base64.b64encode(m['bytes']).decode()})
            finally:
                await queue.put(None)
        reader = asyncio.create_task(receive())
        try:
            while True:
                messages, closed = [], False
                while not queue.empty():
                    m = queue.get_nowait()
                    if m is None:
                        closed = True
                    else:
                        messages.append(m)
                if closed:
                    break
                result = await self._run(WS_POLL, {'id': ident, 'send': messages}, timeout=30)
                for m in result.get('messages', []):
                    if 'text' in m:
                        await ws.send_text(m['text'])
                    else:
                        await ws.send_bytes(base64.b64decode(m['bytes']))
                if result.get('closed'):
                    break
                await asyncio.sleep(.3)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            try:
                await self.runtime.execute(f"_e=globals().get('_drawing_ws',{{}}).pop({ident!r},None)\nif _e:\n _e['closed']=True\n _e['socket'].close()", timeout=10)
            except AdapterError:
                pass
            try:
                await ws.close()
            except RuntimeError:
                pass

    def model_path(self, row):
        kind = row.get('kind') or row.get('type') or row.get('category')
        aliases = {'checkpoint': 'checkpoints', 'lora': 'loras', 'upscale': 'upscale_models', 'upscaler': 'upscale_models', 'SEEDVR2': 'seedvr2'}
        kind = aliases.get(kind, kind)
        if kind not in KINDS:
            # Owner can assign a meaningful category to previously unclassified custom models.
            raise AdapterError('请先将模型分类设置为ComfyUI实际模型目录')
        name = row.get('filename') or row.get('name')
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            raise AdapterError('模型文件名无效')
        return ('SEEDVR2' if kind == 'seedvr2' else kind) + '/' + name

    async def sync_model(self, row, drive, on_progress=None):
        relative = self.model_path(row)
        ident = file_id(row.get('drive_file_id') or row.get('file_id') or row.get('id'))
        lock = self.sync_locks.setdefault(relative, asyncio.Lock())
        async with lock:
            meta = await drive.metadata(ident)
            control = {'path': COMFY + '/models/' + relative, 'job_id': uuid.uuid4().hex}
            endpoint = self.runtime.handle.get('endpoint')
            async def command(payload, timeout=30):
                # Include queueing behind other kernel work in the deadline.
                try:
                    async with asyncio.timeout(timeout):
                        return await self._run(SYNC_CODE, {**payload, 'expires_at': time.time()+timeout},
                                               timeout=timeout, interrupt_on_timeout=False)
                except TimeoutError:
                    raise AdapterError('模型同步控制通道超时，未中断内核；远端结果未确认') from None
            try:
                snapshot = await command({**control, 'action': 'start', 'file_id': ident,
                    'relative': relative, 'size': int(meta.get('size') or 0), 'modified_at': meta.get('modifiedTime'),
                    'md5': meta.get('md5Checksum'), 'token': await drive.access_token()})
                control['job_id'] = snapshot['job_id']
                while True:
                    if on_progress:
                        value = on_progress({k: v for k, v in snapshot.items() if k not in {'result', 'job_id'}})
                        if inspect.isawaitable(value):
                            await value
                    if snapshot['stage'] == 'ready':
                        result = snapshot['result']
                        await self._link_vfi_weight(row)
                        return result
                    if snapshot['stage'] in {'error', 'cancelled'}:
                        raise AdapterError(snapshot.get('error') or '模型同步未完成，保留.part供重试')
                    await asyncio.sleep(1)
                    if self.runtime.handle.get('endpoint') != endpoint:
                        raise AdapterError('GPU实例已改变，模型同步结果未确认')
                    snapshot = await command({**control, 'action': 'poll'})
            except AdapterError as exc:
                # Do not replay an ambiguous start/poll or report ready on disconnect.
                raise AdapterError('模型同步未确认完成，保留已有.part；远端job可能仍在结束，请重试时协调：' + str(exc)) from None
            except BaseException:
                # Cooperative cancellation affects this download only, never the kernel.
                try:
                    if self.runtime.handle.get('endpoint') == endpoint:
                        await command({**control, 'action': 'cancel'}, timeout=15)
                except (Exception, asyncio.CancelledError):
                    pass  # A disconnected worker stops when its lease expires.
                raise

    async def _link_vfi_weight(self, row):
        """RIFE loads ckpts from the custom node folder; point it at the Drive-synced file."""
        kind = row.get('kind') or row.get('type') or row.get('category')
        if kind != 'vfi_models':
            return
        name = row.get('filename') or row.get('name')
        if not name or '/' in str(name) or '\\' in str(name) or name in ('.', '..'):
            return
        src = COMFY + '/models/vfi_models/' + name
        dest = COMFY + '/custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/' + name
        script = (
            'from pathlib import Path\n'
            'src, dest = Path(' + repr(src) + '), Path(' + repr(dest) + ')\n'
            'dest.parent.mkdir(parents=True, exist_ok=True)\n'
            'if src.exists() and dest.resolve() != src.resolve():\n'
            '    dest.unlink(missing_ok=True)\n'
            '    dest.symlink_to(src)\n'
        )
        result = await self.runtime.execute(script, timeout=30)
        if result.get('executionError'):
            raise AdapterError('补帧模型未能链接到 RIFE 目录')

    async def remove_model(self, row):
        relative = self.model_path(row)
        path = COMFY + '/models/' + relative
        result = await self.runtime.execute('from pathlib import Path\np=Path(' + repr(path) + ')\np.unlink(missing_ok=True)\np.with_name(p.name+\'.drive.json\').unlink(missing_ok=True)', timeout=30)
        if result.get('executionError'):
            raise AdapterError('GPU模型缓存删除失败')
        return {'path': relative, 'removed': True}
