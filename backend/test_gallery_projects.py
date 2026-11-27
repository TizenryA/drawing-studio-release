"""Tests for Draw 1.0.37: gallery pagination, Drive project folders (Scheme A), & auto-prune."""
import asyncio
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from .database import Database
from .drive import Drive
from .gallery import Gallery
from .tasks import Tasks
from .workflows import WorkflowStore


def png_bytes(size=(40, 60), color=(20, 120, 220)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


class FakeDrive:
    def __init__(self):
        self.root_id = 'drv-root'
        self.outputs_id = 'drv-outputs'
        self.originals_id = 'drv-originals'
        self.upscaled_id = 'drv-upscaled'
        self.projects_root_id = 'drv-projects'
        self.project_folders = {}  # name -> id
        self.folder_names = {}  # id -> name
        self.files = {}  # id -> {'id', 'name', 'parents', 'mimeType', 'modifiedTime', 'bytes'}
        self.upload_calls = []
        self.move_calls = []
        self.rename_calls = []
        self.trash_calls = []
        self.download_calls = []
        self.fail_upload = False
        self._seq = 0

    def is_configured(self):
        return True

    def reset_folder_cache(self):
        pass

    async def folders(self):
        return {
            'root': self.root_id,
            'outputs': self.outputs_id,
            'originals': self.originals_id,
            'upscaled': self.upscaled_id,
            'projects': self.projects_root_id,
        }

    async def project_folder(self, name, *, create=True):
        if name in self.project_folders:
            return self.project_folders[name]
        if not create:
            return None
        self._seq += 1
        fid = f'drv-proj-{self._seq}'
        self.project_folders[name] = fid
        self.folder_names[fid] = name
        return fid

    async def list_project_folders(self):
        return [{'id': fid, 'name': name} for name, fid in sorted(self.project_folders.items())]

    async def list_folder_files(self, folder_id, *, recursive=True):
        return [
            {k: v for k, v in row.items() if k != 'bytes'}
            for row in self.files.values()
            if folder_id in (row.get('parents') or [])
        ]

    async def folder_has_children(self, folder_id):
        return any(folder_id in (row.get('parents') or []) for row in self.files.values())

    async def upload(self, path, parent=None, name=None):
        if self.fail_upload:
            raise RuntimeError('simulated drive upload failure')
        self._seq += 1
        fid = f'drv-file-{self._seq}'
        fname = name or Path(path).name
        data = Path(path).read_bytes()
        self.files[fid] = {
            'id': fid,
            'name': fname,
            'parents': [parent],
            'mimeType': 'application/json' if fname.endswith('.json') else 'image/png',
            'modifiedTime': '2026-11-26T00:00:00Z',
            'bytes': data,
        }
        self.upload_calls.append((fid, parent, fname))
        return {'id': fid, 'name': fname, 'parents': [parent]}

    async def move_file(self, ident, new_parent_id, old_parent_ids=None):
        self.move_calls.append((ident, new_parent_id))
        if ident in self.files:
            self.files[ident]['parents'] = [new_parent_id]
        return {'id': ident, 'parents': [new_parent_id]}

    async def rename_file(self, ident, new_name):
        self.rename_calls.append((ident, new_name))
        old_name = self.folder_names.get(ident)
        if old_name and old_name in self.project_folders:
            del self.project_folders[old_name]
        self.project_folders[new_name] = ident
        self.folder_names[ident] = new_name
        return {'id': ident, 'name': new_name}

    async def download_text(self, ident, max_bytes=5 * 1024 * 1024):
        return self.files[ident]['bytes'].decode('utf-8')

    async def download(self, ident, dest):
        self.download_calls.append(ident)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[ident]['bytes'])
        return str(dest)

    async def trash(self, ident):
        self.trash_calls.append(ident)
        self.files.pop(ident, None)
        if ident in self.folder_names:
            name = self.folder_names.pop(ident)
            self.project_folders.pop(name, None)
        return {'id': ident, 'trashed': True}


class GalleryProjectsAndPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_summary_stripping_and_backward_compat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            cfg = {'prune_local_after_archive': False}
            fake_drive = FakeDrive()
            gallery = Gallery(db, fake_drive, root, settings=lambda: cfg)

            for i in range(5):
                project_name = '角色设定' if i < 3 else ''
                rec = await gallery.archive(
                    f't-{i}',
                    png_bytes(),
                    f'img-{i}.png',
                    {
                        'prompt': f'prompt number {i}',
                        'model': 'moss-sft',
                        'loras': [{'id': 'lora-a', 'name': 'style-demo', 'strength': 0.8}] if i % 2 == 0 else [],
                        'model_snapshot': [{'id': 'moss-sft', 'name': 'MOSS SFT', 'filename': 'moss_sft.safetensors'}],
                        'api_workflow': {'1': {'class_type': 'KSampler', 'inputs': {'seed': i}}},
                        'project': project_name,
                    },
                )
                db.save('gallery', {'id': rec['id'], 'created_at': f'2026-11-2{i}T10:00:00Z'})
                if i == 1:
                    db.save('gallery', {'id': rec['id'], 'favorite': True, 'tags': ['cover']})

            # Wait for background archive tasks
            await asyncio.sleep(0.05)

            from . import main
            with patch.multiple(main, db=db, gallery=gallery, drive=fake_drive, DATA=root), \
                 patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    # 1. Paginated envelope
                    r1 = await client.get('/api/gallery?page=1&limit=2&order=desc')
                    self.assertEqual(r1.status_code, 200)
                    p1 = r1.json()
                    self.assertEqual(p1['total'], 5)
                    self.assertEqual(p1['page'], 1)
                    self.assertEqual(p1['limit'], 2)
                    self.assertEqual(p1['total_pages'], 3)
                    self.assertTrue(p1['has_more'])
                    self.assertEqual(len(p1['items']), 2)
                    # Heavy workflow blobs stripped from list items, preview_url present
                    self.assertNotIn('api_workflow', p1['items'][0]['params'])
                    self.assertNotIn('model_snapshot', p1['items'][0]['params'])
                    self.assertEqual(p1['items'][0]['project'], '')
                    self.assertEqual(p1['items'][0]['preview_url'], f"/api/gallery/{p1['items'][0]['id']}/preview")

                    # 2. Legacy unpaginated array format
                    r_legacy = await client.get('/api/gallery')
                    self.assertEqual(r_legacy.status_code, 200)
                    arr = r_legacy.json()
                    self.assertIsInstance(arr, list)
                    self.assertEqual(len(arr), 5)
                    self.assertNotIn('api_workflow', arr[0]['params'])

                    # 3. Project filter + unassigned + album alias + lora + model + favorite + tag
                    r_proj = await client.get('/api/gallery?project=角色设定&page=1&limit=10')
                    self.assertEqual(r_proj.json()['total'], 3)
                    r_album = await client.get('/api/gallery?album=角色设定&page=1&limit=10')
                    self.assertEqual(r_album.json()['total'], 3)
                    r_none = await client.get('/api/gallery?project=__none__&page=1&limit=10')
                    self.assertEqual(r_none.json()['total'], 2)
                    r_lora = await client.get('/api/gallery?lora=lora-a&page=1&limit=10')
                    self.assertEqual(r_lora.json()['total'], 3)
                    r_model = await client.get('/api/gallery?model=moss_sft.safetensors&page=1&limit=10')
                    self.assertEqual(r_model.json()['total'], 3 + 2)
                    r_fav = await client.get('/api/gallery?favorite=true&tag=cover&page=1&limit=10')
                    self.assertEqual(r_fav.json()['total'], 1)

                    # 4. Detail endpoint still includes full api_workflow & related
                    target_id = p1['items'][0]['id']
                    detail = (await client.get(f'/api/gallery/{target_id}')).json()
                    self.assertIn('api_workflow', detail['params'])
                    self.assertIn('related', detail)

                    # 5. Fast /api/tasks works with db.ids('gallery')
                    db.save('tasks', {'id': 'task-ok', 'status': 'completed', 'results': [{'id': target_id}]})
                    tasks_res = await client.get('/api/tasks')
                    self.assertEqual(tasks_res.status_code, 200)
                    self.assertEqual(len(tasks_res.json()), 1)

    async def test_drive_scheme_a_auto_prune_move_rename_delete_and_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            cfg = {'prune_local_after_archive': True}
            fake_drive = FakeDrive()
            gallery = Gallery(db, fake_drive, root, settings=lambda: cfg)

            # Archive image under project "海报合集" with default auto-prune=True
            rec = await gallery.archive('task-1', png_bytes((120, 180)), 'hero.png', {'prompt': 'moss art', 'project': '海报合集'})
            await asyncio.sleep(0.05)

            saved = db.get('gallery', rec['id'])
            self.assertEqual(saved['archive_status'], 'saved')
            self.assertEqual(saved['project'], '海报合集')
            self.assertEqual(saved['album'], '海报合集')
            self.assertFalse(saved['local_cached'])
            # Original + json pruned, thumbnail retained
            self.assertFalse(Path(saved['local_path']).exists())
            self.assertFalse((root / 'images' / (saved['id'] + '.json')).exists())
            self.assertTrue((root / 'thumbnails' / (saved['id'] + '.webp')).is_file())
            # Uploaded into Drive outputs/projects/海报合集/
            proj_folder_id = fake_drive.project_folders['海报合集']
            self.assertEqual(saved['drive_parent_id'], proj_folder_id)
            self.assertIn(proj_folder_id, fake_drive.files[saved['drive_file_id']]['parents'])

            # Move to another project -> Drive move_file called for both media + metadata
            from . import main
            with patch.multiple(main, db=db, gallery=gallery, drive=fake_drive, DATA=root), \
                 patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    # Bad project name rejected
                    bad = await client.post('/api/gallery/projects', json={'name': 'bad/name'})
                    self.assertEqual(bad.status_code, 400)

                    # Move item via PATCH
                    mv = await client.patch(f"/api/gallery/{saved['id']}", json={'project': '角色设定'})
                    self.assertEqual(mv.status_code, 200)
                    self.assertEqual(mv.json()['project'], '角色设定')
                    new_folder_id = fake_drive.project_folders['角色设定']
                    self.assertIn(new_folder_id, fake_drive.files[saved['drive_file_id']]['parents'])
                    self.assertIn(new_folder_id, fake_drive.files[saved['metadata_file_id']]['parents'])

                    # Rename project -> renames Drive folder and updates items
                    rn = await client.patch('/api/gallery/projects/角色设定', json={'name': '设定V2'})
                    self.assertEqual(rn.status_code, 200)
                    self.assertEqual(db.get('gallery', saved['id'])['project'], '设定V2')
                    self.assertIn('设定V2', fake_drive.project_folders)
                    self.assertNotIn('角色设定', fake_drive.project_folders)

                    # On-demand download restores from Drive, and manual prune clears it again
                    dl = await client.get(saved['url'])
                    self.assertEqual(dl.status_code, 200)
                    self.assertTrue(Path(saved['local_path']).is_file())
                    prune = await client.post('/api/gallery/prune-local')
                    self.assertEqual(prune.status_code, 200)
                    self.assertEqual(prune.json()['pruned'], 1)
                    self.assertFalse(Path(saved['local_path']).exists())

                    # Delete empty project "海报合集" trashes empty Drive folder
                    del_empty = await client.delete('/api/gallery/projects/海报合集')
                    self.assertEqual(del_empty.status_code, 200)
                    self.assertNotIn('海报合集', fake_drive.project_folders)

                    # Seed an external file directly into a new Drive project folder "外部漫画" (without sidecar)
                    ext_fid = await fake_drive.project_folder('外部漫画', create=True)
                    ext_bytes = png_bytes((80, 100), (240, 40, 40))
                    fake_drive.files['drv-ext-1'] = {
                        'id': 'drv-ext-1',
                        'name': 'page01.png',
                        'parents': [ext_fid],
                        'mimeType': 'image/png',
                        'modifiedTime': '2026-11-26T01:00:00Z',
                        'bytes': ext_bytes,
                    }
                    sync_res = await client.post('/api/gallery/sync', json={})
                    self.assertEqual(sync_res.status_code, 200)
                    self.assertEqual(sync_res.json()['added'], 1)
                    self.assertEqual(fake_drive.download_calls.count('drv-ext-1'), 0)

                    synced_list = (await client.get('/api/gallery?project=外部漫画&page=1&limit=10')).json()
                    self.assertEqual(synced_list['total'], 1)
                    ext_item = synced_list['items'][0]
                    self.assertFalse(ext_item['local_cached'])

                    # Request thumbnail for Drive-only synced image: generates webp, prunes temp original, sets immutable cache
                    thumb_res = await client.get(ext_item['thumbnail_url'])
                    self.assertEqual(thumb_res.status_code, 200)
                    self.assertEqual(thumb_res.headers['content-type'], 'image/webp')
                    self.assertIn('immutable', thumb_res.headers.get('cache-control', ''))
                    self.assertTrue((root / 'thumbnails' / (ext_item['id'] + '.webp')).is_file())
                    self.assertFalse(Path(ext_item['local_path']).exists())
                    refetched = (await client.get(f"/api/gallery/{ext_item['id']}")).json()
                    self.assertEqual((refetched['width'], refetched['height']), (80, 100))

    async def test_failed_archive_never_prunes_local_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            cfg = {'prune_local_after_archive': True}
            fake_drive = FakeDrive()
            fake_drive.fail_upload = True
            gallery = Gallery(db, fake_drive, root, settings=lambda: cfg)

            rec = await gallery.archive('t-fail', png_bytes(), 'keep.png', {'prompt': 'keep'})
            await asyncio.sleep(0.05)
            after = db.get('gallery', rec['id'])
            self.assertEqual(after['archive_status'], 'retry')
            self.assertTrue(Path(after['local_path']).is_file())

    async def test_task_submit_inherits_and_persists_project_and_concat_keeps_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            store = WorkflowStore(root)
            cfg = {'prune_local_after_archive': False}
            fake_drive = FakeDrive()
            gallery = Gallery(db, fake_drive, root, settings=lambda: cfg)

            src = await gallery.archive('t0', png_bytes(), 'base.png', {'prompt': 'base', 'project': '连载A'})
            await asyncio.sleep(0.05)

            db.save('models', {'id': 'ckpt-1', 'name': 'sdxl.safetensors', 'filename': 'sdxl.safetensors', 'kind': 'checkpoints'})
            t = Tasks(db, None, None, fake_drive, store, lambda *a: None, gallery=gallery)
            sub_task = await t.submit({
                'mode': 'img2img',
                'model': 'ckpt-1',
                'prompt': 'edit',
                'parent_id': src['id'],
            })
            self.assertEqual(sub_task['params']['project'], '连载A')

            # Video concat inherits project
            v1_path = root / 'v1.mp4'
            v2_path = root / 'v2.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x48:d=0.2', '-y', str(v1_path)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=64x48:d=0.2', '-y', str(v2_path)], check=True)
            v1 = await gallery.archive('tv1', v1_path.read_bytes(), 'v1.mp4', {'project': '视频项目'})
            v2 = await gallery.archive('tv2', v2_path.read_bytes(), 'v2.mp4', {'project': '视频项目'})
            await asyncio.sleep(0.05)

            from . import main
            with patch.multiple(main, db=db, gallery=gallery, drive=fake_drive, DATA=root), \
                 patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    # Import with project
                    imp = await client.post(
                        '/api/gallery/import',
                        files={'file': ('imp.png', png_bytes(), 'image/png')},
                        data={'metadata': json.dumps({'prompt': 'imported'}), 'project': '导入项目'},
                    )
                    self.assertEqual(imp.status_code, 200)
                    self.assertEqual(imp.json()['project'], '导入项目')

                    # Batch project move
                    bm = await client.post('/api/gallery/batch', json={'ids': [imp.json()['id']], 'action': 'project', 'value': '连载A'})
                    self.assertEqual(bm.status_code, 200)
                    self.assertEqual(db.get('gallery', imp.json()['id'])['project'], '连载A')

                    res = await client.post('/api/gallery/concat', json={'ids': [v1['id'], v2['id']], 'trim_overlap_frames': 0})
                    self.assertEqual(res.status_code, 200)
                    self.assertEqual(res.json()['project'], '视频项目')
                    await asyncio.sleep(0.05)

    async def test_drive_class_folders_and_move_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drv = Drive(lambda: {'drive_root_name': 'DrawingStudio'})
            calls = []

            async def fake_request(method, path, **kwargs):
                calls.append((method, path, kwargs))
                class Resp:
                    def __init__(self, data): self._data = data
                    def json(self): return self._data
                if method == 'GET' and path == '/files':
                    q_str = kwargs.get('params', {}).get('q', '')
                    if "name='DrawingStudio'" in q_str:
                        return Resp({'files': [{'id': 'f-root'}]})
                    if "name='outputs'" in q_str:
                        return Resp({'files': [{'id': 'f-outputs'}]})
                    if "name='projects'" in q_str:
                        return Resp({'files': [{'id': 'f-projects'}]})
                    if "name='myproj'" in q_str:
                        return Resp({'files': [{'id': 'f-myproj'}]})
                    return Resp({'files': [{'id': 'f-sub'}]})
                if method == 'GET' and path == '/files/file-1':
                    return Resp({'id': 'file-1', 'parents': ['f-old']})
                if method == 'PATCH' and path == '/files/file-1':
                    return Resp({'id': 'file-1', 'parents': ['f-myproj']})
                return Resp({})

            drv._request = fake_request
            folders = await drv.folders()
            self.assertEqual(folders['projects'], 'f-projects')
            pf = await drv.project_folder('myproj')
            self.assertEqual(pf, 'f-myproj')
            moved = await drv.move_file('file-1', 'f-myproj')
            self.assertEqual(moved['parents'], ['f-myproj'])
            patch_call = next(c for c in calls if c[0] == 'PATCH')
            self.assertEqual(patch_call[2]['params']['addParents'], 'f-myproj')
            self.assertEqual(patch_call[2]['params']['removeParents'], 'f-old')

    async def test_hd_preview_immutable_headers_and_no_sync_trim_on_local_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            cfg = {'prune_local_after_archive': True}
            fake_drive = FakeDrive()
            gallery = Gallery(db, fake_drive, root, settings=lambda: cfg)

            # 1. Archived image generates both thumbnail and HD preview before auto-prune removes PNG
            rec = await gallery.archive('t-prev', png_bytes((120, 180)), 'hero.png', {'prompt': 'hd preview'})
            await asyncio.sleep(0.05)
            prev_path = root / 'previews' / (rec['id'] + '.webp')
            self.assertTrue(prev_path.is_file())
            self.assertFalse(Path(rec['local_path']).exists())

            from . import main
            with patch.multiple(main, db=db, gallery=gallery, drive=fake_drive, DATA=root), \
                 patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    # /preview serves cached WebP with immutable header and 0 Drive downloads
                    prev_res = await client.get(f"/api/gallery/{rec['id']}/preview")
                    self.assertEqual(prev_res.status_code, 200)
                    self.assertEqual(prev_res.headers['content-type'], 'image/webp')
                    self.assertIn('immutable', prev_res.headers.get('cache-control', ''))
                    self.assertEqual(len(fake_drive.download_calls), 0)

                    # Large Drive-only image (2000x1000) scales down to 1600x800 WebP on first /preview, caches on disk
                    large_bytes = png_bytes((2000, 1000), (80, 160, 220))
                    proj_id = await fake_drive.project_folder('高清测试', create=True)
                    fake_drive.files['drv-large'] = {
                        'id': 'drv-large',
                        'name': 'wide.png',
                        'parents': [proj_id],
                        'mimeType': 'image/png',
                        'modifiedTime': '2026-11-26T02:00:00Z',
                        'bytes': large_bytes,
                    }
                    sync_res = await client.post('/api/gallery/sync', json={})
                    self.assertEqual(sync_res.status_code, 200)
                    large_row = next(r for r in db.list('gallery') if r.get('drive_file_id') == 'drv-large')
                    self.assertEqual(large_row['preview_url'], f"/api/gallery/{large_row['id']}/preview")

                    r_prev1 = await client.get(large_row['preview_url'])
                    self.assertEqual(r_prev1.status_code, 200)
                    self.assertEqual(r_prev1.headers['content-type'], 'image/webp')
                    self.assertIn('immutable', r_prev1.headers.get('cache-control', ''))
                    self.assertEqual(fake_drive.download_calls.count('drv-large'), 1)
                    with Image.open(io.BytesIO(r_prev1.content)) as im:
                        self.assertEqual(im.size, (1600, 800))

                    # Second /preview hit does 0 extra Drive downloads
                    r_prev2 = await client.get(large_row['preview_url'])
                    self.assertEqual(r_prev2.status_code, 200)
                    self.assertEqual(fake_drive.download_calls.count('drv-large'), 1)

                    # Small image (80x100) is NOT upscaled
                    small_row = await gallery.archive('t-small', png_bytes((80, 100)), 'small.png', {'prompt': 'tiny'})
                    await asyncio.sleep(0.05)
                    r_small = await client.get(small_row['preview_url'])
                    with Image.open(io.BytesIO(r_small.content)) as im_small:
                        self.assertEqual(im_small.size, (80, 100))

                    # /download and /media return immutable Cache-Control and local cache hit NEVER runs trim_local_cache synchronously
                    with patch.object(gallery, 'trim_local_cache', wraps=gallery.trim_local_cache) as trim_spy:
                        r_dl = await client.get(large_row['url'])
                        r_media = await client.get(f"/api/gallery/{large_row['id']}/media")
                        await asyncio.sleep(0.02)
                        self.assertEqual(r_dl.status_code, 200)
                        self.assertEqual(r_media.status_code, 200)
                        self.assertIn('immutable', r_dl.headers.get('cache-control', ''))
                        self.assertIn('immutable', r_media.headers.get('cache-control', ''))
                        self.assertEqual(trim_spy.call_count, 0)

                    # Deleting removes preview webp as well
                    large_prev_file = root / 'previews' / (large_row['id'] + '.webp')
                    self.assertTrue(large_prev_file.is_file())
                    del_res = await client.delete(f"/api/gallery/{large_row['id']}")
                    self.assertEqual(del_res.status_code, 200)
                    self.assertFalse(large_prev_file.is_file())

                    # trim_preview_cache evicts oldest preview while keeping exclude_id
                    removed = gallery.trim_preview_cache(max_bytes=1, exclude_id=small_row['id'])
                    self.assertGreaterEqual(removed, 1)
                    self.assertTrue((root / 'previews' / (small_row['id'] + '.webp')).is_file())
                    self.assertFalse((root / 'previews' / (rec['id'] + '.webp')).is_file())
                    await gallery.close()


if __name__ == '__main__':
    unittest.main()
