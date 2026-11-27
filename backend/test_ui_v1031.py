"""Tests for Draw 1.0.31: concurrent model sync (semaphore=2), quota tracking, and task metrics."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from .database import Database
from .runtime import Runtime
from .tasks import Tasks
from .workflows import WorkflowStore


class RuntimeQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_and_session_cu_calculations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cred = root / "colab.json"
            cred.write_text(json.dumps({
                "client_id": "cid", "client_secret": "sec", "refresh_token": "ref",
                "access_token": "tok", "expires_at": 9999999999,
            }))
            rt = Runtime(lambda: {"colab_credentials_file": str(cred)}, lambda *a: None)
            rt.path = root / "runtime.json"

            calls = []
            async def fake_tun(method, path, params=None, headers=None):
                calls.append(path)
                if path == "/tun/m/ccu-info":
                    return {
                        "currentBalance": 1949.5,
                        "consumptionRateHourly": 6.0,
                        "assignmentsCount": 1,
                        "eligibleGpus": ["G4", "A100", "L4"],
                    }
                return {}

            rt._tun = fake_tun
            rt.handle = {
                "state": "off",
                "started_at": None,
                "session_start_balance": 1950.0,
            }
            st = await rt.status()
            self.assertEqual(calls, ["/tun/m/ccu-info"])
            self.assertEqual(st["quota"]["balance"], 1949.5)
            self.assertEqual(st["quota"]["consumption_rate"], 6.0)
            self.assertIsNone(st["session_cu_actual"])

            # Cached within 60s TTL; when active endpoint exists, compute actual & estimated CU
            rt.handle = {
                "state": "ready",
                "endpoint": "gpu-a100-1",
                "started_at": 1000.0,
                "session_start_balance": 1950.0,
            }
            rt._status_checked = 1e18
            rt._quota_checked = 1e18
            with patch("backend.runtime.time.time", return_value=2800.0):
                st2 = await rt.status()
            self.assertEqual(len(calls), 1)
            self.assertEqual(st2["session_cu_actual"], 0.5)
            self.assertEqual(st2["session_cu_estimated"], 3.0)
            self.assertEqual(st2["session_cu_used"], 0.5)

    async def test_health_check_tolerates_single_transient_disconnect_and_recovers(self):
        from .runtime import AdapterError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rt = Runtime(lambda: {}, lambda *a: None)
            rt.path = root / "runtime.json"
            rt.handle = {"state": "ready", "endpoint": "gpu-g4-1", "gpu": "G4"}

            async def fake_assignments():
                return [{"endpoint": "gpu-g4-1", "runtimeProxyInfo": {"url": "https://proxy.example", "token": "tok", "tokenExpiresInSeconds": 3600}}]

            outcomes = iter([
                AdapterError("Jupyter控制通道超时，远端结果未知；未中断内核，不会自动重放代码"),
                AdapterError("Jupyter控制通道超时，远端结果未知；未中断内核，不会自动重放代码"),
                {"stdout": 'DRAWING_HEALTH:{"ok": true, "memory": "18000, 96000"}\n', "executionError": None},
            ])

            async def fake_execute(*args, **kwargs):
                nxt = next(outcomes)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt

            rt._assignments = fake_assignments
            rt.execute = fake_execute

            # 1st transient error keeps state ready and schedules earlier retry
            rt._status_checked = 0.0
            rt._health_checked = 0.0
            s1 = await rt.status()
            self.assertEqual(s1["state"], "ready")
            self.assertIsNone(s1["error"])
            self.assertEqual(rt._health_failures, 1)

            # 2nd consecutive error marks disconnected
            rt._status_checked = 0.0
            rt._health_checked = 0.0
            s2 = await rt.status()
            self.assertEqual(s2["state"], "disconnected")
            self.assertEqual(s2["error"], "Jupyter控制通道不可达")
            self.assertEqual(s2["message"], "Jupyter控制通道不可达")
            self.assertIsNone(s2["memory_used"])
            self.assertIsNone(s2["memory_total"])
            self.assertIsNotNone(s2["disconnected_at"])
            self.assertEqual(rt._health_failures, 2)

            # Subsequent success recovers to ready and resets failure counter
            rt._status_checked = 0.0
            rt._health_checked = 0.0
            s3 = await rt.status()
            self.assertEqual(s3["state"], "ready")
            self.assertIsNone(s3["error"])
            self.assertEqual(s3["message"], "ComfyUI已就绪")
            self.assertIsNone(s3["disconnected_at"])
            self.assertEqual(s3["memory_used"], 18000 * 1024**2)
            self.assertEqual(rt._health_failures, 0)

    async def test_disconnected_clears_stale_message_memory_and_freezes_uptime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []
            rt = Runtime(lambda: {}, lambda *args: events.append(args))
            rt.path = root / "runtime.json"
            rt.handle = {
                "state": "ready",
                "endpoint": "gpu-g4-1",
                "gpu": "G4",
                "started_at": 1000.0,
                "message": "ComfyUI已就绪",
                "memory_used": 20_000_000_000,
                "memory_total": 96_000_000_000,
                "logs": [{"time": 1000.0, "state": "ready", "message": "ComfyUI已就绪"}],
            }

            async def fake_assignments():
                return []

            rt._assignments = fake_assignments

            rt._status_checked = 0.0
            with patch("backend.runtime.time.time", return_value=87400.0):
                s1 = await rt.status()
            self.assertEqual(s1["state"], "disconnected")
            self.assertEqual(s1["error"], "Colab运行时已回收或释放")
            self.assertEqual(s1["message"], "Colab运行时已回收或释放")
            self.assertIsNone(s1["memory_used"])
            self.assertIsNone(s1["memory_total"])
            self.assertEqual(s1["disconnected_at"], 87400.0)
            self.assertEqual(s1["uptime"], 86400.0)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(s1["logs"]), 2)

            # Repeated status polls keep uptime frozen and avoid duplicate disconnect log entries
            rt._status_checked = 0.0
            with patch("backend.runtime.time.time", return_value=90000.0):
                s2 = await rt.status()
            self.assertEqual(s2["uptime"], 86400.0)
            self.assertEqual(s2["disconnected_at"], 87400.0)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(s2["logs"]), 2)


class TaskMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_records_gpu_media_kind_split_timings_and_cu_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            store = WorkflowStore(root)
            store.save({
                "id": "h3-test",
                "workflow": {
                    "1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"prompt": "p", "width": 864, "height": 480, "length": 124}},
                    "2": {"class_type": "CreateVideo", "inputs": {"fps": 24, "images": ["1", 0]}},
                    "3": {"class_type": "SaveVideo", "inputs": {"video": ["2", 0], "filename_prefix": "v"}},
                },
                "mapping": {"prompt": "1.prompt", "width": "1.width", "height": "1.height"},
            })

            balances = iter([
                {"balance": 100.0, "consumption_rate": 6.0},
                {"balance": 99.75, "consumption_rate": 6.0},
            ])

            class FakeRuntime:
                async def status(self):
                    return {"state": "ready", "instance_id": "inst-1", "gpu": "A100", "actual_gpu": "NVIDIA A100-SXM4-40GB"}
                async def quota(self, force=False):
                    return next(balances)

            class FakeComfy:
                async def request(self, method, path, **kwargs):
                    if path == "/object_info":
                        return {
                            "MiniMaxH3ReferenceToVideo": {"input": {"required": {"prompt": ["STRING", {}], "width": ["INT", {}], "height": ["INT", {}], "length": ["INT", {}]}}},
                            "CreateVideo": {"input": {"required": {"fps": ["FLOAT", {}]}}},
                            "SaveVideo": {"input": {"required": {"video": ["VIDEO", {}], "filename_prefix": ["STRING", {}]}}},
                        }
                    if path == "/prompt":
                        return {"prompt_id": "p-1"}
                    if path == "/history/p-1":
                        return {"p-1": {"status": {"completed": True}, "outputs": {"3": {"videos": [{"filename": "out.mp4", "type": "output"}]}}}}
                    if path.startswith("/view?"):
                        return b"mp4bytes"
                    raise AssertionError(path)

            class FakeGallery:
                async def archive(self, task_id, content, filename, metadata, parent_id=None):
                    return {"id": "gal-1", "filename": filename, "media_type": "video", "width": 864, "height": 480, "duration": 5.0}

            t = Tasks(db, FakeRuntime(), FakeComfy(), None, store, lambda *a: None, gallery=FakeGallery(), poll_interval=0.001)
            row = await t.submit({"workflow_id": "h3-test", "prompt": "hi", "duration_seconds": 5})
            self.assertEqual(row["media_kind"], "video")
            await t._execute_subscribed(row)
            done = db.get("tasks", row["id"])
            self.assertEqual(done["status"], "completed")
            self.assertEqual(done["gpu"], "NVIDIA A100-SXM4-40GB")
            self.assertEqual(done["actual_gpu"], "NVIDIA A100-SXM4-40GB")
            self.assertIn("sync_seconds", done)
            self.assertIn("gen_seconds", done)
            self.assertIn("total_seconds", done)
            self.assertEqual(done["cu_balance_start"], 100.0)
            self.assertEqual(done["cu_balance_end"], 99.75)
            self.assertEqual(done["cu_actual_delta"], 0.25)
            self.assertGreaterEqual(done["cu_estimated"], 0.0)


class ConcurrentModelSyncApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_sync_runs_two_concurrently_queues_third_and_allows_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            for idx in range(1, 5):
                db.save("models", {
                    "id": f"m{idx}",
                    "name": f"model-{idx}.safetensors",
                    "filename": f"model-{idx}.safetensors",
                    "kind": "diffusion_models",
                    "size": 1000 * idx,
                    "status": "drive",
                    "drive_file_id": f"drv-{idx}",
                })

            from . import main

            class FakeRuntime:
                async def status(self):
                    return {"state": "ready", "gpu": "A100"}

            in_flight = set()
            max_seen = 0
            gates = {f"m{i}": asyncio.Event() for i in range(1, 5)}
            started_events = {f"m{i}": asyncio.Event() for i in range(1, 5)}

            class FakeComfy:
                async def sync_model(self, row, drive, on_progress=None):
                    nonlocal max_seen
                    mid = row["id"]
                    in_flight.add(mid)
                    max_seen = max(max_seen, len(in_flight))
                    started_events[mid].set()
                    if on_progress:
                        await on_progress({"stage": "downloading", "downloaded_bytes": 50, "total_bytes": row["size"], "download_progress": 0.5})
                    try:
                        await gates[mid].wait()
                    finally:
                        in_flight.discard(mid)
                    return {"path": f"diffusion_models/{row['filename']}"}

            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", FakeRuntime()), \
                 patch.object(main, "comfy", FakeComfy()), \
                 patch.object(main, "drive", object()), \
                 patch.object(main.auth, "check", return_value="test"):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                    res = await client.post("/api/models/sync-batch", json={"ids": ["m1", "m2", "m3"]})
                    self.assertEqual(res.status_code, 202)
                    body = res.json()
                    self.assertEqual(body["concurrency"], 2)
                    self.assertEqual(body["started"], ["m1", "m2", "m3"])

                    await asyncio.wait_for(started_events["m1"].wait(), timeout=2)
                    await asyncio.wait_for(started_events["m2"].wait(), timeout=2)
                    await asyncio.sleep(0.05)

                    # Only 2 should be running concurrently; m3 must wait in queued_sync
                    self.assertEqual(max_seen, 2)
                    self.assertFalse(started_events["m3"].is_set())
                    self.assertEqual(db.get("models", "m3")["status"], "queued_sync")

                    # Single-model cancel works even on a running or queued item without deadlocking others
                    cancel_res = await client.post("/api/models/m3/sync/cancel")
                    self.assertEqual(cancel_res.status_code, 200)
                    self.assertEqual(cancel_res.json()["status"], "cancelled")

                    # Queue m4 via single sync endpoint (non-blocking 202)
                    single_res = await client.post("/api/models/m4/sync")
                    self.assertEqual(single_res.status_code, 202)
                    self.assertEqual(single_res.json()["status"], "queued_sync")

                    gates["m1"].set()
                    await asyncio.wait_for(started_events["m4"].wait(), timeout=2)
                    gates["m2"].set()
                    gates["m4"].set()
                    await asyncio.gather(*list(main.sync_tasks.values()), return_exceptions=True)

                    self.assertEqual(db.get("models", "m1")["status"], "ready")
                    self.assertEqual(db.get("models", "m2")["status"], "ready")
                    self.assertEqual(db.get("models", "m3")["status"], "cancelled")
                    self.assertEqual(db.get("models", "m4")["status"], "ready")
                    self.assertEqual(max_seen, 2)


if __name__ == "__main__":
    unittest.main()
