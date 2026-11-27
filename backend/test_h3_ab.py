"""Tests for H3 Route A (multi-keyframe timeline) & Route B (tail-clip continuation) workflows."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from .workflows import WorkflowStore, WorkflowError, compute_keyframe_indices
from .tasks import Tasks
from .database import Database


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


class H3WorkflowCatalog(unittest.TestCase):
    def test_all_h3_workflows_clear_precision_naming_and_valid_build(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = WorkflowStore(Path(tmp.name))
        files = sorted(WORKFLOWS_DIR.glob("h3-*.json"))
        self.assertEqual(len(files), 16, "Expected 8 Ref2VA + 4 Route A + 4 Route B H3 workflows")
        for path in files:
            record = json.loads(path.read_text("utf-8"))
            name = record["name"]
            unet = next(n["inputs"]["unet_name"] for n in record["workflow"].values() if n["class_type"] == "UNETLoader")
            if "int8" in unet:
                self.assertIn("[INT8]", name, f"{record['id']} uses int8 UNET but name lacks [INT8]: {name}")
                self.assertNotIn("[BF16]", name)
            else:
                self.assertIn("[BF16]", name, f"{record['id']} uses bf16 UNET but name lacks [BF16]: {name}")
                self.assertNotIn("[INT8]", name)
            store.save(record)
            built = store.build({"workflow_id": record["id"]})
            self.assertEqual(built["params"]["fps"], 24)
            self.assertTrue(built["output_nodes"])


class H3RouteAKeyframes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))
        for fname in ("h3-fl2va-keyframes-8step-bf16.json", "h3-fl2va-keyframes-8step-int8.json", "h3-fl2va-keyframes-hq20-bf16.json"):
            self.store.save(json.loads((WORKFLOWS_DIR / fname).read_text("utf-8")))

    def test_four_keyframes_over_15s_anchor_evenly_with_add_guide_chain(self):
        built = self.store.build({
            "workflow_id": "h3-fl2va-keyframes-8step-bf16",
            "duration_seconds": 15,
            "keyframes": ["k1.png", "k2.png", "k3.png", "k4.png"],
        })
        self.assertEqual(built["params"]["length"], 362)
        self.assertEqual(built["params"]["keyframe_frames"], [0, 120, 241, 361])
        prompt = built["prompt"]
        # Old placeholder LoadImage nodes 1 and 2 must be removed
        self.assertNotIn("1", prompt)
        self.assertNotIn("2", prompt)
        i2v = prompt["20"]["inputs"]
        self.assertEqual(prompt[i2v["first_frame"][0]]["inputs"]["image"], "k1.png")
        self.assertEqual(prompt[i2v["last_frame"][0]]["inputs"]["image"], "k4.png")
        guides = {k: n for k, n in prompt.items() if n["class_type"] == "MiniMaxH3AddGuide"}
        self.assertEqual(len(guides), 2)
        by_idx = {n["inputs"]["frame_idx"]: n for n in guides.values()}
        self.assertEqual(sorted(by_idx.keys()), [120, 241])
        self.assertEqual(prompt[by_idx[120]["inputs"]["image"][0]]["inputs"]["image"], "k2.png")
        self.assertEqual(prompt[by_idx[241]["inputs"]["image"][0]]["inputs"]["image"], "k3.png")
        # Chaining: I2V[20,0] -> guide(120) -> guide(241) -> BasicGuider[24].conditioning
        g1_id = next(k for k, n in guides.items() if n["inputs"]["frame_idx"] == 120)
        g2_id = next(k for k, n in guides.items() if n["inputs"]["frame_idx"] == 241)
        self.assertEqual(prompt[g1_id]["inputs"]["positive"], ["20", 0])
        self.assertEqual(prompt[g2_id]["inputs"]["positive"], [g1_id, 0])
        self.assertEqual(prompt["24"]["inputs"]["conditioning"], [g2_id, 0])

    def test_single_and_two_and_zero_keyframes(self):
        one = self.store.build({"workflow_id": "h3-fl2va-keyframes-8step-int8", "keyframes": ["only.png"]})
        self.assertEqual(one["params"]["keyframe_frames"], [0])
        self.assertIn("first_frame", one["prompt"]["20"]["inputs"])
        self.assertNotIn("last_frame", one["prompt"]["20"]["inputs"])
        self.assertFalse(any(n["class_type"] == "MiniMaxH3AddGuide" for n in one["prompt"].values()))

        two = self.store.build({"workflow_id": "h3-fl2va-keyframes-8step-int8", "keyframes": ["a.png", "b.png"]})
        self.assertEqual(two["params"]["keyframe_frames"], [0, 123])
        self.assertIn("first_frame", two["prompt"]["20"]["inputs"])
        self.assertIn("last_frame", two["prompt"]["20"]["inputs"])
        self.assertFalse(any(n["class_type"] == "MiniMaxH3AddGuide" for n in two["prompt"].values()))

        zero = self.store.build({"workflow_id": "h3-fl2va-keyframes-8step-int8", "keyframes": []})
        self.assertNotIn("first_frame", zero["prompt"]["20"]["inputs"])
        self.assertNotIn("last_frame", zero["prompt"]["20"]["inputs"])
        self.assertFalse(any(n["class_type"] == "LoadImage" for n in zero["prompt"].values()))

    def test_custom_keyframe_frames_and_bounds(self):
        built = self.store.build({
            "workflow_id": "h3-fl2va-keyframes-hq20-bf16",
            "duration_seconds": 15,
            "keyframes": ["a.png", "b.png", "c.png"],
            "keyframe_frames": [0, 75, 361],
        })
        self.assertEqual(built["params"]["keyframe_frames"], [0, 75, 361])
        guides = [n for n in built["prompt"].values() if n["class_type"] == "MiniMaxH3AddGuide"]
        self.assertEqual(len(guides), 1)
        self.assertEqual(guides[0]["inputs"]["frame_idx"], 75)

        with self.assertRaises(WorkflowError):
            self.store.build({
                "workflow_id": "h3-fl2va-keyframes-hq20-bf16",
                "duration_seconds": 5,
                "keyframes": ["a.png", "b.png"],
                "keyframe_frames": [0, 200],
            })

    def test_rife_fast_mode_disabled_for_quality(self):
        built = self.store.build({
            "workflow_id": "h3-fl2va-keyframes-8step-bf16",
            "keyframes": ["a.png", "b.png"],
            "frame_interp": 48,
        })
        rife = next(n for n in built["prompt"].values() if n["class_type"] == "RIFE VFI")
        self.assertFalse(rife["inputs"]["fast_mode"])
        self.assertTrue(rife["inputs"]["ensemble"])
        self.assertEqual(built["prompt"]["28"]["inputs"]["fps"], 48)


class H3RouteBContinue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))
        for fname in ("h3-fl2va-continue-8step-bf16.json", "h3-fl2va-continue-8step-int8.json"):
            self.store.save(json.loads((WORKFLOWS_DIR / fname).read_text("utf-8")))

    def test_continue_default_5_frames_and_22_frames(self):
        b5 = self.store.build({
            "workflow_id": "h3-fl2va-continue-8step-int8",
            "source_video": "part1.mp4",
        })
        self.assertEqual(b5["params"]["continue_frames"], 5)
        self.assertEqual(b5["prompt"]["1"]["inputs"]["file"], "part1.mp4")
        self.assertEqual(b5["prompt"]["3"]["inputs"]["batch_index"], -5)
        self.assertEqual(b5["prompt"]["3"]["inputs"]["length"], 5)
        self.assertEqual(b5["prompt"]["4"]["class_type"], "TrimAudioDuration")
        self.assertAlmostEqual(b5["prompt"]["4"]["inputs"]["start_index"], -0.2083, places=4)
        self.assertAlmostEqual(b5["prompt"]["4"]["inputs"]["duration"], 0.2083, places=4)
        self.assertEqual(b5["prompt"]["30"]["inputs"]["audio"], ["4", 0])
        self.assertEqual(b5["prompt"]["30"]["inputs"]["audio_vae"], ["14", 0])
        self.assertEqual(b5["prompt"]["30"]["inputs"]["frame_idx"], 0)

        b22 = self.store.build({
            "workflow_id": "h3-fl2va-continue-8step-bf16",
            "source_video": "part1.mp4",
            "continue_frames": 22,
            "duration_seconds": 8,
        })
        self.assertEqual(b22["params"]["continue_frames"], 22)
        self.assertEqual(b22["prompt"]["3"]["inputs"]["batch_index"], -22)
        self.assertEqual(b22["prompt"]["3"]["inputs"]["length"], 22)
        self.assertAlmostEqual(b22["prompt"]["4"]["inputs"]["start_index"], -0.9167, places=4)
        self.assertAlmostEqual(b22["prompt"]["4"]["inputs"]["duration"], 0.9167, places=4)

        with self.assertRaises(WorkflowError):
            self.store.build({
                "workflow_id": "h3-fl2va-continue-8step-bf16",
                "source_video": "part1.mp4",
                "continue_frames": 10,  # not 17k+5
            })

    def test_continue_with_target_keyframes_preserves_frame0_video_guide(self):
        built = self.store.build({
            "workflow_id": "h3-fl2va-continue-8step-bf16",
            "source_video": "part1.mp4",
            "duration_seconds": 5,
            "keyframes": ["mid.png", "end.png"],
        })
        self.assertEqual(built["params"]["keyframe_frames"], [62, 123])
        prompt = built["prompt"]
        # Frame 0 must NOT be occupied by first_frame; video tail guide node 30 stays at frame 0
        self.assertNotIn("first_frame", prompt["20"]["inputs"])
        self.assertEqual(prompt[prompt["20"]["inputs"]["last_frame"][0]]["inputs"]["image"], "end.png")
        mid_guides = [k for k, n in prompt.items() if n["class_type"] == "MiniMaxH3AddGuide" and k != "30"]
        self.assertEqual(len(mid_guides), 1)
        mid_id = mid_guides[0]
        self.assertEqual(prompt[mid_id]["inputs"]["frame_idx"], 62)
        self.assertEqual(prompt[mid_id]["inputs"]["positive"], ["30", 0])
        self.assertEqual(prompt["24"]["inputs"]["conditioning"], [mid_id, 0])

        # Reject custom frame inside overlap prefix
        with self.assertRaises(WorkflowError):
            self.store.build({
                "workflow_id": "h3-fl2va-continue-8step-bf16",
                "source_video": "part1.mp4",
                "continue_frames": 22,
                "keyframes": ["bad.png"],
                "keyframe_frames": [10],
            })

    def test_upload_inputs_handles_load_video_from_gallery(self):
        data_dir = Path(self.tmp.name)
        db = Database(data_dir / "studio.sqlite3")
        video_file = data_dir / "images" / "clip1.mp4"
        video_file.parent.mkdir(parents=True, exist_ok=True)
        video_file.write_bytes(b"fake-mp4-bytes")
        db.save("gallery", {
            "id": "vid1",
            "filename": "clip1.mp4",
            "local_path": str(video_file),
            "media_type": "video",
        })

        uploaded_calls = []

        class FakeComfy:
            async def request(self, method, path, **kwargs):
                uploaded_calls.append((method, path, kwargs))
                return {"name": "remote-clip1.mp4", "subfolder": ""}

        tasks = Tasks(db, None, FakeComfy(), None, self.store, lambda *a: None)
        built = self.store.build({
            "workflow_id": "h3-fl2va-continue-8step-int8",
            "source_video": "clip1.mp4",
            "source_video_id": "vid1",
        })
        row = {"id": "task123", "params": built["params"], "prompt": built["prompt"]}
        out = asyncio.run(tasks._upload_inputs(row))
        self.assertEqual(len(uploaded_calls), 1)
        self.assertEqual(uploaded_calls[0][1], "/upload/image")
        self.assertEqual(out["1"]["inputs"]["file"], "remote-clip1.mp4")

    def test_upload_inputs_backfills_missing_gallery_video_from_drive(self):
        data_dir = Path(self.tmp.name)
        db = Database(data_dir / "studio.sqlite3")
        missing_video = data_dir / "images" / "archived.mp4"
        db.save("gallery", {
            "id": "vid_drive",
            "filename": "archived.mp4",
            "local_path": str(missing_video),
            "drive_file_id": "drive-file-123",
            "media_type": "video",
        })

        downloaded = []
        uploaded = []

        class FakeDrive:
            async def download(self, file_id, dest):
                downloaded.append((file_id, str(dest)))
                Path(dest).write_bytes(b"restored-from-drive")

        class FakeComfy:
            async def request(self, method, path, **kwargs):
                uploaded.append((method, path))
                return {"name": "remote-archived.mp4", "subfolder": ""}

        tasks = Tasks(db, None, FakeComfy(), FakeDrive(), self.store, lambda *a: None)
        built = self.store.build({
            "workflow_id": "h3-fl2va-continue-8step-int8",
            "source_video": "archived.mp4",
            "source_video_id": "vid_drive",
        })
        row = {"id": "task456", "params": built["params"], "prompt": built["prompt"]}
        out = asyncio.run(tasks._upload_inputs(row))
        self.assertEqual(len(downloaded), 1)
        self.assertEqual(downloaded[0][0], "drive-file-123")
        self.assertTrue(missing_video.is_file())
        self.assertEqual(out["1"]["inputs"]["file"], "remote-archived.mp4")


class KeyframeIndexMath(unittest.TestCase):
    def test_compute_keyframe_indices(self):
        self.assertEqual(compute_keyframe_indices(0, 124), [])
        self.assertEqual(compute_keyframe_indices(1, 124), [0])
        self.assertEqual(compute_keyframe_indices(2, 124), [0, 123])
        self.assertEqual(compute_keyframe_indices(4, 362), [0, 120, 241, 361])
        self.assertEqual(compute_keyframe_indices(1, 124, continue_mode=True), [123])
        self.assertEqual(compute_keyframe_indices(2, 124, continue_mode=True), [62, 123])


if __name__ == "__main__":
    unittest.main()
