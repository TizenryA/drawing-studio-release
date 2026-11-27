"""Tests for SeedVR2 video upscaling, high-quality video export CRF, and model resolution priority."""
import asyncio
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .database import Database
from .gallery import Gallery
from .tasks import Tasks
from .workflows import WorkflowError, WorkflowStore, adapt_save_video, parse_video_crf


def _make_dynamic_save_video_info():
    return {
        "SaveVideo": {
            "input": {
                "required": {
                    "video": ["VIDEO", {}],
                    "filename_prefix": ["STRING", {"default": "video/ComfyUI"}],
                    "format": [
                        "COMFY_DYNAMICCOMBO_V3",
                        {
                            "options": [
                                {"key": "auto", "inputs": {"required": {}}},
                                {
                                    "key": "mp4",
                                    "inputs": {
                                        "required": {
                                            "codec": [
                                                "COMFY_DYNAMICCOMBO_V3",
                                                {
                                                    "options": [
                                                        {"key": "auto", "inputs": {"required": {}}},
                                                        {
                                                            "key": "h264",
                                                            "inputs": {
                                                                "optional": {
                                                                    "encoding": [
                                                                        "COMFY_DYNAMICCOMBO_V3",
                                                                        {
                                                                            "options": [
                                                                                {"key": "auto", "inputs": {"required": {}}},
                                                                                {
                                                                                    "key": "re-encode",
                                                                                    "inputs": {
                                                                                        "required": {
                                                                                            "crf": ["FLOAT", {"default": 18.0, "min": 0.0, "max": 51.0, "step": 1.0}],
                                                                                        }
                                                                                    },
                                                                                },
                                                                            ]
                                                                        },
                                                                    ]
                                                                }
                                                            },
                                                        },
                                                    ]
                                                },
                                            ]
                                        }
                                    },
                                },
                            ]
                        },
                    ],
                },
                "optional": {},
            }
        }
    }


