"""Video archive and imported LoRA regressions."""
import asyncio
import copy
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from .gallery import Gallery
from .tasks import Tasks
from .workflows import WorkflowStore

class DB:
    def __init__(self): self.rows = {}
    def get(self, kind, ident): return copy.deepcopy(self.rows.get((kind, ident)))
    def save(self, kind, row):
        self.rows[(kind, row['id'])] = {**self.rows.get((kind, row['id']), {}), **copy.deepcopy(row)}
        return self.get(kind, row['id'])

class VideoOutputs(unittest.IsolatedAsyncioTestCase):
    async def test_video_archive_and_image_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            gallery = Gallery(DB(), None, tmp)
            async def uploaded(record): return record
            gallery.retry_archive = uploaded
            for suffix in ('mp4', 'webm'):
                path = Path(tmp) / ('fixture.' + suffix)
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x48:d=0.3', '-y', str(path)], check=True)
                row = await gallery.archive('t', path.read_bytes(), path.name, {'prompt': 'test'})
                self.assertEqual(row['media_type'], 'video')
                self.assertEqual(row['mime_type'], 'video/' + suffix)
                self.assertEqual((row['width'], row['height']), (64, 48))
                self.assertGreater(row['duration'], 0)
                self.assertEqual(Path(row['local_path']).suffix, '.' + suffix)
                with Image.open(Path(tmp) / 'thumbnails' / (row['id'] + '.webp')) as thumb:
                    self.assertEqual(thumb.size, (64, 48))
            image = io.BytesIO(); Image.new('RGB', (30, 20)).save(image, 'PNG')
            row = await gallery.archive('t', image.getvalue(), 'original.png', {})
            self.assertEqual((row['width'], row['height']), (30, 20))
            self.assertEqual(row['media_type'], 'image')
            await asyncio.sleep(0)

    async def test_video_http_download_preview_range_and_thumbnail(self):
        import os
        import httpx
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'DRAWING_DATA': tmp}):
            from . import main
            db = DB(); gallery = Gallery(db, None, tmp)
            async def uploaded(record): return record
            gallery.retry_archive = uploaded
            source = Path(tmp) / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=64x48:d=0.3', '-y', str(source)], check=True)
            row = await gallery.archive('t', source.read_bytes(), source.name, {})
            with patch.multiple(main, db=db, gallery=gallery, DATA=Path(tmp)), patch.object(main.auth, 'check', return_value='test'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    preview = await client.get('/api/gallery/' + row['id'] + '/media', headers={'Range':'bytes=0-15'})
                    self.assertEqual(preview.status_code, 206)
                    self.assertEqual(preview.headers['content-type'], 'video/mp4')
                    self.assertEqual(len(preview.content), 16)
                    download = await client.get(row['url'])
                    self.assertEqual(download.headers['content-type'], 'video/mp4')
                    self.assertIn('attachment', download.headers['content-disposition'])
                    self.assertEqual(download.content, source.read_bytes())
                    (Path(tmp) / 'thumbnails' / (row['id'] + '.webp')).unlink()
                    thumbnail = await client.get(row['thumbnail_url'])
                    self.assertEqual(thumbnail.status_code, 200)
                    self.assertEqual(thumbnail.headers['content-type'], 'image/webp')
            await asyncio.sleep(0)

    async def test_native_video_and_vhs_outputs_are_deduplicated(self):
        db = DB(); seen = []
        class Comfy:
            async def request(self, *args): return b'fixture'
        class Archive:
            async def archive(self, task, content, filename, params, **kwargs):
                seen.append(filename); return {'id': filename}
        tasks = Tasks(db, None, Comfy(), None, None, lambda *args: None, gallery=Archive())
        row = db.save('tasks', {'id':'t', 'params':{}, 'prompt':{}, 'prompt_id':'p', 'results':[], 'output_nodes':['image', 'video']})
        video = {'filename':'clip.mp4', 'type':'output', 'subfolder':''}
        history = {'outputs':{'image':{'images':[{'filename':'original.png', 'type':'output'}]},
                              'video':{'videos':[video], 'gifs':[video], 'images':[{'filename':'temp.png', 'type':'temp'}]}}}
        await tasks._archive(row, history)
        self.assertEqual(seen, ['original.png', 'clip.mp4'])
        await tasks._archive(db.get('tasks', 't'), history)
        self.assertEqual(len(seen), 2)
        self.assertEqual(db.get('tasks', 't')['status'], 'completed')

class LoraMatching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))
        self.graph = {str(n): {'class_type':'LoraLoaderModelOnly', 'inputs':{'lora_name':name, 'strength_model':1.0}}
                      for n, name in enumerate(['turbo.safetensors', 'mystic.safetensors'])}
        self.store.save({'id':'video', 'workflow':self.graph})
        self.models = [{'id':'turbo-id', 'filename':'turbo.safetensors'}, {'id':'mystic-id', 'filename':'mystic.safetensors'}]
    def build(self, loras): return self.store.build({'workflow_id':'video', 'loras':loras}, models=self.models)['prompt']
    def test_multi_lora_matches_filename_via_registry_id(self):
        graph = self.build([{'id':'mystic-id', 'strength':.7}, {'id':'turbo-id', 'strength':.9}])
        self.assertEqual(graph['0']['inputs'], {'lora_name':'turbo.safetensors','strength_model':.9})
        self.assertEqual(graph['1']['inputs'], {'lora_name':'mystic.safetensors','strength_model':.7})
    def test_single_selection_cannot_overwrite_other_slot(self):
        graph = self.build([{'id':'mystic-id', 'strength':.5}])
        self.assertEqual(graph['0'], self.graph['0'])
        self.assertEqual(graph['1']['inputs']['strength_model'], .5)
    def test_single_slot_replacement_compatible(self):
        self.store.save({'id':'single', 'workflow':{'0':self.graph['0']}})
        graph = self.store.build({'workflow_id':'single', 'loras':[{'id':'mystic-id', 'strength':.6}]}, models=self.models)['prompt']
        self.assertEqual(graph['0']['inputs'], {'lora_name':'mystic.safetensors','strength_model':.6})
    def test_no_loras_preserves_graph(self):
        self.assertEqual(self.build([]), self.graph)
        self.assertEqual(self.store.build({'workflow_id':'video'}, models=self.models)['prompt'], self.graph)

    def test_ui_weight_edit_and_remove_reach_imported_nodes(self):
        import json
        # Exercise the same transformations used by both React controls, then build a real graph.
        helper = Path(__file__).parents[1] / 'web/src/loraSelection.js'
        source = """
import {setLoraStrength,removeLora,toggleLora} from HELPER;
const slots=[{node_id:'0',id:'turbo-id',strength:1,strength_model:1,strength_clip:.3},
             {node_id:'1',id:'mystic-id',strength:1,strength_model:1,strength_clip:.4}];
const edited=slots.map(l=>setLoraStrength(l,-.35));
const removed=removeLora(edited,edited[1]);
const model={id:'mystic-id',recommended_weight:.65};
const restored=toggleLora(removed,model);
const unchecked=toggleLora(restored,model);
console.log(JSON.stringify({edited,removed,restored,unchecked}));
""".replace('HELPER', json.dumps(helper.as_uri()))
        result = subprocess.run(['node', '--input-type=module', '-e', source], check=True, capture_output=True, text=True)
        payloads = json.loads(result.stdout)
        self.graph['1']['class_type'] = 'LoraLoader'
        self.graph['1']['inputs']['strength_clip'] = .4
        self.store.save({'id':'video', 'workflow':self.graph})
        for action, weight in [('edited', -.35), ('removed', 0), ('restored', .65), ('unchecked', 0)]:
            with self.subTest(action=action):
                graph = self.build(payloads[action])
                self.assertEqual(graph['0']['inputs']['strength_model'], -.35)
                self.assertEqual(graph['1']['inputs']['strength_model'], weight)
                self.assertEqual(graph['1']['inputs']['strength_clip'], weight)
                self.assertEqual(graph['1']['inputs']['lora_name'], 'mystic.safetensors')
        self.assertEqual(len(payloads['removed']), 2)
        self.assertFalse(payloads['removed'][1]['enabled'])

    def test_node_id_disables_only_one_duplicate_filename_slot(self):
        self.graph['1'] = copy.deepcopy(self.graph['0'])
        self.graph['1']['class_type'] = 'LoraLoader'
        self.graph['1']['inputs']['strength_clip'] = .4
        self.store.save({'id':'video', 'workflow':self.graph})
        graph = self.build([{'node_id':'1', 'id':'turbo-id', 'enabled':False, 'strength':0,
                             'strength_model':.8, 'strength_clip':.9}])
        self.assertEqual(graph['0'], self.graph['0'])
        self.assertEqual(graph['1']['inputs']['strength_model'], 0)
        self.assertEqual(graph['1']['inputs']['strength_clip'], 0)
    def test_disabled_lora_zeroes_only_matching_slot(self):
        graph = self.build([{'id':'mystic-id', 'enabled':False}, {'id':'turbo-id','strength':.8}])
        self.assertEqual(graph['1']['inputs']['strength_model'], 0)
        self.assertEqual(graph['0']['inputs']['strength_model'], .8)

    def test_sdxl_imported_lora_loader_splices_model_and_clip(self):
        sdxl_graph = {
            '0': {'class_type': 'LoraLoader', 'inputs': {'model': ['10', 0], 'clip': ['10', 1], 'lora_name': 'turbo.safetensors', 'strength_model': 1.0, 'strength_clip': 1.0}},
            '2': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['0', 1], 'text': 'hi'}},
            '3': {'class_type': 'KSampler', 'inputs': {'model': ['0', 0], 'positive': ['2', 0]}},
        }
        self.store.save({'id': 'sdxl-imp', 'workflow': sdxl_graph})
        models = [*self.models, {'id': 'extra-id', 'filename': 'extra.safetensors'}]
        built = self.store.build(
            {'workflow_id': 'sdxl-imp', 'loras': [{'node_id': '0', 'id': 'turbo-id', 'strength': .9}, {'id': 'extra-id', 'strength_model': .6, 'strength_clip': .4}]},
            models=models,
        )
        prompt = built['prompt']
        self.assertEqual(prompt['extra_lora_1']['class_type'], 'LoraLoader')
        self.assertEqual(prompt['extra_lora_1']['inputs'], {'model': ['0', 0], 'clip': ['0', 1], 'lora_name': 'extra.safetensors', 'strength_model': .6, 'strength_clip': .4})
        self.assertEqual(prompt['3']['inputs']['model'], ['extra_lora_1', 0])
        self.assertEqual(prompt['2']['inputs']['clip'], ['extra_lora_1', 1])

if __name__ == '__main__': unittest.main()
