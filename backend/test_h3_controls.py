"""H3 duration and resolution contract tests."""
import tempfile
import unittest
from pathlib import Path
from .workflows import WorkflowStore, WorkflowError

class H3Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))
        self.graph = {'8': {'class_type':'MiniMaxH3ReferenceToVideo', 'inputs':{'prompt':'original', 'width':864, 'height':480, 'length':124}},
                      '9': {'class_type':'KSampler', 'inputs':{'steps':8}},
                      '26': {'class_type':'VAEDecode', 'inputs':{'samples':['8', 1]}},
                      '10': {'class_type':'CreateVideo', 'inputs':{'fps':24, 'images':['26', 0]}}}
        self.store.save({'id':'h3', 'mode':'custom', 'workflow':self.graph,
                         'mapping':{'width':'8.width', 'height':'8.height', 'steps':'9.steps'}})
    def build(self, **params):
        return self.store.build({'workflow_id':'h3', **params})
    def test_duration_presets_reach_graph_and_snapshot(self):
        for seconds, length in [(5,124),(8,192),(10,243),(12,294),(15,362)]:
            with self.subTest(seconds=seconds):
                built = self.build(duration_seconds=seconds, fps=24, width=1280, height=736)
                self.assertEqual(built['prompt']['8']['inputs']['length'], length)
                self.assertEqual(built['params']['length'], length)
                self.assertEqual(built['params']['duration_seconds'], seconds)
                self.assertEqual(built['params']['actual_duration_seconds'], length/24)
                again = self.store.build(built['params'])
                self.assertEqual(again['prompt']['8']['inputs'], built['prompt']['8']['inputs'])
    def test_legacy_defaults_remain_original(self):
        built = self.build()
        self.assertEqual(built['prompt']['8']['inputs'], self.graph['8']['inputs'])
        self.assertEqual(built['prompt']['9']['inputs']['steps'], 8)
        self.assertEqual(built['params']['length'],124)
    def test_automatic_mapping_and_custom_resolution(self):
        self.store.save({'id':'h3', 'workflow':self.graph, 'mapping':{}})
        built = self.build(duration_seconds=15, width=640, height=960)
        self.assertEqual(built['prompt']['8']['inputs'], {'prompt':'original','width':640,'height':960,'length':362})
    def test_invalid_duration_fps_dimensions_are_rejected(self):
        for params in [{'duration_seconds':0},{'duration_seconds':200},{'duration_seconds':'nan'},
                       {'duration_seconds':8,'fps':30},{'width':1280,'height':720}, {'width':0}]:
            with self.subTest(params=params), self.assertRaises(WorkflowError):
                self.build(**params)
    def test_legacy_nonpreset_length_and_basic_scheduler_are_preserved(self):
        self.graph['8']['inputs']['length'] = 175
        self.graph['9'] = {'class_type':'BasicScheduler','inputs':{'steps':11}}
        self.store.save({'id':'h3', 'workflow':self.graph, 'mapping':{'steps':'9.steps'}})
        built = self.build()
        self.assertEqual(built['prompt']['8']['inputs']['length'],175)
        self.assertEqual(built['params']['steps'],11)
        self.assertEqual(self.store.build(built['params'])['prompt']['8']['inputs']['length'],175)
        self.graph['8']['inputs']['length'] = 120
        self.store.save({'id':'h3','workflow':self.graph})
        self.assertEqual(self.build()['params']['length'],124)
    def test_graph_fps_must_match_h3(self):
        self.graph['10']['inputs']['fps'] = 30
        self.store.save({'id':'h3','workflow':self.graph})
        with self.assertRaises(WorkflowError): self.build(duration_seconds=8)
    def test_image_workflows_do_not_acquire_video_parameters(self):
        self.store.save({'id':'image', 'workflow':{'1':{'class_type':'SaveImage','inputs':{}}}})
        built = self.store.build({'workflow_id':'image','duration_seconds':15,'fps':24,'length':362,'actual_duration_seconds':362/24})
        for key in ['duration_seconds','fps','length','actual_duration_seconds']:
            self.assertNotIn(key, built['params'])
        self.assertEqual(built['prompt']['1']['inputs'],{})


    def test_frame_interp_off_keeps_24fps_graph(self):
        built = self.build(duration_seconds=5)
        self.assertEqual(built["prompt"]["10"]["inputs"]["fps"], 24)
        self.assertEqual(built["params"]["fps"], 24)
        self.assertEqual(built["params"]["frame_interp"], "off")
        self.assertEqual(built["params"]["output_fps"], 24)
        self.assertNotIn("RIFE VFI", {n["class_type"] for n in built["prompt"].values()})

    def test_frame_interp_48_pads_to_2x_and_sets_48fps(self):
        built = self.build(duration_seconds=5, frame_interp=48)
        rife = next(n for n in built["prompt"].values() if n["class_type"] == "RIFE VFI")
        self.assertEqual(rife["inputs"]["multiplier"], 2)
        self.assertEqual(rife["inputs"]["ckpt_name"], "rife49.pth")
        self.assertEqual(rife["inputs"]["frames"], ["26", 0])
        # CreateVideo images should come from a pad node, not raw RIFE (2n-1 + last = 2n)
        video = built["prompt"]["10"]
        self.assertEqual(video["inputs"]["fps"], 48)
        self.assertNotEqual(video["inputs"]["images"], rife["inputs"]["frames"])
        last = next(n for n in built["prompt"].values() if n["class_type"] == "ImageFromBatch")
        self.assertEqual(last["inputs"]["batch_index"], -1)
        self.assertEqual(built["params"]["fps"], 24)
        self.assertEqual(built["params"]["output_fps"], 48)
        self.assertEqual(built["params"]["actual_duration_seconds"], 124/24)

    def test_frame_interp_60_uses_3x_container_72(self):
        built = self.build(duration_seconds=15, frame_interp="60fps")
        rife = next(n for n in built["prompt"].values() if n["class_type"] == "RIFE VFI")
        self.assertEqual(rife["inputs"]["multiplier"], 3)
        self.assertEqual(built["prompt"]["10"]["inputs"]["fps"], 72)
        pads = [n for n in built["prompt"].values() if n["class_type"] == "ImageBatch"]
        self.assertEqual(len(pads), 2)
        self.assertEqual(built["params"]["fps"], 24)
        self.assertEqual(built["params"]["output_fps"], 60)
        self.assertEqual(built["params"]["length"], 362)
        self.assertEqual(built["params"]["actual_duration_seconds"], 362/24)
        # Audio stays on CreateVideo; only images are rewired.
        # Default test graph has no audio input; just ensure fps/images changed.

    def test_frame_interp_rejects_unknown_and_keeps_generation_at_24(self):
        from .workflows import WorkflowError
        with self.assertRaises(WorkflowError):
            self.build(frame_interp=30)
        built = self.build(duration_seconds=8, fps=24)
        self.assertEqual(built["params"]["fps"], 24)
        self.assertEqual(built["prompt"]["8"]["inputs"]["length"], 192)


if __name__ == '__main__': unittest.main()
