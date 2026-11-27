"""Tests for standalone Google Colab and Google Drive OAuth2 authorization flows."""
import hashlib
import base64
import json
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from . import main
from .database import Database
from .drive import Drive
from .oauth_flow import (
    OAuthFlowManager,
    extract_code_and_state,
    mask_client_id,
    parse_client_config,
    pkce_pair,
)
from .runtime import Runtime


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, calls, responder):
        self._calls = calls
        self._responder = responder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None, **kwargs):
        self._calls.append(('POST', url, dict(data or {}), dict(headers or {})))
        return self._responder('POST', url, data=data, headers=headers)

    async def get(self, url, params=None, headers=None, **kwargs):
        self._calls.append(('GET', url, dict(params or {}), dict(headers or {})))
        return self._responder('GET', url, params=params, headers=headers)


class OAuthHelpersTests(unittest.TestCase):
    def test_parse_client_config_variants(self):
        installed = parse_client_config(json.dumps({
            'installed': {
                'client_id': 'cid-installed.apps.googleusercontent.com',
                'client_secret': 'sec-installed',
                'redirect_uris': ['http://127.0.0.1:8765/callback'],
            }
        }))
        self.assertEqual(installed['client_id'], 'cid-installed.apps.googleusercontent.com')
        self.assertEqual(installed['client_secret'], 'sec-installed')
        self.assertEqual(installed['redirect_uris'], ['http://127.0.0.1:8765/callback'])

        web = parse_client_config({
            'web': {
                'client_id': 'cid-web',
                'client_secret': 'sec-web',
                'redirect_uris': ['https://draw.example.com/api/oauth/drive/callback'],
            }
        })
        self.assertEqual(web['client_id'], 'cid-web')
        self.assertEqual(web['client_secret'], 'sec-web')

        with self.assertRaises(ValueError):
            parse_client_config('[1, 2, 3]')

    def test_extract_code_and_state_and_pkce(self):
        code, st = extract_code_and_state('4/0AQ-raw-code')
        self.assertEqual(code, '4/0AQ-raw-code')
        self.assertIsNone(st)

        code2, st2 = extract_code_and_state('http://127.0.0.1:8765/callback?state=xyz123&code=4%2F0AQ-url-code&scope=openid')
        self.assertEqual(code2, '4/0AQ-url-code')
        self.assertEqual(st2, 'xyz123')

        with self.assertRaises(ValueError):
            extract_code_and_state('http://127.0.0.1:8765/callback?error=access_denied')

        verifier, challenge = pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
        self.assertEqual(challenge, expected)
        self.assertTrue(mask_client_id('1234567890-abcdef.apps.googleusercontent.com').startswith('12345678...'))


class OAuthFlowManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.colab_path = self.root / 'colab.json'
        self.drive_path = self.root / 'drive.json'
        self.settings = lambda: {
            'colab_credentials_file': str(self.colab_path),
            'drive_credentials_file': str(self.drive_path),
        }
        self.runtime = Runtime(self.settings)
        self.drive = Drive(self.settings)
        self.calls = []
        self.now = 1_700_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def _manager(self, responder):
        return OAuthFlowManager(
            self.runtime,
            self.drive,
            http_client_factory=lambda timeout=30: FakeAsyncClient(self.calls, responder),
            time_fn=lambda: self.now,
        )

    async def test_manual_pkce_flow_saves_0600_and_never_leaks_secrets(self):
        def responder(method, url, **kwargs):
            if method == 'POST' and url.endswith('/token'):
                return FakeResponse(200, {
                    'access_token': 'ya29.fresh-colab',
                    'refresh_token': '1//0g-secret-refresh',
                    'expires_in': 3600,
                    'scope': 'openid https://www.googleapis.com/auth/colaboratory',
                })
            if method == 'GET' and 'userinfo' in url:
                return FakeResponse(200, {'email': 'colab-owner@example.com'})
            raise AssertionError(f'Unexpected call: {method} {url}')

        mgr = self._manager(responder)
        initial = mgr.all_status()
        self.assertFalse(initial['colab']['configured'])
        self.assertFalse(initial['drive']['configured'])

        started = mgr.start('colab', {'client_id': 'colab-client-id-12345678', 'flow_mode': 'manual'})
        auth_query = parse_qs(urlsplit(started['auth_url']).query)
        self.assertEqual(auth_query['code_challenge_method'][0], 'S256')
        self.assertEqual(auth_query['access_type'][0], 'offline')
        self.assertIn('https://www.googleapis.com/auth/colaboratory', auth_query['scope'][0])

        # CSRF mismatch in pasted callback URL is rejected cleanly.
        with self.assertRaises(ValueError):
            await mgr.exchange('colab', flow_id=started['flow_id'], code_or_url='http://127.0.0.1:8765/callback?state=wrong-state&code=abc')

        res = await mgr.exchange(
            'colab',
            flow_id=started['flow_id'],
            code_or_url=f"http://127.0.0.1:8765/callback?state={started['state']}&code=4/0A-good-code",
        )
        self.assertTrue(res['ok'])
        self.assertEqual(res['account_email'], 'colab-owner@example.com')
        self.assertTrue(res['status']['configured'])
        serialized = json.dumps(res)
        self.assertNotIn('1//0g-secret-refresh', serialized)
        self.assertNotIn('ya29.fresh-colab', serialized)

        # PKCE verifier sent to token exchange endpoint.
        token_post = self.calls[0]
        self.assertEqual(token_post[2]['grant_type'], 'authorization_code')
        self.assertIn('code_verifier', token_post[2])
        self.assertEqual(token_post[2]['code'], '4/0A-good-code')

        # Credential persisted with strict 0600 mode.
        self.assertTrue(self.colab_path.is_file())
        mode = stat.S_IMODE(self.colab_path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        saved = json.loads(self.colab_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['refresh_token'], '1//0g-secret-refresh')

        # Replay of consumed single-use flow is rejected.
        with self.assertRaises(ValueError):
            await mgr.exchange('colab', flow_id=started['flow_id'], code_or_url='4/0A-good-code')

    async def test_import_and_drive_test_and_disconnect(self):
        def responder(method, url, **kwargs):
            if method == 'POST' and url.endswith('/token'):
                return FakeResponse(200, {'access_token': 'ya29.drive-tok', 'expires_in': 3600})
            if method == 'GET' and 'userinfo' in url:
                return FakeResponse(200, {'email': 'drive-user@example.com'})
            if method == 'GET' and 'drive/v3/about' in url:
                return FakeResponse(200, {'user': {'emailAddress': 'drive-user@example.com', 'displayName': 'Raven Drive'}})
            raise AssertionError(f'Unexpected call: {method} {url}')

        mgr = self._manager(responder)
        imp = await mgr.import_credentials('drive', {
            'credential_json': json.dumps({
                'client_id': 'drive-cid-987654321',
                'client_secret': 'drive-sec-topsecret',
                'refresh_token': '1//0g-drive-ref',
            }),
            'verify': True,
        })
        self.assertTrue(imp['ok'])
        self.assertTrue(imp['verified'])
        self.assertTrue(imp['status']['configured'])
        self.assertNotIn('drive-sec-topsecret', json.dumps(imp))
        self.assertNotIn('1//0g-drive-ref', json.dumps(imp))

        # Patch runtime & drive token refresh in test_connection by pre-caching tokens via oauth file:
        # test_connection forces refresh via oauth.token(force=True) which uses httpx directly unless patched.
        orig_async_client = httpx.AsyncClient
        calls = self.calls
        try:
            httpx.AsyncClient = lambda *a, **kw: FakeAsyncClient(calls, responder)
            probe = await mgr.test_connection('drive')
        finally:
            httpx.AsyncClient = orig_async_client

        self.assertTrue(probe['ok'])
        self.assertEqual(probe['account_email'], 'drive-user@example.com')

        disc = mgr.disconnect('drive')
        self.assertTrue(disc['ok'])
        self.assertFalse(self.drive_path.exists())
        self.assertFalse(disc['status']['configured'])

    async def test_fastapi_callback_route_without_session_cookie(self):
        old_db, old_rt, old_dr, old_mgr = main.db, main.runtime, main.drive, main.oauth_flows
        db = Database(self.root / 'studio.sqlite3')
        db.save('settings', {'id': 'main', 'password_hash': 'x:y', 'colab_credentials_file': str(self.colab_path), 'drive_credentials_file': str(self.drive_path)})
        rt = Runtime(lambda: db.get('settings', 'main') or {})
        dr = Drive(lambda: db.get('settings', 'main') or {})

        def responder(method, url, **kwargs):
            if method == 'POST' and url.endswith('/token'):
                return FakeResponse(200, {'access_token': 'ya29.cb', 'refresh_token': '1//0g-cb-ref', 'expires_in': 3600})
            if method == 'GET' and 'userinfo' in url:
                return FakeResponse(200, {'email': 'cb@example.com'})
            raise AssertionError(f'Unexpected call: {method} {url}')

        mgr = OAuthFlowManager(rt, dr, http_client_factory=lambda timeout=30: FakeAsyncClient(self.calls, responder))
        main.db, main.runtime, main.drive, main.oauth_flows = db, rt, dr, mgr
        try:
            started = mgr.start(
                'colab',
                {'client_id': 'cb-cid-12345678', 'flow_mode': 'web_callback'},
                callback_url='https://draw.example.com/api/oauth/colab/callback',
            )
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url='https://draw.example.com') as client:
                # Authenticated OAuth status endpoint requires login
                unauth = await client.get('/api/oauth/status')
                self.assertEqual(unauth.status_code, 401)

                # Callback endpoint does not require session cookie; validated by single-use state token
                cb = await client.get('/api/oauth/colab/callback', params={'state': started['state'], 'code': 'cb-code-1'})
                self.assertEqual(cb.status_code, 200)
                self.assertIn('studio:oauth-complete', cb.text)
                self.assertIn('cb@example.com', cb.text)

            self.assertTrue(self.colab_path.is_file())
        finally:
            main.db, main.runtime, main.drive, main.oauth_flows = old_db, old_rt, old_dr, old_mgr
