import unittest
from . import test_h3_controls
from .workflows import WorkflowError

class H3Refs(test_h3_controls.H3Controls):
    def setUp(self):
        super().setUp()
        self.graph['8']['inputs'].update({'ref_images.ref_image_0':['18',0], 'ref_videos.ref_video_0':['20',0], 'ref_audios.ref_audio_0':['21',0]})
        self.graph['18']={'class_type':'LoadImage','inputs':{'image':'old.png'}}
        self.graph['20']={'class_type':'LoadVideo','inputs':{'file':'v.mp4'}}
        self.graph['21']={'class_type':'LoadAudio','inputs':{'audio':'a.wav'}}
        self.store.save({'id':'h3','workflow':self.graph,'mapping':{'image':'18.image','fps':'10.fps'}})
    def refs(self,b):
        i=b['prompt']['8']['inputs']
        return [b['prompt'][i[f'ref_images.ref_image_{n}'][0]]['inputs']['image'] for n in range(len([k for k in i if k.startswith('ref_images.ref_image_')]))]
    def test_refs_order_limits_duplicates_and_snapshots(self):
        for paths in [['a.png','b.png'],['same.png']*9,[]]:
            b=self.build(ref_images=paths)
            self.assertEqual(self.refs(b),paths)
            self.assertNotIn('18',b['prompt'])
            links=[v[0] for k,v in b['prompt']['8']['inputs'].items() if k.startswith('ref_images.ref_image_')]
            self.assertEqual(len(set(links)),len(paths))
            self.assertEqual(self.refs(self.store.build(b['params'])),paths)
            self.assertEqual(b['prompt']['8']['inputs']['ref_videos.ref_video_0'],['20',0])
            self.assertEqual(b['prompt']['8']['inputs']['ref_audios.ref_audio_0'],['21',0])
        with self.assertRaises(WorkflowError): self.build(ref_images=['a.png']*10)
        with self.assertRaises(WorkflowError): self.build(ref_images='a.png')
    def test_delete_and_legacy(self):
        self.assertEqual(self.refs(self.build(ref_images=['b.png'])),['b.png'])
        self.assertEqual(self.refs(self.build(image='legacy.png')),['legacy.png'])
        self.assertEqual(self.refs(self.build()),['old.png'])
    def test_size_before_defaults_explicit_wins(self):
        b=self.build(size='640x384');self.assertEqual((b['params']['width'],b['params']['height']),(640,384))
        b=self.build(size='640x384',width=960);self.assertEqual((b['params']['width'],b['params']['height']),(960,384))
    def test_fields_cannot_override_final_fps(self):
        b=self.build(fields={'fps':30});self.assertEqual(b['prompt']['10']['inputs']['fps'],24)
    def test_image_refs_isolation(self):
        self.store.save({'id':'image','workflow':{'1':{'class_type':'SaveImage','inputs':{}}}})
        self.assertNotIn('ref_images',self.store.build({'workflow_id':'image','ref_images':['a.png']})['params'])
    def test_legacy_defaults_remain_original(self):
        self.assertEqual(self.build()['prompt']['8']['inputs'],self.graph['8']['inputs'])
    def test_automatic_mapping_and_custom_resolution(self):
        built=self.build(width=640,height=960)
        self.assertEqual((built['prompt']['8']['inputs']['width'],built['prompt']['8']['inputs']['height']),(640,960))
