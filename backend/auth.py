"""Password login and revocable bearer keys for a single owner."""
import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()


class Auth:
    def __init__(self, db):
        self.db = db
        self.sessions = {}

    def login(self, password):
        settings = self.db.get('settings', 'main') or {}
        saved = settings.get('password_hash', '')
        if not saved or not hmac.compare_digest(password_hash(password, saved.split(':')[0]), saved):
            raise HTTPException(401, '密码错误')
        token = secrets.token_urlsafe(32)
        self.sessions[digest(token)] = time.time() + 86400 * 30
        return token

    def check(self, request):
        session = request.cookies.get('drawing_session', '')
        if session and self.sessions.get(digest(session), 0) > time.time():
            return 'session'
        header = request.headers.get('authorization', '')
        if header.startswith('Bearer '):
            token_hash = digest(header[7:])
            for key in self.db.list('keys'):
                if not key.get('revoked') and hmac.compare_digest(key.get('hash', ''), token_hash):
                    return key['id']
        raise HTTPException(401, '请先登录或提供有效 API Key')

    def key(self, name):
        token = 'ds-' + secrets.token_urlsafe(32)
        row = self.db.save('keys', {'name': name or 'API Key', 'hash': digest(token), 'prefix': token[:10], 'revoked': False})
        return {k: v for k, v in row.items() if k != 'hash'} | {'key': token}
