"""Independent legacy Drive account; bounded streaming transfers, no CLI Drive coupling."""
from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from .runtime import OAuth, AdapterError, ROOT, private_json

API = 'https://www.googleapis.com/drive/v3'
FOLDER = 'application/vnd.google-apps.folder'
KINDS = ('checkpoints', 'loras', 'vae', 'embeddings', 'controlnet', 'upscale_models', 'diffusion_models', 'text_encoders', 'clip', 'clip_vision', 'seedvr2', 'latent_upscale_models', 'vfi_models')
MODEL_EXT = {'.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf', '.onnx'}
FIELDS = 'id,name,size,mimeType,modifiedTime,md5Checksum,sha256Checksum,parents,webViewLink'


def file_id(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise AdapterError('Drive文件ID无效')
    return value


def q(value):
    return str(value).replace('\\', '\\\\').replace("'", "\\'")


class Drive:
    def __init__(self, settings):
        self.settings = settings
        self.oauth = OAuth(settings, 'drive')
        self.folder_lock = asyncio.Lock()
        self._folders = None
        self._project_folders = {}
        self.data = Path(os.environ.get('DRAWING_DATA', str(ROOT / 'data')))

    def is_configured(self):
        return bool(self.oauth.path.is_file())

    def reset_folder_cache(self):
        self._folders = None
        self._project_folders.clear()

    async def access_token(self):
        """Internal only: short-lived token for trusted Colab downloads; never HTTP-return it."""
        return await self.oauth.token()

    async def _request(self, method, path, **kwargs):
        url = path if path.startswith('https://') else API + path
        host = urlsplit(url).hostname
        if host not in {'www.googleapis.com', 'content.googleapis.com'}:
            raise AdapterError('拒绝非Google Drive传输地址')
        headers = dict(kwargs.pop('headers', {}))
        token = await self.oauth.token()
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                    r = await client.request(method, url, headers={**headers, 'Authorization': 'Bearer ' + token}, **kwargs)
            except httpx.HTTPError:
                # Never replay file-creation POST after an ambiguous response.
                if method != 'GET':
                    raise AdapterError('Drive写请求网络异常，结果可能未知，请先扫描核对') from None
                if attempt == 3:
                    raise AdapterError('Drive网络请求失败') from None
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 401 and attempt < 3:
                token = await self.oauth.token(force=True)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and method == 'GET' and attempt < 3:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise AdapterError(f'Drive请求失败（HTTP {r.status_code}），检查授权范围和文件归属')
            return r
        raise AdapterError('Drive重试耗尽')

    async def _list(self, query):
        rows, cursor = [], None
        while True:
            params = {'q': query, 'pageSize': 1000, 'fields': f'nextPageToken,files({FIELDS})',
                      'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
            if cursor:
                params['pageToken'] = cursor
            result = (await self._request('GET', '/files', params=params)).json()
            rows.extend(result.get('files') or [])
            cursor = result.get('nextPageToken')
            if not cursor:
                return rows

    async def _folder(self, name, parent=None, create=True):
        query = f"trashed=false and mimeType='{FOLDER}' and name='{q(name)}'"
        query += f" and '{q(parent or 'root')}' in parents"
        matches = await self._list(query)
        if matches:
            return matches[0]['id']
        if not create:
            return None
        body = {'name': name, 'mimeType': FOLDER, 'parents': [parent or 'root']}
        return (await self._request('POST', '/files', json=body, params={'fields': 'id'})).json()['id']

    async def folders(self):
        async with self.folder_lock:
            if self._folders:
                return dict(self._folders)
            s = self.settings()
            root = s.get('drive_root_id') or await self._folder(s.get('drive_root_name') or 'DrawingStudio')
            mapping = {'root': root}
            for name in ('models', 'workflows', 'outputs', 'backups'):
                mapping[name] = await self._folder(name, root)
            for name in ('originals', 'upscaled', 'projects'):
                mapping[name] = await self._folder(name, mapping['outputs'])
            configured = s.get('drive_folders') or {}
            for kind in KINDS:
                mapping[kind] = configured.get(kind) or await self._folder(kind, mapping['models'])
            self._folders = mapping
            return dict(mapping)

    async def project_folder(self, name, *, create=True):
        name = str(name or '').strip()
        if not name:
            raise AdapterError('项目名称不能为空')
        cached = self._project_folders.get(name)
        if cached:
            return cached
        folders = await self.folders()
        projects_root = folders['projects']
        async with self.folder_lock:
            cached = self._project_folders.get(name)
            if cached:
                return cached
            fid = await self._folder(name, projects_root, create=create)
            if fid:
                self._project_folders[name] = fid
            return fid

    async def list_project_folders(self):
        folders = await self.folders()
        projects_root = folders['projects']
        query = f"trashed=false and mimeType='{FOLDER}' and '{q(projects_root)}' in parents"
        rows = await self._list(query)
        rows.sort(key=lambda r: (r.get('name') or '').lower())
        for row in rows:
            name = str(row.get('name') or '').strip()
            if name and row.get('id'):
                self._project_folders[name] = row['id']
        return rows

    async def list_folder_files(self, folder_id, *, recursive=True):
        root_id = file_id(folder_id)
        frontier = [root_id]
        visited = set()
        files = []
        while frontier:
            fid = frontier.pop(0)
            if fid in visited:
                continue
            visited.add(fid)
            rows = await self._list(f"trashed=false and '{q(fid)}' in parents")
            for row in rows:
                if row.get('mimeType') == FOLDER:
                    if recursive and row.get('id'):
                        frontier.append(file_id(row['id']))
                else:
                    files.append(row)
        return files

    async def folder_has_children(self, folder_id):
        fid = file_id(folder_id)
        params = {
            'q': f"trashed=false and '{q(fid)}' in parents",
            'pageSize': 1,
            'fields': 'files(id)',
            'supportsAllDrives': 'true',
            'includeItemsFromAllDrives': 'true',
        }
        result = (await self._request('GET', '/files', params=params)).json()
        return bool(result.get('files'))

    async def scan(self):
        """Read-only scan. Finds legacy folders plus explicit mappings; creates no folders."""
        s = self.settings()
        roots = s.get('drive_folders') or {}
        allrows = await self._list('trashed=false')
        folders = {row['id']: row for row in allrows if row.get('mimeType') == FOLDER}
        kind_by_folder = {str(fid): kind for kind, fid in roots.items() if kind in KINDS and fid}
        for fid, row in folders.items():
            name = row.get('name', '').lower()
            if name in KINDS:
                kind_by_folder.setdefault(fid, name)
            elif name in {'lora', 'loras'}:
                kind_by_folder.setdefault(fid, 'loras')
            elif name in {'checkpoint', 'checkpoints'}:
                kind_by_folder.setdefault(fid, 'checkpoints')
        scope_roots = set(str(x) for x in roots.values() if x)
        if s.get('drive_root_id'):
            scope_roots.add(str(s['drive_root_id']))
        elif not scope_roots:
            root_name = s.get('drive_root_name') or 'DrawingStudio'
            scope_roots.update(fid for fid, row in folders.items() if row.get('name') == root_name)
        # No implicit adoption of unrelated projects. Missing DrawingStudio means an empty scan.
        allowed = set(scope_roots)
        changed = True
        while changed:
            changed = False
            for fid, folder in folders.items():
                if fid not in allowed and any(p in allowed for p in folder.get('parents', [])):
                    allowed.add(fid)
                    changed = True
        models = []
        for row in allrows:
            if not any(p in allowed for p in row.get('parents', [])):
                continue
            if Path(row.get('name', '')).suffix.lower() not in MODEL_EXT:
                continue
            kind, trail, frontier, seen = None, [], list(row.get('parents') or []), set()
            while frontier:
                fid = frontier.pop(0)
                if fid in seen:
                    continue
                seen.add(fid)
                kind = kind or kind_by_folder.get(fid)
                parent = folders.get(fid)
                if parent:
                    trail.append(parent.get('name', ''))
                    frontier.extend(parent.get('parents') or [])
            models.append({**row, 'id': row['id'], 'drive_file_id': row['id'], 'file_id': row['id'],
                'name': row['name'], 'filename': row['name'], 'kind': kind or 'other', 'type': kind or 'other',
                'size': int(row.get('size') or 0), 'modified_at': row.get('modifiedTime'),
                'md5': row.get('md5Checksum'), 'sha256': row.get('sha256Checksum'),
                'drive_path': '/'.join(reversed(trail)) + '/' + row['name'], 'status': 'drive_only'})
        private_json(self.data / 'drive-index.json', models)
        return models

    async def metadata(self, ident):
        return (await self._request('GET', '/files/' + file_id(ident), params={'fields': FIELDS, 'supportsAllDrives': 'true'})).json()

    async def download(self, ident, dest):
        ident, dest = file_id(ident), Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise AdapterError('下载目标不得为符号链接')
        meta = await self.metadata(ident)
        expected = int(meta.get('size') or 0)
        checksum = meta.get('md5Checksum')
        if dest.is_file() and dest.stat().st_size == expected and (not checksum or await asyncio.to_thread(_digest, dest) == checksum):
            return str(dest)
        partial = dest.with_name(dest.name + '.part')
        marker = partial.with_name(partial.name + '.json')
        fingerprint = {k: meta.get(k) for k in ('id', 'size', 'modifiedTime', 'md5Checksum')}
        try:
            previous = json.loads(marker.read_text())
        except (OSError, ValueError):
            previous = None
        if previous != fingerprint:
            partial.unlink(missing_ok=True)
        if partial.is_symlink():
            raise AdapterError('临时下载文件不得为符号链接')
        private_json(marker, fingerprint)
        for attempt in range(5):
            offset = partial.stat().st_size if partial.exists() else 0
            token = await self.oauth.token(force=attempt > 0)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, read=180), follow_redirects=True) as client:
                    async with client.stream('GET', API + '/files/' + ident, params={'alt': 'media', 'supportsAllDrives': 'true'},
                        headers={'Authorization': 'Bearer ' + token, **({'Range': f'bytes={offset}-'} if offset else {})}) as r:
                        if r.status_code in (401, 429, 500, 502, 503, 504):
                            await asyncio.sleep(2 ** attempt)
                            continue
                        if r.status_code not in (200, 206):
                            raise AdapterError(f'Drive下载失败（HTTP {r.status_code}）')
                        if r.status_code == 206 and not r.headers.get('content-range', '').startswith(f'bytes {offset}-'):
                            raise AdapterError('Drive断点响应偏移不符')
                        if r.status_code == 200:
                            offset = 0
                        with partial.open('ab' if offset else 'wb') as out:
                            async for chunk in r.aiter_bytes(1024 * 1024):
                                out.write(chunk)
                if expected and partial.stat().st_size != expected:
                    raise httpx.ReadError('incomplete')
                if checksum and await asyncio.to_thread(_digest, partial) != checksum:
                    partial.unlink(missing_ok=True)
                    raise httpx.ReadError('checksum')
                partial.replace(dest)
                marker.unlink(missing_ok=True)
                return str(dest)
            except httpx.HTTPError:
                if attempt == 4:
                    raise AdapterError('Drive下载中断，保留.part供重试') from None
                await asyncio.sleep(2 ** attempt)
        raise AdapterError('Drive下载未完成')

    async def upload(self, path, parent=None, name=None):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise AdapterError('上传源文件无效')
        parent = file_id(parent or (await self.folders())['originals'])
        name = name or path.name
        size = path.stat().st_size
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        digest = await asyncio.to_thread(_digest, path)
        # Idempotent retries after lost completion response, scoped to this parent/name/content.
        query = f"trashed=false and '{q(parent)}' in parents and name='{q(name)}'"
        for row in await self._list(query):
            if row.get('md5Checksum') == digest and int(row.get('size') or 0) == size:
                return row
        session = await self._request('POST', 'https://www.googleapis.com/upload/drive/v3/files',
            params={'uploadType': 'resumable', 'fields': FIELDS, 'supportsAllDrives': 'true'},
            json={'name': name, 'parents': [parent]},
            headers={'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(size)})
        location = session.headers.get('location', '')
        parsed = urlsplit(location)
        if parsed.scheme != 'https' or parsed.hostname not in {'www.googleapis.com', 'content.googleapis.com'} or not parsed.path.startswith('/upload/drive/v3/'):
            raise AdapterError('Drive上传会话地址无效')
        offset, stalled, result = 0, 0, None
        with path.open('rb') as source:
            while offset < size or size == 0:
                source.seek(offset)
                chunk = source.read(8 * 1024 * 1024)
                if size and not chunk:
                    raise AdapterError('上传期间源文件改变')
                previous = offset
                try:
                    r = await self._request('PUT', location, content=chunk,
                        headers={'Content-Type': mime, 'Content-Range': f'bytes {offset}-{offset + len(chunk) - 1}/{size}' if size else 'bytes */0'})
                except AdapterError:
                    r = await self._request('PUT', location, content=b'', headers={'Content-Range': f'bytes */{size}'})
                if r.status_code in (200, 201):
                    result = r.json()
                    break
                if r.status_code != 308:
                    raise AdapterError('Drive上传未确认完成')
                received = re.fullmatch(r'bytes=0-(\d+)', r.headers.get('range', ''))
                offset = int(received.group(1)) + 1 if received else 0
                if not 0 <= offset <= size:
                    raise AdapterError('Drive上传确认范围无效')
                stalled = stalled + 1 if offset <= previous else 0
                if stalled > 4:
                    raise AdapterError('Drive上传未推进，请重试')
        if not result or not result.get('id') or int(result.get('size') or 0) != size or result.get('md5Checksum') != digest:
            raise AdapterError('Drive上传后大小/校验和不符；保留源文件')
        return result

    async def move_file(self, ident, new_parent_id, old_parent_ids=None):
        ident = file_id(ident)
        new_parent_id = file_id(new_parent_id)
        if old_parent_ids is None:
            meta = await self.metadata(ident)
            old_parent_ids = meta.get('parents') or []
        old_parents = [str(p) for p in old_parent_ids if p]
        remove = [file_id(p) for p in old_parents if p != new_parent_id]
        already_in = new_parent_id in old_parents
        if already_in and not remove:
            return {'id': ident, 'parents': [new_parent_id]}
        params = {'fields': FIELDS, 'supportsAllDrives': 'true'}
        if not already_in:
            params['addParents'] = new_parent_id
        if remove:
            params['removeParents'] = ','.join(remove)
        return (await self._request('PATCH', '/files/' + ident, params=params)).json()

    async def rename_file(self, ident, new_name):
        ident = file_id(ident)
        return (await self._request(
            'PATCH',
            '/files/' + ident,
            json={'name': str(new_name)},
            params={'fields': FIELDS, 'supportsAllDrives': 'true'},
        )).json()

    async def download_text(self, ident, max_bytes=5 * 1024 * 1024):
        ident = file_id(ident)
        for attempt in range(4):
            token = await self.oauth.token(force=attempt > 0)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=60), follow_redirects=True) as client:
                    async with client.stream(
                        'GET',
                        API + '/files/' + ident,
                        params={'alt': 'media', 'supportsAllDrives': 'true'},
                        headers={'Authorization': 'Bearer ' + token},
                    ) as r:
                        if r.status_code in (401, 429, 500, 502, 503, 504) and attempt < 3:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        if r.status_code >= 400:
                            raise AdapterError(f'Drive读取文件失败（HTTP {r.status_code}）')
                        buf = bytearray()
                        async for chunk in r.aiter_bytes(256 * 1024):
                            buf.extend(chunk)
                            if len(buf) > max_bytes:
                                raise AdapterError('Drive元数据文件过大')
                        return buf.decode('utf-8', errors='replace')
            except httpx.HTTPError:
                if attempt == 3:
                    raise AdapterError('Drive读取文件网络异常') from None
                await asyncio.sleep(2 ** attempt)
        raise AdapterError('Drive读取文件未完成')

    async def trash(self, ident):
        await self._request('PATCH', '/files/' + file_id(ident), json={'trashed': True}, params={'supportsAllDrives': 'true'})
        return {'id': ident, 'trashed': True}


def _digest(path):
    h = hashlib.md5()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()
