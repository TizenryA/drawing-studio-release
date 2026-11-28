"""Independent CLI Colab tunnel transport; no production imports or database writes."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent
TUNNEL = 'https://colab.research.google.com'
log = logging.getLogger(__name__)


class AdapterError(RuntimeError):
    pass


def private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class OAuth:
    """Refresh a project-private COPY; never write back to the source application."""
    def __init__(self, settings, purpose):
        self.settings, self.purpose = settings, purpose
        self.lock = asyncio.Lock()

    @property
    def path(self):
        return Path(os.environ.get('DRAWING_' + self.purpose.upper() + '_CREDENTIALS')
                    or self.settings().get(self.purpose + '_credentials_file')
                    or ROOT / 'config' / 'private' / (self.purpose + '.json')).expanduser()

    def credential(self):
        try:
            result = json.loads(self.path.read_text())
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (OSError, ValueError):
            raise AdapterError(f'{self.purpose}授权文件缺失或无效，请配置独立凭据') from None

    async def token(self, force=False):
        async with self.lock:
            c = self.credential()
            if not force and c.get('access_token') and float(c.get('expires_at') or 0) > time.time() + 90:
                return c['access_token']
            if not c.get('refresh_token') or not c.get('client_id'):
                raise AdapterError(f'{self.purpose}缺少可刷新授权，请重新授权')
            data = {k: c[k] for k in ('client_id', 'client_secret', 'refresh_token') if c.get(k)}
            data['grant_type'] = 'refresh_token'
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post('https://oauth2.googleapis.com/token', data=data)
                if response.status_code != 200:
                    raise AdapterError(f'{self.purpose}授权刷新失败（HTTP {response.status_code}）')
                token = response.json()
                if not token.get('access_token'):
                    raise AdapterError(f'{self.purpose}授权刷新缺少令牌')
                c.update(access_token=token['access_token'], expires_at=time.time() + int(token.get('expires_in', 3600)))
                if token.get('refresh_token'):
                    c['refresh_token'] = token['refresh_token']
                private_json(self.path, c)
                return c['access_token']
            except httpx.HTTPError:
                raise AdapterError(f'{self.purpose}授权服务器连接失败') from None


class Runtime:
    def __init__(self, settings, emit=lambda *args: None):
        self.settings, self.emit = settings, emit
        self.oauth = OAuth(settings, 'colab')
        self.path = Path(os.environ.get('DRAWING_DATA', str(ROOT / 'data'))) / 'runtime.json'
        try:
            self.handle = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.handle = {'state': 'off'}
        except (OSError, ValueError):
            raise AdapterError('runtime.json损坏；请保留原文件并人工核对Colab实例，禁止自动新申请') from None
        self.lifecycle_lock = asyncio.Lock()
        self.kernel_lock = asyncio.Lock()
        self.last_activity = float(self.handle.get('last_activity') or self.handle.get('ready_at') or time.time())
        self._status_checked = 0.0
        self._health_checked = 0.0
        self._health_failures = 0
        self._status_lock = asyncio.Lock()
        self._quota = None
        self._quota_checked = 0.0
        self._quota_lock = asyncio.Lock()
        self._starting_task = None
        self._kernel_verified_id = None
        self._kernel_verified_at = 0.0

    def _save(self, **patch):
        self.handle.update(patch)
        private_json(self.path, self.handle)

    def touch_activity(self, at=None, *, persist=False):
        ts = time.time() if at is None else float(at)
        self.last_activity = ts if at is not None else max(self.last_activity, ts)
        if persist and self.handle.get('state') not in {None, 'off'}:
            self._save(last_activity=self.last_activity)
        return self.last_activity

    async def _event(self, state=None, message=None):
        if state:
            self._save(state=state)
        if message:
            logs = self.handle.get('logs', [])[-199:] + [{'time': time.time(), 'message': message, 'state': self.handle.get('state')}]
            self._save(logs=logs, message=message)
        result = self.emit('runtime', {**self._public(), **({'message': message} if message else {})})
        if inspect.isawaitable(result):
            await result

    def _public(self):
        h = self.handle
        return {**{k: h.get(k) for k in ('state', 'gpu', 'mode', 'endpoint', 'started_at', 'ready_at', 'stopped_at', 'stop_reason', 'stop_trigger', 'disconnected_at', 'error', 'actual_gpu', 'memory_used', 'memory_total', 'message')},
                'last_activity': h.get('last_activity') if h.get('last_activity') is not None else self.last_activity,
                'runtime_id': h.get('endpoint'), 'instance_id': h.get('endpoint'), 'worker_ports': h.get('worker_ports') or [8188], 'logs': h.get('logs', [])}

    def _mark_disconnected(self, reason):
        disconnected_at = self.handle.get('disconnected_at') or time.time()
        changed = self.handle.get('state') != 'disconnected' or self.handle.get('error') != reason
        self._save(
            state='disconnected',
            error=reason,
            message=reason,
            memory_used=None,
            memory_total=None,
            disconnected_at=disconnected_at,
        )
        return changed

    async def _tun(self, method, path, params=None, headers=None):
        token = await self.oauth.token()
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    r = await client.request(method, TUNNEL + path, params={'authuser': '0', **(params or {})},
                        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'X-Colab-Client-Agent': 'colab-cli', **(headers or {})})
            except httpx.HTTPError:
                raise AdapterError('Colab隧道网络异常，操作结果可能未知；保留实例句柄后协调') from None
            if r.status_code == 401 and attempt == 0:
                token = await self.oauth.token(force=True)
                continue
            if r.status_code >= 400:
                raise AdapterError(f'Colab隧道请求失败（HTTP {r.status_code}）')
            text = r.text
            if text.startswith(")]}'"):
                text = text.split('\n', 1)[-1]
            try:
                return json.loads(text) if text.strip() else {}
            except ValueError:
                raise AdapterError('Colab隧道返回无效JSON') from None

    async def _assignments(self):
        data = await self._tun('GET', '/tun/m/assignments')
        return data if isinstance(data, list) else data.get('assignments', [])

    async def gpus(self):
        info = await self._tun('GET', '/tun/m/ccu-info')
        # Live tunnel may return protobuf JSON's compact positional array.
        if isinstance(info, list):
            info = dict(zip(('currentBalance', 'consumptionRateHourly', 'assignmentsCount', 'eligibleGpus', 'unused4', 'unused5', 'eligibleTpus'), info))
        self._quota = {
            'balance': info.get('currentBalance'),
            'consumption_rate': info.get('consumptionRateHourly'),
            'assignments_count': info.get('assignmentsCount'),
            'updated_at': time.time(),
        }
        self._quota_checked = time.monotonic()
        return {'gpus': [{'id': str(g).upper(), 'name': str(g).upper(), 'eligible': True}
                         for g in info.get('eligibleGpus', [])],
                'balance': info.get('currentBalance'), 'consumption_rate': info.get('consumptionRateHourly'),
                'assignments_count': info.get('assignmentsCount'), 'cpu_available': True}

    async def quota(self, force=False):
        """Return cached Colab CU balance and burn rate without failing runtime status checks."""
        if not force and self._quota is not None and time.monotonic() - self._quota_checked < 60:
            return dict(self._quota)
        async with self._quota_lock:
            if not force and self._quota is not None and time.monotonic() - self._quota_checked < 60:
                return dict(self._quota)
            if not self.oauth.path.is_file():
                return dict(self._quota) if self._quota else None
            try:
                await self.gpus()
            except Exception:
                pass
            return dict(self._quota) if self._quota else None

    def _proxy(self, assignment):
        p = assignment.get('runtimeProxyInfo') or {}
        if not p.get('url') or not p.get('token'):
            raise AdapterError('Colab实例缺少代理连接信息')
        self._save(endpoint=assignment['endpoint'], proxy_url=p['url'].rstrip('/'), proxy_token=p['token'],
                   proxy_expires_at=time.time() + int(p.get('tokenExpiresInSeconds') or 3600))

    async def _connection(self):
        if not self.handle.get('endpoint'):
            raise AdapterError('GPU尚未申请')
        if self.handle.get('proxy_token') and self.handle.get('proxy_expires_at', 0) > time.time() + 120:
            return self.handle['proxy_url'], self.handle['proxy_token']
        matched = next((a for a in await self._assignments() if a.get('endpoint') == self.handle['endpoint']), None)
        if not matched:
            self._mark_disconnected('Colab运行时已回收或释放')
            await self._event('disconnected', 'Colab运行时已回收或释放')
            raise AdapterError('Colab运行时已回收或释放')
        self._proxy(matched)
        return self.handle['proxy_url'], self.handle['proxy_token']

    def reserve_start(self, gpu='G4', mode='temporary'):
        # Persist acceptance before scheduling any network-bound work. No await:
        # concurrent HTTP submissions cannot both reserve the same runtime.
        if self.lifecycle_lock.locked() or self.handle.get('endpoint') or self.handle.get('state') in {
                'allocating', 'bootstrapping', 'syncing', 'starting', 'starting_comfy',
                'ready', 'stopping', 'allocation_unknown'}:
            raise AdapterError('机器正在运行、启动或待协调，禁止重复申请')
        now = time.time()
        self.handle = {'state': 'allocating', 'gpu': str(gpu).upper(), 'mode': mode,
                       'started_at': now, 'ready_at': None, 'stopped_at': None,
                       'stop_reason': None, 'stop_trigger': None, 'last_activity': now,
                       'error': None,
                       'message': '正在核对GPU可用性',
                       'logs': [{'time': now, 'state': 'allocating', 'message': '正在核对GPU可用性'}]}
        self.last_activity = now
        self._save()
        return self._public()

    async def start(self, gpu='G4', mode='temporary', *, reserved=False):
        if not reserved:
            self.reserve_start(gpu, mode)
        async with self.lifecycle_lock:
            self._starting_task = asyncio.current_task()
            self._kernel_verified_id = None
            self._kernel_verified_at = 0.0
            try:
                gpu = str(gpu).upper()
                available = await self.gpus()
                if gpu != 'CPU' and gpu not in [r['id'] for r in available['gpus']]:
                    raise AdapterError('所选GPU不在当前账号真实可用列表中')
                request_id = str(uuid.uuid4())
                params = {'nbh': request_id.replace('-', '_').ljust(44, '.')}
                if gpu != 'CPU':
                    params.update(variant='GPU', accelerator=gpu)
                if self.settings().get('high_ram') and gpu not in {'L4', 'V5E1', 'V6E1'}:
                    params['shape'] = 'hm'
                self._save(request_id=request_id, assign_params=params, session_start_balance=available.get('balance'))
                await self._event('allocating', '正在申请Colab运行时')
                first = await self._tun('GET', '/tun/m/assign', params)
                if first.get('endpoint'):
                    assigned = first
                else:
                    if not first.get('token'):
                        raise AdapterError('Colab未返回分配确认令牌')
                    # Persist ambiguity BEFORE the non-idempotent network round trip.
                    self._save(state='allocation_unknown')
                    assigned = await self._tun('POST', '/tun/m/assign', params, {'X-Goog-Colab-Token': first['token']})
                self._proxy(assigned)
                await self._event('bootstrapping', '运行时已分配，安装完整ComfyUI')
                code = (ROOT / 'colab' / 'bootstrap.py').read_text()
                config = {'cpu': gpu == 'CPU', 'comfy_ref': self.settings().get('comfy_ref', 'master'),
                          'custom_nodes': self.settings().get('custom_nodes', []),
                          'comfy_workers': int(self.settings().get('comfy_workers', 1))}
                result = await self.execute(code + '\nbootstrap(' + repr(config) + ')', timeout=1800)
                if result.get('executionError'):
                    raise AdapterError('ComfyUI初始化失败，请检查启动日志')
                # Check actual hardware and HTTP readiness; never infer readiness from allocation.
                probe = await self.execute("import json,requests,subprocess\nprint('DRAWING_READY:'+json.dumps({'comfy': requests.get('http://127.0.0.1:8188/system_stats',timeout=15).status_code == 200, 'gpu': subprocess.getoutput('nvidia-smi --query-gpu=name --format=csv,noheader')}))", timeout=30)
                line = next((v for v in probe['stdout'].splitlines() if v.startswith('DRAWING_READY:')), None)
                if probe.get('executionError') or not line or not json.loads(line[14:]).get('comfy'):
                    raise AdapterError('ComfyUI真实就绪探测失败')
                self._save(actual_gpu=json.loads(line[14:]).get('gpu'), error=None, disconnected_at=None)
                try:
                    await asyncio.wait_for(self.quota(force=True), timeout=8)
                except Exception:
                    pass
                self._health_failures = 0
                ready_ts = time.time()
                self.last_activity = ready_ts
                self._save(ready_at=ready_ts, last_activity=ready_ts, stopped_at=None, stop_reason=None, stop_trigger=None)
                await self._event('ready', 'ComfyUI已就绪')
                return self._public()
            except BaseException as exc:
                # Retain the owned endpoint so stop() can release after bootstrap failure.
                state = 'allocation_unknown' if self.handle.get('state') == 'allocation_unknown' and not self.handle.get('endpoint') else 'error'
                self._save(error='启动被取消' if isinstance(exc, asyncio.CancelledError) else str(exc) if isinstance(exc, AdapterError) else '启动失败，请检查连接与初始化日志')
                await self._event(state, self.handle['error'])
                raise
            finally:
                self._starting_task = None

    async def reset(self):
        """Force reset runtime state to off after checking active assignments."""
        async with self.lifecycle_lock:
            try:
                rows = await self._assignments()
            except Exception:
                rows = []
            if rows:
                for a in rows:
                    endpoint = a.get('endpoint')
                    if endpoint:
                        try:
                            path = '/tun/m/unassign/' + quote(endpoint, safe='')
                            c = await self._tun('GET', path)
                            if c.get('token'):
                                await self._tun('POST', path, headers={'X-Goog-Colab-Token': c['token']})
                        except Exception:
                            pass
            prev_logs = list(self.handle.get('logs') or [])[-180:]
            self.handle = {
                'state': 'off',
                'stopped_at': time.time(),
                'stop_reason': '手动复位状态并清理残留实例',
                'stop_trigger': 'manual_reset',
                'logs': prev_logs,
            }
            self._health_failures = 0
            self._kernel_verified_id = None
            self._kernel_verified_at = 0.0
            await self._event('off', '已完成状态复位与残留清理')
            return self._public()

    async def stop(self, *, reason=None, trigger='manual', require_ready=False):
        if require_ready and self.handle.get('state') != 'ready':
            return self._public()
        starting = self._starting_task
        if starting and starting is not asyncio.current_task() and not starting.done():
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
        async with self.lifecycle_lock:
            if require_ready and self.handle.get('state') != 'ready':
                return self._public()
            reason_text = reason or ('空闲超时自动关机' if trigger == 'idle_watchdog' else '手动关机')
            endpoint = self.handle.get('endpoint')
            if not endpoint:
                if self.handle.get('state') == 'allocation_unknown':
                    # Check live assignments before deciding
                    try:
                        rows = await self._assignments()
                    except Exception:
                        rows = []
                    if not rows:
                        prev_logs = list(self.handle.get('logs') or [])[-180:]
                        self.handle = {
                            'state': 'off',
                            'stopped_at': time.time(),
                            'stop_reason': reason_text,
                            'stop_trigger': trigger,
                            'logs': prev_logs,
                        }
                        await self._event('off', '已确认无活跃实例，状态已复位')
                        return self._public()
                    raise AdapterError('分配结果未知，先recover_allocation再关闭')
                if self.handle.get('state') != 'off':
                    prev_logs = list(self.handle.get('logs') or [])[-180:]
                    self.handle = {
                        'state': 'off',
                        'stopped_at': time.time(),
                        'stop_reason': reason_text,
                        'stop_trigger': trigger,
                        'logs': prev_logs,
                    }
                    await self._event('off', f'已停止运行时（{reason_text}）')
                else:
                    await self._event('off')
                return self._public()
            log.info('Stopping runtime %s (trigger=%s, reason=%s)', endpoint, trigger, reason_text)
            await self._event('stopping', f'正在关闭Colab运行时（{reason_text}）')
            rows = await self._assignments()
            if any(a.get('endpoint') == endpoint for a in rows):
                path = '/tun/m/unassign/' + quote(endpoint, safe='')
                confirmation = await self._tun('GET', path)
                if not confirmation.get('token'):
                    raise AdapterError('Colab未返回释放确认令牌')
                await self._tun('POST', path, headers={'X-Goog-Colab-Token': confirmation['token']})
                if any(a.get('endpoint') == endpoint for a in await self._assignments()):
                    raise AdapterError('Colab尚未确认释放，保留句柄，请重试关闭')
            prev_logs = list(self.handle.get('logs') or [])[-180:]
            self.handle = {
                'state': 'off',
                'stopped_at': time.time(),
                'stop_reason': reason_text,
                'stop_trigger': trigger,
                'logs': prev_logs,
            }
            self._health_failures = 0
            self._kernel_verified_id = None
            self._kernel_verified_at = 0.0
            await self._event('off', f'Colab运行时已确认释放（{reason_text}）')
            try:
                await asyncio.wait_for(self.quota(force=True), timeout=8)
            except Exception:
                pass
            return await self.status()

    async def status(self):
        # No kernel queueing behind model downloads or bootstrap; use bounded TTL.
        if self.handle.get('endpoint') and time.monotonic() - self._status_checked > 15 and not self._status_lock.locked():
            async with self._status_lock:
                self._status_checked = time.monotonic()
                try:
                    matched = next((a for a in await self._assignments() if a.get('endpoint') == self.handle['endpoint']), None)
                    if not matched:
                        if self._mark_disconnected('Colab运行时已回收或释放'):
                            await self._event('disconnected', 'Colab运行时已回收或释放')
                    else:
                        self._proxy(matched)
                        if self.handle.get('state') in {'ready', 'disconnected'} and not self.kernel_lock.locked() and time.monotonic() - self._health_checked > 30:
                            self._health_checked = time.monotonic()
                            try:
                                code = "import requests,json,subprocess\n_ports=[]\nfor _p in (8188,8189):\n try:\n  if requests.get(f'http://127.0.0.1:{_p}/system_stats',timeout=4).status_code==200: _ports.append(_p)\n except Exception: pass\nprint('DRAWING_HEALTH:'+json.dumps({'ok':8188 in _ports,'ports':_ports,'memory':subprocess.getoutput('nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits')}))"
                                result = await self.execute(code, timeout=20, activity=False)
                                line = next((x[len('DRAWING_HEALTH:'):] for x in result['stdout'].splitlines() if x.startswith('DRAWING_HEALTH:')), None)
                                health = json.loads(line) if line and not result.get('executionError') else {}
                                if health.get('ok'):
                                    self._health_failures = 0
                                patch = {
                                    'state': 'ready' if health.get('ok') else 'error',
                                    'worker_ports': health.get('ports') or ([8188] if health.get('ok') else []),
                                    'error': None if health.get('ok') else 'ComfyUI不可用，可重启进程',
                                    'message': 'ComfyUI已就绪' if health.get('ok') else 'ComfyUI不可用，可重启进程',
                                    'disconnected_at': None,
                                }
                                if health.get('ok') and (self.handle.get('state') != 'ready' or not self.handle.get('ready_at')):
                                    recovered_at = float(self.handle.get('ready_at') or self.touch_activity(persist=False))
                                    patch['ready_at'] = recovered_at
                                    patch['last_activity'] = max(float(self.handle.get('last_activity') or 0.0), recovered_at)
                                    self.last_activity = patch['last_activity']
                                try:
                                    used, total = health['memory'].splitlines()[0].split(',')
                                    patch.update(memory_used=int(used.strip()) * 1024**2, memory_total=int(total.strip()) * 1024**2)
                                except (KeyError, ValueError, IndexError):
                                    pass
                                self._save(**patch)
                            except AdapterError as exc:
                                self._health_failures += 1
                                log.warning('Runtime Jupyter health check failed (%d): %s', self._health_failures, exc)
                                if self.handle.get('state') == 'ready' and self._health_failures < 2:
                                    self._health_checked = time.monotonic() - 20
                                else:
                                    if self._mark_disconnected('Jupyter控制通道不可达'):
                                        await self._event('disconnected', 'Jupyter控制通道不可达')
                except AdapterError as exc:
                    if self._mark_disconnected(str(exc)):
                        await self._event('disconnected', str(exc))
        if self.oauth.path.is_file() and (self._quota is None or time.monotonic() - self._quota_checked > 60) and not self._quota_lock.locked():
            try:
                await asyncio.wait_for(self.quota(), timeout=8)
            except Exception:
                pass
        if self.handle.get('state') == 'ready' and not self.handle.get('ready_at'):
            backfill = max(float(self.last_activity or 0.0), time.time())
            self.last_activity = backfill
            self._save(ready_at=backfill, last_activity=backfill)
        h = self.handle
        result = self._public()
        end_ts = float(h['disconnected_at']) if h.get('state') == 'disconnected' and h.get('disconnected_at') else time.time()
        uptime = max(0, end_ts - float(h.get('started_at') or end_ts)) if h.get('endpoint') else 0
        result['uptime'] = uptime
        result['comfy_url'] = '/comfy/' if result['state'] == 'ready' else None
        result['last_activity'] = self.last_activity
        quota = dict(self._quota) if self._quota else None
        result['quota'] = quota
        start_bal = h.get('session_start_balance')
        result['session_start_balance'] = start_bal
        cur_bal = quota.get('balance') if quota else None
        rate = quota.get('consumption_rate') if quota else None
        def _num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        sb, cb, rt = _num(start_bal), _num(cur_bal), _num(rate)
        actual = max(0.0, round(sb - cb, 3)) if sb is not None and cb is not None and h.get('endpoint') else None
        estimated = round((uptime / 3600.0) * rt, 3) if h.get('endpoint') and uptime > 0 and rt is not None and rt > 0 else None
        result['session_cu_actual'] = actual
        result['session_cu_estimated'] = estimated
        result['session_cu_used'] = actual if actual is not None and actual > 0 else estimated
        return result

    @staticmethod
    def pack(message):
        body = json.dumps(message).encode()
        return struct.pack('>III', 2, 12, 12 + len(body)) + body

    @staticmethod
    def unpack(raw):
        if isinstance(raw, str):
            return json.loads(raw)
        if len(raw) < 8:
            raise AdapterError('无效Jupyter帧')
        count = int.from_bytes(raw[:4], 'big')
        if not 1 <= count <= (len(raw) - 4) // 4:
            raise AdapterError('无效Jupyter帧偏移')
        offsets = struct.unpack('>' + 'I' * count, raw[4:4 * (count + 1)])
        if offsets[0] < 4 * (count + 1) or list(offsets) != sorted(offsets) or offsets[-1] > len(raw):
            raise AdapterError('无效Jupyter帧范围')
        return json.loads(raw[offsets[0]:offsets[1] if count > 1 else len(raw)])

    async def execute(self, code, timeout=120, *, activity=True, interrupt_on_timeout=True):
        async with self.kernel_lock:
            if activity:
                self.touch_activity(persist=False)
            url, token = await self._connection()
            params = {'colab-runtime-proxy-token': token, 'authuser': '0'}
            headers = {'X-Colab-Client-Agent': 'colab-cli', 'X-Colab-Runtime-Proxy-Token': token}
            kernel = self.handle.get('kernel_id')
            sent = False
            try:
                async def _discover_kernel(current_kernel):
                    async with httpx.AsyncClient(timeout=30) as client:
                        if current_kernel:
                            r = await client.get(url + '/api/kernels/' + quote(current_kernel, safe=''), params=params, headers=headers)
                            if r.status_code == 404:
                                current_kernel = None
                            elif r.status_code != 200:
                                raise AdapterError(f'内核查询失败（HTTP {r.status_code}）')
                        if not current_kernel:
                            r = await client.get(url + '/api/kernels', params=params, headers=headers)
                            if r.status_code != 200:
                                raise AdapterError(f'内核列表失败（HTTP {r.status_code}）')
                            kernels = r.json()
                            if kernels:
                                current_kernel = kernels[0]['id']
                            else:
                                r = await client.post(url + '/api/kernels', json={'name': 'python3'}, params=params, headers=headers)
                                if r.status_code not in (200, 201):
                                    raise AdapterError(f'创建内核失败（HTTP {r.status_code}）')
                                current_kernel = r.json()['id']
                            self._save(kernel_id=current_kernel)
                    return current_kernel

                now_m = time.monotonic()
                used_cached_kernel = bool(
                    kernel and self._kernel_verified_id == kernel and (now_m - self._kernel_verified_at) < 60.0
                )
                if not used_cached_kernel:
                    kernel = await _discover_kernel(kernel)

                session, msgid = uuid.uuid4().hex, uuid.uuid4().hex
                message = {'header': {'msg_id': msgid, 'session': session, 'username': 'drawing-studio',
                    'date': datetime.now(timezone.utc).isoformat(), 'msg_type': 'execute_request', 'version': '5.3'},
                    'parent_header': {}, 'metadata': {}, 'channel': 'shell',
                    'content': {'code': code, 'silent': False, 'store_history': False, 'user_expressions': {},
                                'allow_stdin': False, 'stop_on_error': True}}

                def _ws_url(kid):
                    return url.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + '/api/kernels/' + quote(kid, safe='') + '/channels?' + urlencode({**params, 'session_id': session})

                stdout, stderr, error, reply, idle = [], [], None, False, False

                async def _run_ws(kid):
                    nonlocal error, reply, idle, sent
                    async with connect(_ws_url(kid), additional_headers=headers, open_timeout=30, max_size=None) as ws:
                        self._kernel_verified_id = kid
                        self._kernel_verified_at = time.monotonic()
                        sent = True
                        await ws.send(self.pack(message))
                        async for raw in ws:
                            m = self.unpack(raw)
                            if (m.get('parent_header') or {}).get('msg_id') != msgid:
                                continue
                            kind, c = (m.get('header') or {}).get('msg_type'), m.get('content') or {}
                            if kind == 'stream':
                                (stderr if c.get('name') == 'stderr' else stdout).append(c.get('text', ''))
                            elif kind in ('execute_result', 'display_data'):
                                stdout.append(str(c.get('data', {}).get('text/plain', '')) + '\n')
                            elif kind == 'error':
                                error = {'name': c.get('ename'), 'value': c.get('evalue')}
                            elif kind == 'execute_reply':
                                reply = True
                                if c.get('status') == 'error':
                                    error = {'name': c.get('ename'), 'value': c.get('evalue')}
                                elif c.get('status') not in {'ok'}:
                                    error = {'name': 'ExecutionAborted', 'value': c.get('status')}
                            elif kind == 'status' and c.get('execution_state') == 'idle':
                                idle = True
                            if reply and idle:
                                break

                async with asyncio.timeout(timeout):
                    try:
                        await _run_ws(kernel)
                    except Exception:
                        self._kernel_verified_id = None
                        self._kernel_verified_at = 0.0
                        if sent or not used_cached_kernel:
                            raise
                        # Pre-send connect failure on a cached kernel: re-probe once without replaying code.
                        kernel = await _discover_kernel(None)
                        await _run_ws(kernel)
                if not (reply and idle):
                    self._kernel_verified_id = None
                    self._kernel_verified_at = 0.0
                    raise AdapterError('Jupyter连接提前关闭，执行结果未知')
                return {'stdout': ''.join(stdout), 'stderr': ''.join(stderr), 'executionError': error}
            except (TimeoutError, asyncio.CancelledError) as exc:
                self._kernel_verified_id = None
                self._kernel_verified_at = 0.0
                # Background-job control cells must never interrupt unrelated kernel work.
                if interrupt_on_timeout:
                    try:
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(url + '/api/kernels/' + quote(kernel or '', safe='') + '/interrupt', params=params, headers=headers)
                    except httpx.HTTPError:
                        pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
                message = 'Jupyter执行超时，已请求中断；不会自动重放代码' if interrupt_on_timeout else 'Jupyter控制通道超时，远端结果未知；未中断内核，不会自动重放代码'
                raise AdapterError(message) from None
            except AdapterError:
                self._kernel_verified_id = None
                self._kernel_verified_at = 0.0
                raise
            except Exception as exc:
                self._kernel_verified_id = None
                self._kernel_verified_at = 0.0
                raise AdapterError(f'Jupyter通道失败（{type(exc).__name__}），执行结果可能未知') from None

    async def restart_comfy(self):
        async with self.lifecycle_lock:
            await self._event('bootstrapping', '重启ComfyUI进程，保留GPU及模型')
            code = (ROOT / 'colab' / 'bootstrap.py').read_text()
            try:
                workers = max(1, min(2, int(self.settings().get('comfy_workers', 1))))
            except (TypeError, ValueError):
                workers = 1
            result = await self.execute(code + f'\nrestart_comfy(workers={workers})', timeout=180)
            if result.get('executionError'):
                await self._event('error', 'ComfyUI重启失败')
                raise AdapterError('ComfyUI重启失败')
            ready_ts = time.time()
            self.last_activity = ready_ts
            self._save(ready_at=ready_ts, last_activity=ready_ts, stopped_at=None, stop_reason=None, stop_trigger=None)
            await self._event('ready', 'ComfyUI重启完成并已就绪')
            return await self.status()
