"""Tests for Draw 1.0.40: indexed/paginated /api/tasks, SSE events & combined Comfy poll_task_state."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from .database import Database, task_summary
from .tasks import Tasks
from .workflows import WorkflowStore


class TasksPerfAndSseTests(unittest.IsolatedAsyncioTestCase):
    async def test_tasks_index_backfill_pagination_filters_and_active_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')

            giant_workflow = {str(i): {'class_type': 'KSampler', 'inputs': {'seed': i, 'pad': 'x' * 500}} for i in range(40)}
            giant_snapshot = [{'id': f'm{i}', 'filename': f'f{i}.safetensors', 'notes': 'y' * 400} for i in range(10)]

            # Seed gallery items
            db.save('gallery', {'id': 'gal-1', 'filename': 'out1.png', 'width': 1024, 'height': 1536, 'media_type': 'image'})
            db.save('gallery', {'id': 'gal-v', 'filename': 'vid1.mp4', 'width': 864, 'height': 480, 'media_type': 'video', 'duration': 5.0})

            # 1) Completed task with deleted gallery output (must be hidden)
            db.save('tasks', {
                'id': 't-hidden',
                'status': 'completed',
                'params': {'prompt': 'deleted output', 'model': 'm1', 'api_workflow': giant_workflow},
                'results': [{'id': 'gal-deleted'}],
                'prompt': giant_workflow,
            })
            # 2) Completed image task
            db.save('tasks', {
                'id': 't-img',
                'status': 'completed',
                'stage': 'completed',
                'media_kind': 'image',
                'cu_actual_delta': 0.25,
                'params': {'prompt': 'cat girl', 'model': 'moss-sft', 'api_workflow': giant_workflow, 'model_snapshot': giant_snapshot},
                'results': [{'id': 'gal-1', 'filename': 'out1.png', 'width': 1024, 'height': 1536, 'api_workflow': giant_workflow, 'params': {'api_workflow': giant_workflow}}],
                'prompt': giant_workflow,
                'submitted_prompt': giant_workflow,
                'required_models': giant_snapshot,
                'finished_at': '2026-11-26T04:00:00Z',
            })
            # 3) Completed video task
            db.save('tasks', {
                'id': 't-vid',
                'status': 'completed',
                'stage': 'completed',
                'media_kind': 'video',
                'cu_estimated': 0.5,
                'params': {'workflow_id': 'h3-studio', 'prompt': 'dancing', 'model': 'h3', 'duration_seconds': 5},
                'results': [{'id': 'gal-v', 'filename': 'vid1.mp4', 'media_type': 'video', 'duration': 5.0}],
                'prompt': giant_workflow,
                'finished_at': '2026-11-26T05:00:00Z',
            })
            # 4) Failed refine task
            db.save('tasks', {
                'id': 't-fail',
                'status': 'failed',
                'stage': 'failed',
                'error': 'OOM',
                'params': {'mode': 'video_seedvr2', 'model': 'seedvr2'},
                'results': [],
                'finished_at': '2026-11-25T05:00:00Z',
            })
            # 5) Running active task
            db.save('tasks', {
                'id': 't-run',
                'status': 'running',
                'stage': 'generating',
                'sampling_step': 10,
                'total_steps': 28,
                'params': {'prompt': 'live job', 'model': 'moss-sft', 'api_workflow': giant_workflow},
                'results': [],
                'prompt': giant_workflow,
                'submitted_prompt': giant_workflow,
            })

            # Drop and re-open DB to verify automatic tasks_index backfill
            with db.connect() as c:
                c.execute('DELETE FROM tasks_index')
            db2 = Database(root / 'studio.sqlite3')
            self.assertEqual(db2.active_task_count(), 1)

            from . import main
            class FakeRuntime:
                async def status(self):
                    return {'state': 'ready', 'gpu': 'A100'}

            with patch.multiple(main, db=db2, runtime=FakeRuntime()), patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    # /api/runtime uses indexed count
                    rt = await client.get('/api/runtime')
                    self.assertEqual(rt.status_code, 200)
                    self.assertEqual(rt.json()['queue_count'], 1)

                    # Paginated /api/tasks strips giant blobs and returns stats/active_items
                    p1 = (await client.get('/api/tasks?page=1&limit=2&paginated=true&include_active=true')).json()
                    self.assertEqual(p1['total'], 4)
                    self.assertEqual(p1['total_pages'], 2)
                    self.assertTrue(p1['has_more'])
                    self.assertEqual(p1['stats'], {'active': 1, 'completed': 2, 'failed': 1, 'total_cu': 0.75, 'has_cu': True})
                    self.assertEqual([t['id'] for t in p1['active_items']], ['t-run'])
                    self.assertEqual(p1['items'][0]['id'], 't-run')
                    for item in p1['items']:
                        self.assertNotIn('prompt', item)
                        self.assertNotIn('submitted_prompt', item)
                        self.assertNotIn('api_workflow', item['params'])
                        self.assertNotIn('model_snapshot', item['params'])
                        self.assertEqual(item['params'].get('prompt', item['params'].get('mode')) is not None, True)
                        for res_obj in item.get('results') or []:
                            self.assertNotIn('api_workflow', res_obj)
                            self.assertNotIn('params', res_obj)

                    # Filtering works identically
                    vids = (await client.get('/api/tasks?page=1&limit=10&kind=video')).json()
                    self.assertEqual([t['id'] for t in vids['items']], ['t-vid'])
                    refines = (await client.get('/api/tasks?page=1&limit=10&kind=refine')).json()
                    self.assertEqual([t['id'] for t in refines['items']], ['t-fail'])
                    by_date = (await client.get('/api/tasks?page=1&limit=10&date=2026-11-25')).json()
                    self.assertEqual([t['id'] for t in by_date['items']], ['t-fail'])

                    # Legacy unpaginated array compatibility still strips heavy prompt blobs
                    arr = (await client.get('/api/tasks')).json()
                    self.assertIsInstance(arr, list)
                    self.assertEqual(len(arr), 4)
                    self.assertNotIn('t-hidden', [t['id'] for t in arr])
                    self.assertNotIn('prompt', arr[0])

                    # Detail endpoint still returns full workflow & snapshot
                    full = (await client.get('/api/tasks/t-img')).json()
                    self.assertIn('prompt', full)
                    self.assertIn('api_workflow', full['params'])
                    self.assertIn('model_snapshot', full['params'])

    async def test_combined_poll_task_state_single_round_trip_and_slim_sse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            store = WorkflowStore(root)
            db.save('models', {'id': 'ckpt-1', 'filename': 'sdxl.safetensors', 'kind': 'checkpoints'})

            emitted = []
            async def emit(event, payload):
                emitted.append((event, payload))

            class FakeRuntime:
                async def status(self):
                    return {'state': 'ready', 'instance_id': 'inst-1', 'gpu': 'A100'}
                async def quota(self, force=False):
                    return {'balance': 100.0, 'consumption_rate': 6.0}

            poll_calls = []
            class FakeComfy:
                def __init__(self):
                    self.tick = 0
                async def request(self, method, path, port=8188, **kwargs):
                    if path == '/object_info':
                        return {
                            'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [['sdxl.safetensors'], {}]}}},
                            'CLIPTextEncode': {'input': {'required': {'text': ['STRING', {}], 'clip': ['CLIP', {}]}}},
                            'EmptyLatentImage': {'input': {'required': {'width': ['INT', {}], 'height': ['INT', {}], 'batch_size': ['INT', {}]}}},
                            'KSampler': {'input': {'required': {
                                'model': ['MODEL', {}], 'positive': ['CONDITIONING', {}], 'negative': ['CONDITIONING', {}],
                                'latent_image': ['LATENT', {}], 'seed': ['INT', {}], 'steps': ['INT', {}], 'cfg': ['FLOAT', {}],
                                'sampler_name': [['euler'], {}], 'scheduler': [['normal'], {}], 'denoise': ['FLOAT', {}],
                            }}},
                            'VAEDecode': {'input': {'required': {'samples': ['LATENT', {}], 'vae': ['VAE', {}]}}},
                            'SaveImage': {'input': {'required': {'images': ['IMAGE', {}], 'filename_prefix': ['STRING', {}]}}},
                        }
                    if path == '/prompt':
                        return {'prompt_id': 'pid-1'}
                    if path.startswith('/view?'):
                        return b'pngbytes'
                    raise AssertionError(f'Unexpected fallback call: {method} {path}')

                async def poll_task_state(self, client_id, prompt_id, port=8188):
                    self.tick += 1
                    poll_calls.append((client_id, prompt_id, port))
                    # Identify KSampler node key dynamically from DB task prompt
                    task_row = db.get('tasks', client_id)
                    ksampler_id = next(k for k, n in task_row['prompt'].items() if n['class_type'] == 'KSampler')
                    if self.tick == 1:
                        return {
                            'events': [
                                {'type': 'executing', 'data': {'prompt_id': prompt_id, 'node': ksampler_id}},
                                {'type': 'progress', 'data': {'prompt_id': prompt_id, 'node': ksampler_id, 'value': 1, 'max': 20}},
                                {'type': 'progress', 'data': {'prompt_id': prompt_id, 'node': ksampler_id, 'value': 4, 'max': 20}},
                            ],
                            'history': {},
                            'queue': {'queue_running': [[0, prompt_id]], 'queue_pending': []},
                        }
                    return {
                        'events': [
                            {'type': 'execution_success', 'data': {'prompt_id': prompt_id}},
                        ],
                        'history': {
                            prompt_id: {'status': {'completed': True}, 'outputs': {
                                next(iter(task_row['output_nodes'])): {'images': [{'filename': 'res.png', 'type': 'output'}]}
                            }}
                        },
                        'queue': {},
                    }

            class FakeGallery:
                async def archive(self, task_id, content, filename, metadata, parent_id=None):
                    return {'id': 'gal-new', 'filename': filename, 'width': 512, 'height': 512, 'media_type': 'image', 'params': metadata}

            t = Tasks(db, FakeRuntime(), FakeComfy(), None, store, emit, gallery=FakeGallery(), poll_interval=0.001)
            row = await t.submit({'mode': 'txt2img', 'model': 'ckpt-1', 'prompt': 'test'})
            await t._execute_subscribed(row)

            # Poll ran twice using combined poll_task_state (no /history or /queue HTTP fallback calls)
            self.assertEqual(len(poll_calls), 2)
            done = db.get('tasks', row['id'])
            self.assertEqual(done['status'], 'completed')
            # Archive stored slimmed result (no nested huge params dict)
            self.assertNotIn('params', done['results'][0])
            self.assertEqual(done['results'][0]['id'], 'gal-new')

            # SSE emissions used task_summary (no prompt/submitted_prompt), coalescing tick 1 step 1..4 -> 1 save with step 4
            task_events = [payload for ev, payload in emitted if ev == 'task']
            self.assertTrue(any(ev.get('sampling_step') == 4 and ev.get('total_steps') == 20 for ev in task_events))
            self.assertFalse(any(ev.get('sampling_step') == 1 for ev in task_events))
            for ev in task_events:
                self.assertNotIn('prompt', ev)
                self.assertNotIn('submitted_prompt', ev)

    async def test_events_stream_sets_no_buffering_headers_and_initial_comment(self):
        from . import main

        class FakeRequest:
            def __init__(self):
                self.calls = 0

            async def is_disconnected(self):
                self.calls += 1
                return self.calls > 1

        before_listeners = len(main.listeners)
        resp = await main.events(FakeRequest())
        self.assertIn('no-transform', resp.headers.get('cache-control', ''))
        self.assertEqual(resp.headers.get('x-accel-buffering'), 'no')
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            if len(chunks) == 1:
                main.emit('task', {'id': 'sse-1', 'sampling_step': 5, 'total_steps': 20})
        self.assertEqual(chunks[0], ': connected\n\n')
        self.assertIn('"sse-1"', chunks[1])
        self.assertEqual(len(main.listeners), before_listeners)

    async def test_default_concurrency_is_one_and_settings_clamp_workers(self):
        import importlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            store = WorkflowStore(root)
            t = Tasks(db, None, None, None, store, lambda *a: None)
            self.assertEqual(t._desired_workers(), 1)
            db.save('settings', {'id': 'main', 'comfy_workers': 2})
            self.assertEqual(t._desired_workers(), 2)

            # bootstrap._resolve_ports defaults to [8188] even on >=48GB VRAM, allows 2 if requested
            boot = importlib.import_module('colab.bootstrap')
            with patch.object(boot, '_gpu_vram_gb', return_value=96.0):
                self.assertEqual(boot._resolve_ports(), [8188])
                self.assertEqual(boot._resolve_ports(workers=2), [8188, 8189])
            with patch.object(boot, '_gpu_vram_gb', return_value=40.0):
                self.assertEqual(boot._resolve_ports(workers=2), [8188])

            # /api/settings defaults comfy_workers to 1 and clamps out-of-range ints
            from . import main
            with patch.object(main, 'db', db), patch.object(main, 'tasks', t), patch.object(main.auth, 'check', return_value='test'):
                db.delete('settings', 'main')
                db.save('settings', {'id': 'main', 'mode': 'persistent'})
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    s = (await client.get('/api/settings')).json()
                    self.assertEqual(s['comfy_workers'], 1)
                    bad = await client.patch('/api/settings', json={'comfy_workers': True})
                    self.assertEqual(bad.status_code, 400)
                    clamped = (await client.patch('/api/settings', json={'comfy_workers': 99})).json()
                    self.assertEqual(clamped['comfy_workers'], 2)
                    self.assertEqual(t._desired_workers(), 2)

    async def test_warm_runtime_reuses_models_and_object_info_cache_without_syncing_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            store = WorkflowStore(root)
            db.save('models', {'id': 'ckpt-1', 'filename': 'sdxl.safetensors', 'kind': 'checkpoints'})

            stages_seen = []
            async def emit(event, payload):
                if event == 'task' and payload.get('stage'):
                    stages_seen.append(payload['stage'])

            class FakeRuntime:
                async def status(self):
                    return {'state': 'ready', 'instance_id': 'inst-warm', 'gpu': 'G4'}
                async def quota(self, force=False):
                    return {'balance': 100.0, 'consumption_rate': 11.2}

            subset_calls = []
            prompt_seq = 0
            class FakeComfy:
                async def object_info_for(self, classes, port=8188):
                    subset_calls.append((port, tuple(classes)))
                    return {
                        'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [['sdxl.safetensors'], {}]}}},
                        'CLIPTextEncode': {'input': {'required': {'text': ['STRING', {}], 'clip': ['CLIP', {}]}}},
                        'EmptyLatentImage': {'input': {'required': {'width': ['INT', {}], 'height': ['INT', {}], 'batch_size': ['INT', {}]}}},
                        'KSampler': {'input': {'required': {
                            'model': ['MODEL', {}], 'positive': ['CONDITIONING', {}], 'negative': ['CONDITIONING', {}],
                            'latent_image': ['LATENT', {}], 'seed': ['INT', {}], 'steps': ['INT', {}], 'cfg': ['FLOAT', {}],
                            'sampler_name': [['euler'], {}], 'scheduler': [['normal'], {}], 'denoise': ['FLOAT', {}],
                        }}},
                        'VAEDecode': {'input': {'required': {'samples': ['LATENT', {}], 'vae': ['VAE', {}]}}},
                        'SaveImage': {'input': {'required': {'images': ['IMAGE', {}], 'filename_prefix': ['STRING', {}]}}},
                    }

                async def request(self, method, path, port=8188, **kwargs):
                    nonlocal prompt_seq
                    if path == '/prompt':
                        prompt_seq += 1
                        return {'prompt_id': f'pid-{prompt_seq}'}
                    if path.startswith('/view?'):
                        return b'pngbytes'
                    raise AssertionError(f'Unexpected fallback call: {method} {path}')

                async def poll_task_state(self, client_id, prompt_id, port=8188):
                    task_row = db.get('tasks', client_id)
                    return {
                        'events': [],
                        'history': {
                            prompt_id: {'status': {'completed': True}, 'outputs': {
                                next(iter(task_row['output_nodes'])): {'images': [{'filename': 'res.png', 'type': 'output'}]}
                            }}
                        },
                        'queue': {},
                    }

            class FakeGallery:
                async def archive(self, task_id, content, filename, metadata, parent_id=None):
                    return {'id': f'gal-{task_id[:6]}', 'filename': filename, 'width': 512, 'height': 512, 'media_type': 'image'}

            t = Tasks(db, FakeRuntime(), FakeComfy(), None, store, emit, gallery=FakeGallery(), poll_interval=0.001)

            row1 = await t.submit({'mode': 'txt2img', 'model': 'ckpt-1', 'prompt': 'first'})
            await t._execute_subscribed(row1)
            row2 = await t.submit({'mode': 'txt2img', 'model': 'ckpt-1', 'prompt': 'second'})
            await t._execute_subscribed(row2)

            # object_info_for called only once across both warm tasks, never entered stage='syncing'
            self.assertEqual(len(subset_calls), 1)
            self.assertNotIn('syncing', stages_seen)
            self.assertIn('preparing', stages_seen)
            self.assertEqual(db.get('tasks', row1['id'])['sync_seconds'], 0.0)
            self.assertEqual(db.get('tasks', row2['id'])['sync_seconds'], 0.0)

            # Invalidation forces one fresh fetch on the next task
            t.invalidate_object_info(port=8188)
            row3 = await t.submit({'mode': 'txt2img', 'model': 'ckpt-1', 'prompt': 'third'})
            await t._execute_subscribed(row3)
            self.assertEqual(len(subset_calls), 2)

    async def test_wait_survives_transient_poll_timeouts_and_formats_empty_errors(self):
        from .comfy import Comfy
        from .runtime import AdapterError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / 'studio.sqlite3')
            store = WorkflowStore(root)

            # Verify Comfy._execution_control turns bare TimeoutError into descriptive AdapterError
            class HangingRuntime:
                async def execute(self, *args, **kwargs):
                    raise TimeoutError()
            comfy_real = Comfy(HangingRuntime())
            with self.assertRaises(AdapterError) as ctx:
                await comfy_real._execution_control('print(1)', {}, timeout=1)
            self.assertTrue(len(str(ctx.exception)) > 0)

            class OkRuntime:
                async def status(self):
                    return {'state': 'ready', 'instance_id': 'inst-1'}

            attempts = 0
            class FlakyComfy:
                async def poll_task_state(self, client_id, prompt_id, port=8188):
                    nonlocal attempts
                    attempts += 1
                    if attempts <= 2:
                        raise AdapterError('GPU控制通道轮询超时，将自动重试')
                    if attempts == 3:
                        raise TimeoutError()
                    return {
                        'events': [],
                        'history': {prompt_id: {'status': {'completed': True}, 'outputs': {'6': {'images': []}}}},
                        'queue': {},
                    }

            t = Tasks(db, OkRuntime(), FlakyComfy(), None, store, lambda *a: None, poll_interval=0.001)
            row = db.save('tasks', {'id': 'wait-ok', 'status': 'running', 'prompt_id': 'p-1', 'prompt': {}})
            item = await t._wait(row, port=8188)
            self.assertTrue(item['status']['completed'])
            self.assertEqual(attempts, 4)

            # Persistent failures must raise a non-empty message (never '')
            class AlwaysTimeoutComfy:
                async def poll_task_state(self, client_id, prompt_id, port=8188):
                    raise TimeoutError()

            t_fail = Tasks(db, OkRuntime(), AlwaysTimeoutComfy(), None, store, lambda *a: None, poll_interval=0.001)
            with patch('backend.tasks.asyncio.sleep', return_value=None):
                with self.assertRaises(RuntimeError) as fail_ctx:
                    await t_fail._wait(row, port=8188)
            self.assertIn('连续失败', str(fail_ctx.exception))


if __name__ == '__main__':
    unittest.main()
