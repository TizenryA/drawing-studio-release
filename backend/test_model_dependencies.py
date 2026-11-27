"""Imported loader dependency regressions."""
import copy
import tempfile
import unittest
from pathlib import Path
from .tasks import Tasks
from .workflows import WorkflowStore, WorkflowError

LOADERS = [
    ('UNETLoader', 'unet_name', 'animayume_v05.safetensors', 'diffusion_models'),
    ('CLIPLoader', 'clip_name', 'qwen_3_06b_base.safetensors', 'text_encoders'),
    ('VAELoader', 'vae_name', 'qwen_image_vae.safetensors', 'vae'),
    ('LoraLoader', 'lora_name', 'anima_style_demo.safetensors', 'loras'),
    ('LoraLoader', 'lora_name', 'anima_hsr_cg.safetensors', 'loras'),
    ('SeedVR2LoadDiTModel', 'model', 'seedvr2_ema_3b_fp8_e4m3fn.safetensors', 'seedvr2'),
    ('SeedVR2LoadVAEModel', 'model', 'ema_vae_fp16.safetensors', 'seedvr2'),
]

def graph():
    return {str(i): {'class_type': cls, 'inputs': {field: name}}
            for i, (cls, field, name, _) in enumerate(LOADERS)}

def catalog():
    return [{'id': 'drive-' + str(i), 'filename': name, 'kind': kind, 'status': 'cached'}
            for i, (_, _, name, kind) in enumerate(LOADERS)]

class DB:
    def __init__(self): self.rows = {}
    def get(self, kind, ident): return copy.deepcopy(self.rows.get((kind, ident)))
    def list(self, kind): return catalog() if kind == "models" else []
    def save(self, kind, row):
        self.rows[(kind, row['id'])] = copy.deepcopy(row)
        return copy.deepcopy(row)

class Dependencies(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))
        self.store.save({'id': 'anima', 'workflow': graph()})

    def test_all_frozen_dependencies_and_no_generic_checkpoint(self):
        built = self.store.build({'workflow_id': 'anima', 'model': 'generic-sdxl',
                                  'width': 768, 'height': 1344, 'steps': 32}, models=catalog())
        self.assertEqual({m['id'] for m in built['required_models']}, {m['id'] for m in catalog()})
        self.assertEqual(built['params']['model_snapshot'], built['required_models'])
        self.assertEqual(built['prompt'], graph())

    def test_dual_triple_clip_and_model_only_lora(self):
        prompt = {'1': {'class_type': 'DualCLIPLoader', 'inputs': {'clip_name1': 'a', 'clip_name2': 'b'}},
                  '2': {'class_type': 'TripleCLIPLoader', 'inputs': {'clip_name1': 'b', 'clip_name2': 'c', 'clip_name3': 'd'}},
                  '3': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'lora_name': 'e'}}}
        self.store.save({'id': 'multi', 'workflow': prompt})
        built = self.store.build({'workflow_id': 'multi'})
        self.assertEqual({m['comfy_name']: m['kind'] for m in built['required_models']},
                         {'a': 'text_encoders', 'b': 'text_encoders', 'c': 'text_encoders', 'd': 'text_encoders', 'e': 'loras'})

    async def test_cold_runtime_syncs_all_even_registry_cached(self):
        db = DB(); calls = []
        class Runtime:
            async def status(self): return {'state': 'ready', 'instance_id': 'new-runtime'}
        class Comfy:
            async def request(inner, method, path, **kwargs):
                if path == '/object_info':
                    info = {}
                    for cls, field, name, _ in LOADERS:
                        fields = info.setdefault(cls, {'input': {'required': {}}})['input']['required']
                        fields.setdefault(field, ['COMBO', {'options': []}])[1]['options'].extend(
                            [name] if name in calls else [])
                    return info
                if path == '/prompt':
                    self.assertEqual(set(calls), {x[2] for x in LOADERS})
                    return {'prompt_id': 'test-only'}
                raise AssertionError(path)
        async def sync(model, on_progress=None):
            calls.append(model['comfy_name']); return {'ok': True}
        task = Tasks(db, Runtime(), Comfy(), None, self.store, lambda *a: None, sync_model=sync, poll_interval=.001)
        row = await task.submit({'workflow_id': 'anima'})
        async def wait(row, port=8188): raise StopAsyncIteration('submission verified')
        task._wait = wait
        with self.assertRaises(StopAsyncIteration): await task._execute_subscribed(row)
        self.assertEqual(len(calls), 7)

    async def test_warm_combo_and_legacy_choices_skip_transfer(self):
        for spec in [[[LOADERS[0][2]]], ['COMBO', {'options': [LOADERS[0][2]]}]]:
            db = DB()
            async def sync(model): raise AssertionError('existing model downloaded again')
            task = Tasks(db, None, None, None, self.store, lambda *a: None, sync_model=sync)
            row = db.save('tasks', {'id': 'warm', 'status': 'running', 'prompt': graph(),
                                   'required_models': [{'comfy_name': LOADERS[0][2]}]})
            await task._sync(row, {'UNETLoader': {'input': {'required': {'unet_name': spec}}}})

    async def test_stale_combo_after_sync_fails_before_submission(self):
        db = DB()
        class Runtime:
            async def status(self): return {'state': 'ready', 'instance_id': 'fresh'}
        class Comfy:
            async def request(inner, method, path, **kwargs):
                self.assertEqual(path, '/object_info')
                return {cls: {'input': {'required': {field: ['COMBO', {'options': []}]}}}
                        for cls, field, _, _ in LOADERS}
        async def sync(model): return True
        task = Tasks(db, Runtime(), Comfy(), None, self.store, lambda *a: None, sync_model=sync)
        row = await task.submit({'workflow_id': 'anima'})
        with self.assertRaises(WorkflowError): await task._execute_subscribed(row)

    def test_combo_and_legacy_schema_reject_missing_model(self):
        for spec in [[[]], ['COMBO', {'options': []}]]:
            info = {}
            for cls, field, _, _ in LOADERS:
                info.setdefault(cls, {'input': {'required': {}}})['input']['required'][field] = spec
            with self.subTest(spec=spec), self.assertRaises(WorkflowError):
                self.store.build({'workflow_id': 'anima'}, models=catalog(), object_info=info)

    async def test_other_loader_choice_does_not_skip_sync(self):
        db = DB(); calls = []
        async def sync(model): calls.append(model['comfy_name']); return True
        task = Tasks(db, None, None, None, self.store, lambda *a: None, sync_model=sync)
        row = db.save('tasks', {'id': 'x', 'status': 'running', 'prompt': graph(),
                               'required_models': [{'comfy_name': LOADERS[0][2]}]})
        info = {'Unrelated': {'input': {'required': {'anything': [[LOADERS[0][2]]]}}},
                'UNETLoader': {'input': {'required': {'unet_name': [[]]}}}}
        await task._sync(row, info)
        self.assertEqual(calls, [LOADERS[0][2]])

if __name__ == '__main__': unittest.main()
