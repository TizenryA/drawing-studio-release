"""Self-contained Google OAuth2 authorization manager for Colab and Google Drive."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .runtime import AdapterError, private_json

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'
GOOGLE_DRIVE_ABOUT_URL = 'https://www.googleapis.com/drive/v3/about'

DEFAULT_MANUAL_REDIRECT_URI = 'http://127.0.0.1:8765/callback'
ADC_REDIRECT_URI = 'https://sdk.cloud.google.com/applicationdefaultauthcode.html'
FLOW_TTL_SECONDS = 900
MAX_PENDING_FLOWS = 64

VALID_PURPOSES = ('colab', 'drive')

DEFAULT_SCOPES = {
    'drive': [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/userinfo.email',
        'openid',
    ],
    'colab': [
        'openid',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/userinfo.profile',
        'https://www.googleapis.com/auth/cloud-platform',
        'https://www.googleapis.com/auth/colaboratory',
    ],
}


def validate_purpose(purpose: str) -> str:
    value = str(purpose or '').strip().lower()
    if value not in VALID_PURPOSES:
        raise ValueError(f'不支持的授权目标：{purpose}（仅支持 colab 或 drive）')
    return value


def mask_client_id(client_id: str | None) -> str:
    cid = str(client_id or '').strip()
    if not cid:
        return ''
    if len(cid) <= 16:
        return cid[:4] + '***' + cid[-2:]
    return f'{cid[:8]}...{cid[-16:]}'


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode('ascii').rstrip('=')
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    return verifier, challenge


def parse_client_config(raw) -> dict:
    """Parse Google OAuth client JSON (flat, {'installed': ...}, or {'web': ...})."""
    if raw is None or raw == '':
        return {'client_id': '', 'client_secret': '', 'redirect_uris': []}
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError('OAuth 客户端配置必须是 JSON 对象')
    section = data.get('installed') or data.get('web') or data
    if not isinstance(section, dict):
        raise ValueError('OAuth 客户端配置结构无效')
    client_id = str(section.get('client_id') or '').strip()
    client_secret = str(section.get('client_secret') or '').strip()
    redirects = section.get('redirect_uris') if isinstance(section.get('redirect_uris'), list) else []
    redirect_uris = [str(u).strip() for u in redirects if isinstance(u, str) and u.strip()]
    return {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uris': redirect_uris,
    }


def extract_code_and_state(code_or_url: str) -> tuple[str, str | None]:
    """Extract OAuth authorization code and optional state from a pasted code or callback URL."""
    raw = str(code_or_url or '').strip()
    if not raw:
        raise ValueError('请填写授权码或包含 code= 的回调完整地址')
    if raw.startswith(('http://', 'https://')) or '?' in raw or 'code=' in raw or 'error=' in raw:
        query = urlsplit(raw).query if '://' in raw else raw.lstrip('?')
        if not query and raw.startswith(('http://', 'https://')):
            query = urlsplit(raw).fragment
        params = parse_qs(query, keep_blank_values=False)
        if params.get('error'):
            err = params['error'][0]
            desc = params.get('error_description', [''])[0]
            detail = f'{err}: {desc}' if desc else err
            raise ValueError(f'Google 授权被拒绝或取消（{detail}）')
        codes = params.get('code') or []
        if not codes:
            raise ValueError('未能从回调地址中解析出 code 参数，请确认粘贴了完整跳转链接')
        states = params.get('state') or []
        return codes[0].strip(), (states[0].strip() if states else None)
    return raw, None


def resolve_callback_url(request, purpose: str) -> str:
    purpose = validate_purpose(purpose)
    if request is None:
        return f'/api/oauth/{purpose}/callback'
    headers = getattr(request, 'headers', {}) or {}
    raw_proto = headers.get('x-forwarded-proto') or getattr(request.url, 'scheme', 'http') or 'http'
    proto = raw_proto.split(',')[0].strip() or 'http'
    raw_host = headers.get('x-forwarded-host') or headers.get('host') or getattr(request.url, 'netloc', '')
    host = raw_host.split(',')[0].strip()
    if not host:
        return f'/api/oauth/{purpose}/callback'
    return f'{proto}://{host}/api/oauth/{purpose}/callback'


def _normalize_scopes(raw_scopes, purpose: str) -> list[str]:
    if isinstance(raw_scopes, str):
        items = [s for s in raw_scopes.replace(',', ' ').split() if s]
        return items or list(DEFAULT_SCOPES[purpose])
    if isinstance(raw_scopes, list):
        items = [str(s).strip() for s in raw_scopes if str(s).strip()]
        return items or list(DEFAULT_SCOPES[purpose])
    return list(DEFAULT_SCOPES[purpose])


class OAuthFlowManager:
    """Manages interactive OAuth2 PKCE / Web callback / JSON import flows for Colab and Drive."""

    def __init__(self, runtime, drive, *, http_client_factory=None, time_fn=time.time):
        self.runtime = runtime
        self.drive = drive
        self.http_client_factory = http_client_factory or (lambda timeout=30: httpx.AsyncClient(timeout=timeout))
        self.time_fn = time_fn
        self._flows: dict[str, dict] = {}
        self._state_index: dict[str, str] = {}

    def _adapter(self, purpose: str):
        purpose = validate_purpose(purpose)
        adapter = self.runtime if purpose == 'colab' else self.drive
        if adapter is None:
            raise AdapterError(f'{purpose} 模块尚未初始化')
        return adapter

    def _oauth(self, purpose: str):
        return self._adapter(purpose).oauth

    def _read_record(self, purpose: str) -> dict | None:
        oauth = self._oauth(purpose)
        try:
            if not oauth.path.is_file():
                return None
            data = json.loads(oauth.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _env_client(self, purpose: str) -> tuple[str, str]:
        prefix = 'DRAWING_' + purpose.upper()
        cid = os.environ.get(prefix + '_CLIENT_ID', '').strip()
        secret = os.environ.get(prefix + '_CLIENT_SECRET', '').strip()
        return cid, secret

    def _prune_flows(self):
        now = self.time_fn()
        expired = [fid for fid, item in self._flows.items() if item['expires_at'] < now]
        for fid in expired:
            flow = self._flows.pop(fid, None)
            if flow:
                self._state_index.pop(flow['state'], None)
        while len(self._flows) >= MAX_PENDING_FLOWS:
            oldest_id = min(self._flows, key=lambda k: self._flows[k]['created_at'])
            flow = self._flows.pop(oldest_id, None)
            if flow:
                self._state_index.pop(flow['state'], None)

    def _invalidate_caches(self, purpose: str):
        if purpose == 'drive' and self.drive is not None:
            if hasattr(self.drive, 'reset_folder_cache'):
                self.drive.reset_folder_cache()
            else:
                self.drive._folders = None
        elif purpose == 'colab' and self.runtime is not None:
            self.runtime._quota = None
            self.runtime._quota_checked = 0.0

    def status(self, purpose: str, *, callback_url: str | None = None) -> dict:
        purpose = validate_purpose(purpose)
        oauth = self._oauth(purpose)
        rec = self._read_record(purpose) or {}
        env_cid, _ = self._env_client(purpose)
        client_id = str(rec.get('client_id') or '').strip()
        has_refresh = bool(str(rec.get('refresh_token') or '').strip())
        expires_at = rec.get('expires_at')
        now = self.time_fn()
        token_valid = bool(rec.get('access_token') and expires_at and float(expires_at) > now + 90)
        scopes = rec.get('scopes') or rec.get('scope')
        if isinstance(scopes, str):
            scopes = scopes.split()
        elif not isinstance(scopes, list):
            scopes = []
        return {
            'purpose': purpose,
            'configured': bool(oauth.path.is_file() and client_id and has_refresh),
            'has_client_id': bool(client_id or env_cid),
            'client_id_preview': mask_client_id(client_id or env_cid),
            'has_client_secret': bool(rec.get('client_secret')),
            'has_refresh_token': has_refresh,
            'account_email': str(rec.get('account_email') or ''),
            'scopes': scopes,
            'token_valid': token_valid,
            'expires_at': expires_at,
            'updated_at': rec.get('updated_at'),
            'path_hint': oauth.path.name,
            'default_scopes': list(DEFAULT_SCOPES[purpose]),
            'default_manual_redirect_uri': DEFAULT_MANUAL_REDIRECT_URI,
            'adc_redirect_uri': ADC_REDIRECT_URI,
            'callback_url': callback_url or f'/api/oauth/{purpose}/callback',
            'env_client_configured': bool(env_cid),
        }

    def all_status(self, request=None) -> dict:
        return {
            purpose: self.status(purpose, callback_url=resolve_callback_url(request, purpose))
            for purpose in VALID_PURPOSES
        }

    def start(self, purpose: str, payload: dict | None = None, *, callback_url: str | None = None) -> dict:
        purpose = validate_purpose(purpose)
        self._prune_flows()
        data = payload if isinstance(payload, dict) else {}
        existing = self._read_record(purpose) or {}
        env_cid, env_secret = self._env_client(purpose)

        parsed = parse_client_config(data.get('client_json')) if data.get('client_json') else {'client_id': '', 'client_secret': '', 'redirect_uris': []}
        req_cid = str(data.get('client_id') or '').strip()
        req_secret = str(data.get('client_secret') or '').strip()

        client_id = parsed['client_id'] or req_cid or env_cid or str(existing.get('client_id') or '').strip()
        if not client_id:
            raise ValueError(f'请先填写或导入 {purpose.upper()} 的 Google OAuth Client ID（或在环境变量配置）')

        if parsed['client_id'] or req_cid:
            # Explicit new client selected: only keep secret from explicit payload or matching stored client.
            client_secret = parsed['client_secret'] or req_secret
            if not client_secret and client_id == existing.get('client_id'):
                client_secret = str(existing.get('client_secret') or '').strip()
            if not client_secret and client_id == env_cid:
                client_secret = env_secret
        else:
            client_secret = env_secret or str(existing.get('client_secret') or '').strip()

        flow_mode = str(data.get('flow_mode') or 'manual').strip().lower()
        if flow_mode not in ('manual', 'web_callback'):
            raise ValueError('flow_mode 仅支持 manual 或 web_callback')

        default_redirect = (callback_url or f'/api/oauth/{purpose}/callback') if flow_mode == 'web_callback' else DEFAULT_MANUAL_REDIRECT_URI
        redirect_uri = str(data.get('redirect_uri') or '').strip()
        if not redirect_uri and parsed['redirect_uris']:
            if flow_mode == 'web_callback' and callback_url in parsed['redirect_uris']:
                redirect_uri = callback_url
            elif flow_mode == 'manual':
                redirect_uri = parsed['redirect_uris'][0]
        if not redirect_uri:
            redirect_uri = default_redirect
        if not redirect_uri.startswith(('http://', 'https://')):
            raise ValueError('redirect_uri 必须是完整的 http:// 或 https:// 地址')

        scopes = _normalize_scopes(data.get('scopes'), purpose)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        flow_id = secrets.token_urlsafe(16)
        now = self.time_fn()

        self._flows[flow_id] = {
            'flow_id': flow_id,
            'purpose': purpose,
            'flow_mode': flow_mode,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'scopes': scopes,
            'code_verifier': verifier,
            'state': state,
            'created_at': now,
            'expires_at': now + FLOW_TTL_SECONDS,
        }
        self._state_index[state] = flow_id

        query = urlencode({
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(scopes),
            'access_type': 'offline',
            'prompt': 'consent',
            'include_granted_scopes': 'true',
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'state': state,
        })
        return {
            'flow_id': flow_id,
            'purpose': purpose,
            'flow_mode': flow_mode,
            'auth_url': f'{GOOGLE_AUTH_URL}?{query}',
            'redirect_uri': redirect_uri,
            'state': state,
            'scopes': scopes,
            'expires_in': FLOW_TTL_SECONDS,
        }

    async def _fetch_userinfo(self, access_token: str) -> str:
        if not access_token:
            return ''
        try:
            async with self.http_client_factory(timeout=15) as client:
                r = await client.get(GOOGLE_USERINFO_URL, headers={'Authorization': 'Bearer ' + access_token})
            if r.status_code == 200:
                info = r.json()
                return str(info.get('email') or '').strip()
        except Exception:
            pass
        return ''

    async def exchange(
        self,
        purpose: str,
        *,
        flow_id: str | None = None,
        state: str | None = None,
        code_or_url: str = '',
    ) -> dict:
        purpose = validate_purpose(purpose)
        self._prune_flows()
        code, extracted_state = extract_code_and_state(code_or_url)
        lookup_state = (extracted_state or state or '').strip()

        fid = (flow_id or '').strip()
        if not fid and lookup_state:
            fid = self._state_index.get(lookup_state, '')
        flow = self._flows.get(fid)
        if not flow or flow['purpose'] != purpose:
            raise ValueError('授权会话不存在或已过期，请重新生成授权链接')
        if flow['expires_at'] < self.time_fn():
            self._flows.pop(fid, None)
            self._state_index.pop(flow['state'], None)
            raise ValueError('授权会话已过期，请重新生成授权链接')
        if extracted_state and not secrets.compare_digest(extracted_state, flow['state']):
            raise ValueError('授权 state 校验不匹配，已拒绝防止跨站回调攻击')
        if state and not secrets.compare_digest(state.strip(), flow['state']):
            raise ValueError('授权 state 校验不匹配')

        # Consume single-use flow atomically before external token exchange.
        self._flows.pop(fid, None)
        self._state_index.pop(flow['state'], None)

        form = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': flow['client_id'],
            'redirect_uri': flow['redirect_uri'],
            'code_verifier': flow['code_verifier'],
        }
        if flow['client_secret']:
            form['client_secret'] = flow['client_secret']

        try:
            async with self.http_client_factory(timeout=30) as client:
                resp = await client.post(GOOGLE_TOKEN_URL, data=form)
        except httpx.HTTPError:
            raise AdapterError('连接 Google OAuth 令牌服务器失败，请检查 VPS 出网或代理设置') from None

        if resp.status_code != 200:
            err_detail = ''
            try:
                err_json = resp.json()
                err_detail = err_json.get('error_description') or err_json.get('error') or ''
            except ValueError:
                pass
            suffix = f'：{err_detail}' if err_detail else ''
            raise ValueError(f'授权码换取令牌失败（HTTP {resp.status_code}{suffix}）')

        token_data = resp.json()
        access_token = str(token_data.get('access_token') or '').strip()
        if not access_token:
            raise AdapterError('Google 未返回 access_token')

        existing = self._read_record(purpose) or {}
        refresh_token = str(token_data.get('refresh_token') or '').strip()
        if not refresh_token and existing.get('client_id') == flow['client_id']:
            refresh_token = str(existing.get('refresh_token') or '').strip()
        if not refresh_token:
            raise ValueError('Google 未返回 refresh_token，请在 Google 授权页移除旧应用授权后重新授权')

        email = await self._fetch_userinfo(access_token) or str(existing.get('account_email') or '')
        returned_scopes = str(token_data.get('scope') or '').split() or flow['scopes']
        record = {
            'client_id': flow['client_id'],
            'refresh_token': refresh_token,
            'access_token': access_token,
            'expires_at': self.time_fn() + int(token_data.get('expires_in') or 3600),
            'token_uri': GOOGLE_TOKEN_URL,
            'scopes': returned_scopes,
            'account_email': email,
            'updated_at': self.time_fn(),
        }
        if flow['client_secret']:
            record['client_secret'] = flow['client_secret']

        oauth = self._oauth(purpose)
        private_json(oauth.path, record)
        self._invalidate_caches(purpose)
        return {
            'ok': True,
            'purpose': purpose,
            'account_email': email,
            'status': self.status(purpose),
        }

    async def import_credentials(self, purpose: str, payload: dict | None = None) -> dict:
        purpose = validate_purpose(purpose)
        data = payload if isinstance(payload, dict) else {}
        raw_cred = data.get('credential_json') if 'credential_json' in data else data
        parsed = json.loads(raw_cred) if isinstance(raw_cred, str) else raw_cred
        if not isinstance(parsed, dict):
            raise ValueError('凭据内容必须是 JSON 对象')

        client_id = str(parsed.get('client_id') or '').strip()
        client_secret = str(parsed.get('client_secret') or '').strip()
        refresh_token = str(parsed.get('refresh_token') or '').strip()
        if not client_id or not refresh_token:
            raise ValueError('凭据 JSON 必须包含有效的 client_id 与 refresh_token')

        record = {
            'client_id': client_id,
            'refresh_token': refresh_token,
            'token_uri': str(parsed.get('token_uri') or GOOGLE_TOKEN_URL),
            'scopes': _normalize_scopes(parsed.get('scopes') or parsed.get('scope'), purpose),
            'account_email': str(parsed.get('account_email') or '').strip(),
            'updated_at': self.time_fn(),
        }
        if client_secret:
            record['client_secret'] = client_secret

        verify = data.get('verify', True) is not False
        if verify:
            form = {'grant_type': 'refresh_token', 'client_id': client_id, 'refresh_token': refresh_token}
            if client_secret:
                form['client_secret'] = client_secret
            try:
                async with self.http_client_factory(timeout=30) as client:
                    resp = await client.post(GOOGLE_TOKEN_URL, data=form)
            except httpx.HTTPError:
                raise AdapterError('校验 refresh_token 时无法连接 Google 授权服务器') from None
            if resp.status_code != 200:
                raise ValueError(f'导入的 refresh_token 校验失败（HTTP {resp.status_code}）')
            token_data = resp.json()
            access_token = str(token_data.get('access_token') or '').strip()
            if not access_token:
                raise ValueError('导入验证失败：响应缺少 access_token')
            record['access_token'] = access_token
            record['expires_at'] = self.time_fn() + int(token_data.get('expires_in') or 3600)
            if token_data.get('refresh_token'):
                record['refresh_token'] = str(token_data['refresh_token']).strip()
            if not record['account_email']:
                record['account_email'] = await self._fetch_userinfo(access_token)
        else:
            if parsed.get('access_token'):
                record['access_token'] = str(parsed['access_token']).strip()
            if parsed.get('expires_at'):
                record['expires_at'] = parsed['expires_at']

        oauth = self._oauth(purpose)
        private_json(oauth.path, record)
        self._invalidate_caches(purpose)
        return {
            'ok': True,
            'purpose': purpose,
            'verified': verify,
            'account_email': record.get('account_email', ''),
            'status': self.status(purpose),
        }

    async def test_connection(self, purpose: str) -> dict:
        purpose = validate_purpose(purpose)
        oauth = self._oauth(purpose)
        token = await oauth.token(force=True)
        rec = self._read_record(purpose) or {}

        if purpose == 'drive':
            try:
                async with self.http_client_factory(timeout=20) as client:
                    resp = await client.get(
                        GOOGLE_DRIVE_ABOUT_URL,
                        params={'fields': 'user(emailAddress,displayName)'},
                        headers={'Authorization': 'Bearer ' + token},
                    )
            except httpx.HTTPError:
                raise AdapterError('无法连接 Google Drive API') from None
            if resp.status_code >= 400:
                raise AdapterError(f'Drive 接口返回异常（HTTP {resp.status_code}），请检查授权 Scope')
            user = (resp.json() or {}).get('user') or {}
            email = str(user.get('emailAddress') or rec.get('account_email') or '').strip()
            display_name = str(user.get('displayName') or '').strip()
            if email and email != rec.get('account_email') and oauth.path.is_file():
                rec['account_email'] = email
                rec['updated_at'] = self.time_fn()
                private_json(oauth.path, rec)
            return {
                'ok': True,
                'purpose': 'drive',
                'account_email': email,
                'display_name': display_name,
                'message': f'Drive 连通正常（{email or "已授权账号"}）',
                'status': self.status('drive'),
            }

        info = await self.runtime.gpus()
        if not rec.get('account_email'):
            email = await self._fetch_userinfo(token)
            if email and oauth.path.is_file():
                rec['account_email'] = email
                rec['updated_at'] = self.time_fn()
                private_json(oauth.path, rec)
        gpus = [g.get('id') for g in info.get('gpus', [])]
        balance = info.get('balance')
        rate = info.get('consumption_rate')
        gpu_summary = '、'.join(gpus) if gpus else 'CPU / 待检'
        return {
            'ok': True,
            'purpose': 'colab',
            'account_email': rec.get('account_email', ''),
            'balance': balance,
            'consumption_rate': rate,
            'gpus': gpus,
            'message': f'Colab 连通正常（可用 GPU：{gpu_summary} · 余额 {balance if balance is not None else "—"} CU）',
            'status': self.status('colab'),
        }

    def disconnect(self, purpose: str) -> dict:
        purpose = validate_purpose(purpose)
        oauth = self._oauth(purpose)
        oauth.path.unlink(missing_ok=True)
        self._invalidate_caches(purpose)
        return {
            'ok': True,
            'purpose': purpose,
            'status': self.status(purpose),
        }