class SeedVR2VideoWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name))

    def test_builtin_list_and_graph_wiring(self):
        listed = {w["id"]: w for w in self.store.list()}
        self.assertIn("video-seedvr2", listed)
        self.assertEqual(listed["video-seedvr2"]["mode"], "video_seedvr2")
        self.assertEqual(listed["video-seedvr2"]["dependency"], "ComfyUI-SeedVR2_VideoUpscaler")

        built = self.store.build({
            "mode": "video_seedvr2",
            "workflow_id": "video-seedvr2",
            "source_video": "clip.mp4",
            "source_video_id": "vid1",
            "resolution": 1080,
            "frame_batch_size": 9,
            "temporal_overlap": 2,
            "color_correction": "wavelet",
            "blocks_to_swap": 16,
            "swap_io_components": True,
            "video_crf": 12,
            "n": 3,
        })
        prompt = built["prompt"]
        self.assertEqual(built["output_nodes"], ["7"])
        self.assertEqual(built["params"]["n"], 1)
        self.assertEqual(built["params"]["batch_size"], 1)
        self.assertEqual(built["params"]["frame_batch_size"], 9)
        self.assertEqual(built["params"]["video_crf"], 12)
        self.assertTrue(built["params"]["free_gpu_before_run"])
        self.assertLessEqual(built["params"]["seed"], 4294967294)

        self.assertEqual(prompt["1"]["class_type"], "LoadVideo")
        self.assertEqual(prompt["1"]["inputs"]["file"], "clip.mp4")
        self.assertEqual(prompt["2"]["class_type"], "GetVideoComponents")
        self.assertEqual(prompt["2"]["inputs"]["video"], ["1", 0])
        self.assertEqual(prompt["3"]["class_type"], "SeedVR2LoadDiTModel")
        self.assertEqual(prompt["3"]["inputs"]["blocks_to_swap"], 16)
        self.assertTrue(prompt["3"]["inputs"]["swap_io_components"])
        self.assertEqual(prompt["5"]["class_type"], "SeedVR2VideoUpscaler")
        self.assertEqual(prompt["5"]["inputs"]["image"], ["2", 0])
        self.assertEqual(prompt["5"]["inputs"]["resolution"], 1080)
        self.assertEqual(prompt["5"]["inputs"]["batch_size"], 9)
        self.assertEqual(prompt["5"]["inputs"]["temporal_overlap"], 2)
        self.assertEqual(prompt["5"]["inputs"]["color_correction"], "wavelet")
        self.assertEqual(prompt["6"]["class_type"], "CreateVideo")
        self.assertEqual(prompt["6"]["inputs"]["images"], ["5", 0])
        self.assertEqual(prompt["6"]["inputs"]["fps"], ["2", 2])
        self.assertEqual(prompt["6"]["inputs"]["audio"], ["2", 1])
        self.assertEqual(prompt["7"]["class_type"], "SaveVideo")

    def test_validation_rejects_invalid_video_seedvr2_params(self):
        for bad in [
            {"workflow_id": "video-seedvr2"},  # missing source_video
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "resolution": 1081},
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "frame_batch_size": 4},
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "frame_batch_size": 5, "temporal_overlap": 5},
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "color_correction": "invalid"},
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "video_crf": 35},
            {"workflow_id": "video-seedvr2", "source_video": "a.mp4", "video_crf": -1},
        ]:
            with self.subTest(bad=bad), self.assertRaises(WorkflowError):
                self.store.build(bad)

    def test_resolve_prefers_valid_drive_record_over_broken_record(self):
        broken_first_catalog = [
            {"id": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "filename": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "kind": None, "status": "error", "drive_file_id": None},
            {"id": "1YYxEKLbE3eCvwmQCv8POAB59bo8-Jp7Y", "filename": "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "kind": "seedvr2", "status": "cancelled", "drive_file_id": "1YYxEKLbE3eCvwmQCv8POAB59bo8-Jp7Y"},
            {"id": "1Ksbkk9hcOB3BzC81rDqVXR7lbQeN5Jex", "filename": "ema_vae_fp16.safetensors", "kind": "seedvr2", "status": "cached", "drive_file_id": "1Ksbkk9hcOB3BzC81rDqVXR7lbQeN5Jex"},
        ]
        built = self.store.build({
            "workflow_id": "video-seedvr2",
            "source_video": "clip.mp4",
        }, models=broken_first_catalog)
        dit_req = next(m for m in built["required_models"] if m["comfy_name"] == "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
        self.assertEqual(dit_req["id"], "1YYxEKLbE3eCvwmQCv8POAB59bo8-Jp7Y")
        self.assertEqual(dit_req["drive_file_id"], "1YYxEKLbE3eCvwmQCv8POAB59bo8-Jp7Y")


class SaveVideoAdaptationTests(unittest.TestCase):
    def test_adapt_save_video_applies_crf_when_supported_and_skips_when_not(self):
        base = {
            "29": {"class_type": "SaveVideo", "inputs": {"video": ["28", 0], "filename_prefix": "video/H3", "format": "auto", "format.codec": "auto"}},
            "30": {"class_type": "SaveVideo", "inputs": {"video": ["28", 0], "filename_prefix": "video/Pro", "format": "mov", "format.codec": "prores"}},
        }
        adapted = adapt_save_video(base, _make_dynamic_save_video_info(), crf=15)
        self.assertEqual(adapted["29"]["inputs"]["format"], "mp4")
        self.assertEqual(adapted["29"]["inputs"]["format.codec"], "h264")
        self.assertEqual(adapted["29"]["inputs"]["format.codec.encoding"], "re-encode")
        self.assertEqual(adapted["29"]["inputs"]["format.codec.encoding.crf"], 15.0)
        # Non-h264/auto format must not be overridden
        self.assertEqual(adapted["30"]["inputs"]["format"], "mov")
        self.assertEqual(adapted["30"]["inputs"]["format.codec"], "prores")
        # Legacy object_info without DynamicCombo leaves graph unchanged
        legacy = adapt_save_video(base, {"SaveVideo": {"input": {"required": {"format": [["auto", "mp4"]]}}}}, crf=15)
        self.assertEqual(legacy, base)

    def test_parse_video_crf(self):
        self.assertEqual(parse_video_crf(None), 15)
        self.assertEqual(parse_video_crf("14"), 14)
        with self.assertRaises(WorkflowError):
            parse_video_crf(True)
        with self.assertRaises(WorkflowError):
            parse_video_crf(31)


class TasksAndGalleryVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_seedvr2_frees_gpu_before_prompt_and_preserves_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            store = WorkflowStore(root)
            gallery = Gallery(db, None, root)
            async def noop_retry(rec): return rec
            gallery.retry_archive = noop_retry

            src_video = root / "images" / "src.mp4"
            src_video.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.4:r=24",
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.4", "-y", str(src_video)],
                check=True,
            )
            db.save("gallery", {
                "id": "vid-src-1",
                "filename": "src.mp4",
                "local_path": str(src_video),
                "media_type": "video",
            })
            # Reject non-video gallery items
            db.save("gallery", {"id": "img-1", "filename": "x.png", "local_path": str(src_video), "media_type": "image"})

            calls = []
            out_video = root / "out_upscaled.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=128x128:d=0.4:r=24",
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.4", "-y", str(out_video)],
                check=True,
            )
            out_bytes = out_video.read_bytes()

            class FakeRuntime:
                async def status(self): return {"state": "ready", "instance_id": "gpu-1"}

            class FakeComfy:
                async def request(self, method, path, **kwargs):
                    calls.append((method, path, kwargs))
                    if path == "/object_info":
                        return {
                            "LoadVideo": {"input": {"required": {"file": [["existing.mp4"]]}}},
                            "GetVideoComponents": {"input": {"required": {"video": ["VIDEO", {}]}}},
                            "SeedVR2LoadDiTModel": {"input": {"required": {"model": [["seedvr2_ema_3b_fp8_e4m3fn.safetensors"]]}}},
                            "SeedVR2LoadVAEModel": {"input": {"required": {"model": [["ema_vae_fp16.safetensors"]]}}},
                            "SeedVR2VideoUpscaler": {"input": {"required": {"image": ["IMAGE", {}], "dit": ["SEEDVR2_DIT", {}], "vae": ["SEEDVR2_VAE", {}], "seed": ["INT", {}], "resolution": ["INT", {}], "max_resolution": ["INT", {}], "batch_size": ["INT", {}], "uniform_batch_size": ["BOOLEAN", {}], "color_correction": [["lab", "wavelet", "wavelet_adaptive", "hsv", "adain", "none"]]}, "optional": {"temporal_overlap": ["INT", {}], "prepend_frames": ["INT", {}], "input_noise_scale": ["FLOAT", {}], "latent_noise_scale": ["FLOAT", {}], "offload_device": ["STRING", {}], "enable_debug": ["BOOLEAN", {}]}}},
                            "CreateVideo": {"input": {"required": {"images": ["IMAGE", {}], "fps": ["FLOAT", {}]}, "optional": {"audio": ["AUDIO", {}]}}},
                            **_make_dynamic_save_video_info(),
                        }
                    if path == "/upload/image":
                        return {"name": "uploaded-src.mp4", "subfolder": ""}
                    if path == "/free":
                        return {}
                    if path == "/prompt":
                        return {"prompt_id": "p-123"}
                    if path == "/history/p-123":
                        return {"p-123": {"status": {"completed": True, "status_str": "success"}, "outputs": {"7": {"videos": [{"filename": "up.mp4", "type": "output", "subfolder": ""}]}}}}
                    if path.startswith("/view?"):
                        return out_bytes
                    raise AssertionError(f"Unexpected comfy call: {method} {path}")

            t = Tasks(db, FakeRuntime(), FakeComfy(), None, store, lambda *a: None, gallery=gallery, poll_interval=0.001)
            with self.assertRaises(WorkflowError):
                await t.submit({"workflow_id": "video-seedvr2", "source_video_id": "img-1"})

            row = await t.submit({
                "workflow_id": "video-seedvr2",
                "mode": "video_seedvr2",
                "source_video_id": "vid-src-1",
                "resolution": 1080,
                "video_crf": 14,
            })
            await t._execute_subscribed(row)
            paths = [c[1] for c in calls]
            self.assertIn("/free", paths)
            self.assertIn("/prompt", paths)
            self.assertLess(paths.index("/free"), paths.index("/prompt"))
            prompt_payload = next(c[2]["json"]["prompt"] for c in calls if c[1] == "/prompt")
            self.assertEqual(prompt_payload["7"]["inputs"]["format"], "mp4")
            self.assertEqual(prompt_payload["7"]["inputs"]["format.codec"], "h264")
            self.assertEqual(prompt_payload["7"]["inputs"]["format.codec.encoding"], "re-encode")
            self.assertEqual(prompt_payload["7"]["inputs"]["format.codec.encoding.crf"], 14.0)
            done = db.get("tasks", row["id"])
            self.assertEqual(done["status"], "completed")
            self.assertEqual(done["results"][0]["parent_id"], "vid-src-1")
            await asyncio.sleep(0)

    async def test_conform_60fps_uses_explicit_high_crf_and_preserves_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "clip72.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=128x128:rate=72:duration=0.5",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=0.5",
                 "-c:v", "libx264", "-crf", "10", "-c:a", "aac", "-y", str(src)],
                check=True,
            )
            copy15 = root / "c15.mp4"
            copy23 = root / "c23.mp4"
            copy15.write_bytes(src.read_bytes())
            copy23.write_bytes(src.read_bytes())
            Gallery.conform_output_fps(copy15, {"frame_interp": 60, "video_crf": 15})
            Gallery.conform_output_fps(copy23, {"frame_interp": 60, "video_crf": 23})
            self.assertGreater(copy15.stat().st_size, copy23.stat().st_size)

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate", "-of", "json", str(copy15)],
                capture_output=True, text=True, check=True,
            )
            streams = json.loads(probe.stdout)["streams"]
            vstream = next(s for s in streams if s["codec_type"] == "video")
            astream = next((s for s in streams if s["codec_type"] == "audio"), None)
            self.assertEqual(vstream["r_frame_rate"], "60/1")
            self.assertIsNotNone(astream)

            out_concat = root / "concat.mp4"
            Gallery.concat_videos([copy15, copy15], out_concat, trim_overlap_frames=2, crf=15)
            self.assertTrue(out_concat.is_file() and out_concat.stat().st_size > 0)

    async def test_health_endpoint_reports_1_0_42(self):
        import httpx
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DRAWING_DATA": tmp}):
            from . import main
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                r = await client.get("/api/health")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json(), {"ok": True, "version": "1.0.42"})


if __name__ == "__main__":
    unittest.main()
