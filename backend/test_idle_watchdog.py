"""Regression tests for Issue #1: idle watchdog timing, per-instance mode, and stop logs."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

if "DRAWING_DATA" not in os.environ:
    os.environ["DRAWING_DATA"] = tempfile.mkdtemp(prefix="drawing-test-idle-")

from . import main
from .database import Database
from .runtime import Runtime


def _dummy_cred(root: Path) -> Path:
    cred = root / "colab.json"
    cred.write_text(json.dumps({
        "client_id": "cid",
        "client_secret": "sec",
        "refresh_token": "ref",
        "access_token": "tok",
        "expires_at": 9999999999,
    }))
    return cred


class IdleWatchdogTests(unittest.IsolatedAsyncioTestCase):
    def _make_runtime(self, root: Path, *, mode="temporary", state="ready", endpoint="ep-test", ready_at=1000.0, started_at=900.0):
        cred = _dummy_cred(root)
        rt = Runtime(lambda: {"colab_credentials_file": str(cred), "mode": mode}, lambda *a: None)
        rt.path = root / "runtime.json"
        rt.handle = {
            "state": state,
            "gpu": "A100",
            "mode": mode,
            "endpoint": endpoint,
            "started_at": started_at,
            "ready_at": ready_at,
            "last_activity": ready_at,
            "logs": [
                {"time": started_at, "state": "allocating", "message": "正在核对GPU可用性"},
                {"time": started_at + 10, "state": "bootstrapping", "message": "正在启动ComfyUI"},
                {"time": ready_at, "state": "ready", "message": "ComfyUI已就绪"},
            ],
        }
        rt.last_activity = float(ready_at if ready_at is not None else started_at)
        rt._status_checked = 1e18
        rt._quota_checked = 1e18
        return rt

    async def test_control_plane_idle_before_boot_does_not_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            t_ready = 20000.0
            rt = self._make_runtime(root, mode="temporary", ready_at=t_ready, started_at=t_ready - 120)
            stop_calls = []

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.stop = fake_stop
            stale_tasks = SimpleNamespace(last_activity=t_ready - 18000.0)  # 5h idle before boot
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", stale_tasks), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 60}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick(now=t_ready + 10.0))
                self.assertEqual(stop_calls, [])
                self.assertEqual(rt.handle["state"], "ready")

                # Once genuine idle duration passes idle_minutes after ready_at, auto-stop fires.
                self.assertTrue(await main.idle_watchdog_tick(now=t_ready + 3605.0))
                self.assertEqual(len(stop_calls), 1)
                self.assertEqual(stop_calls[0]["trigger"], "idle_watchdog")
                self.assertTrue(stop_calls[0]["require_ready"])
                self.assertIn("空闲超时自动关机", stop_calls[0]["reason"])

    async def test_long_bootstrap_uses_ready_at_not_started_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            t_ready = 5000.0
            # Bootstrap took 20 minutes while threshold is 10 minutes.
            rt = self._make_runtime(root, mode="temporary", ready_at=t_ready, started_at=t_ready - 1200.0)
            stop_calls = []

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.stop = fake_stop
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=t_ready - 3000.0)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick(now=t_ready + 30.0))
                self.assertEqual(stop_calls, [])

                self.assertTrue(await main.idle_watchdog_tick(now=t_ready + 605.0))
                self.assertEqual(len(stop_calls), 1)

    async def test_per_instance_mode_overrides_global_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            t_ready = 1000.0

            # Case 1: Instance started as persistent, even though global settings switched to temporary.
            rt_persist = self._make_runtime(root, mode="persistent", ready_at=t_ready, started_at=900.0)
            persist_stops = []

            async def stop_persist(**kwargs):
                persist_stops.append(kwargs)
                return rt_persist._public()

            rt_persist.stop = stop_persist
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt_persist), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=t_ready)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick(now=t_ready + 7200.0))
                self.assertEqual(persist_stops, [])

            # Case 2: Instance started as temporary, even though global settings switched to persistent.
            rt_temp = self._make_runtime(root, mode="temporary", ready_at=t_ready, started_at=900.0)
            temp_stops = []

            async def stop_temp(**kwargs):
                temp_stops.append(kwargs)
                return rt_temp._public()

            rt_temp.stop = stop_temp
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt_temp), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=t_ready)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "persistent", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertTrue(await main.idle_watchdog_tick(now=t_ready + 650.0))
                self.assertEqual(len(temp_stops), 1)

    async def test_model_sync_and_comfy_proxy_extend_idle_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            t_ready = 1000.0
            rt = self._make_runtime(root, mode="temporary", ready_at=t_ready, started_at=900.0)
            stop_calls = []

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.stop = fake_stop
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=t_ready)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()

                # Model sync progress at t=1500 resets countdown past t=1700 (>600s since ready_at).
                main._touch_runtime_activity(at=1500.0, sync=True)
                self.assertFalse(await main.idle_watchdog_tick(now=1700.0))
                self.assertEqual(stop_calls, [])

                # Comfy proxy request at t=1950 extends countdown past t=2200.
                main._touch_runtime_activity(at=1950.0)
                self.assertFalse(await main.idle_watchdog_tick(now=2200.0))
                self.assertEqual(stop_calls, [])

                # Once 10m pass after t=1950, watchdog triggers.
                self.assertTrue(await main.idle_watchdog_tick(now=2560.0))
                self.assertEqual(len(stop_calls), 1)

    async def test_comfy_proxy_http_route_updates_runtime_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rt = self._make_runtime(root, mode="temporary", ready_at=1000.0, started_at=900.0)

            class FakeProxyResp:
                async def proxy(self, path, request):
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"ok": True, "path": path})

            with patch.object(main, "runtime", rt), \
                 patch.object(main, "comfy", FakeProxyResp()), \
                 patch.object(main.auth, "check", return_value="test"):
                before = rt.last_activity
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                    res = await client.post("/comfy/prompt", json={"prompt": {}})
                self.assertEqual(res.status_code, 200)
                self.assertGreater(rt.last_activity, before)

    async def test_reallocating_resets_timer_ignoring_prior_session_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            rt = Runtime(lambda: {"colab_credentials_file": str(_dummy_cred(root)), "mode": "temporary"}, lambda *a: None)
            rt.path = root / "runtime.json"
            rt.handle = {
                "state": "off",
                "stopped_at": 3000.0,
                "stop_reason": "手动关机",
                "stop_trigger": "manual",
                "logs": [{"time": 3000.0, "state": "off", "message": "Colab运行时已确认释放（手动关机）"}],
            }

            with patch("backend.runtime.time.time", return_value=10000.0):
                reserved = rt.reserve_start(gpu="A100", mode="temporary")
            self.assertEqual(reserved["state"], "allocating")
            self.assertIsNone(reserved["ready_at"])
            self.assertIsNone(reserved["stopped_at"])
            self.assertIsNone(reserved["stop_reason"])

            rt.handle["endpoint"] = "ep-new"
            rt.handle["ready_at"] = 10120.0
            rt.handle["state"] = "ready"
            rt.handle["last_activity"] = 10120.0
            rt.last_activity = 10120.0
            rt._status_checked = 1e18
            rt._quota_checked = 1e18

            stop_calls = []

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.stop = fake_stop
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=2000.0)), \
                 patch.object(main, "last_sync_activity", 1500.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick(now=10130.0))
                self.assertEqual(stop_calls, [])

    async def test_race_guard_aborts_if_work_or_activity_arrives_during_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            t_ready = 1000.0
            now = 5000.0
            rt = self._make_runtime(root, mode="temporary", ready_at=t_ready, started_at=900.0)
            stop_calls = []

            orig_status = rt.status

            async def racing_status():
                res = await orig_status()
                # Simulate a newly submitted task arriving while awaiting runtime.status().
                db.save("tasks", {"id": "t-race", "status": "queued", "created_at": now})
                return res

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.status = racing_status
            rt.stop = fake_stop
            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=t_ready)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 10}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick(now=now))
                self.assertEqual(stop_calls, [])

            # Also verify require_ready=True refuses to cancel an in-flight start task.
            rt_boot = self._make_runtime(root, mode="temporary", state="bootstrapping", endpoint=None, ready_at=None, started_at=now)
            cancelled = False

            async def sleeper():
                nonlocal cancelled
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled = True
                    raise

            boot_task = asyncio.create_task(sleeper())
            rt_boot._starting_task = boot_task
            res = await rt_boot.stop(reason="test", trigger="idle_watchdog", require_ready=True)
            self.assertEqual(res["state"], "bootstrapping")
            self.assertFalse(cancelled)
            boot_task.cancel()
            await asyncio.gather(boot_task, return_exceptions=True)

    async def test_stop_and_reset_preserve_logs_and_record_trigger_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rt = self._make_runtime(root, mode="temporary", endpoint="ep-log", ready_at=1000.0, started_at=900.0)
            assigned = True

            async def fake_assignments():
                return [{"endpoint": "ep-log"}] if assigned else []

            async def fake_tun(method, path, params=None, headers=None):
                nonlocal assigned
                if method == "GET" and path.startswith("/tun/m/unassign/"):
                    return {"token": "tok-1"}
                if method == "POST" and path.startswith("/tun/m/unassign/"):
                    assigned = False
                    return {}
                return {}

            rt._assignments = fake_assignments
            rt._tun = fake_tun

            stopped = await rt.stop(reason="空闲超时自动关机（已空闲 61.0 分钟 > 阈值 60 分钟）", trigger="idle_watchdog", require_ready=True)
            self.assertEqual(stopped["state"], "off")
            self.assertEqual(stopped["stop_trigger"], "idle_watchdog")
            self.assertIn("空闲超时自动关机", stopped["stop_reason"])
            self.assertIsNotNone(stopped["stopped_at"])
            messages = [entry["message"] for entry in stopped["logs"]]
            self.assertIn("正在核对GPU可用性", messages)
            self.assertIn("正在启动ComfyUI", messages)
            self.assertIn("ComfyUI已就绪", messages)
            self.assertTrue(any("正在关闭Colab运行时" in m and "空闲超时自动关机" in m for m in messages))
            self.assertTrue(any("Colab运行时已确认释放" in m and "空闲超时自动关机" in m for m in messages))

            persisted = json.loads(rt.path.read_text())
            self.assertEqual(persisted["stop_trigger"], "idle_watchdog")
            self.assertGreaterEqual(len(persisted["logs"]), 5)

            # Reset preserves prior logs too.
            rt.handle["state"] = "error"
            rt.handle["logs"] = stopped["logs"]
            reset_res = await rt.reset()
            self.assertEqual(reset_res["stop_trigger"], "manual_reset")
            self.assertTrue(any("ComfyUI已就绪" == entry["message"] for entry in reset_res["logs"]))
            self.assertEqual(reset_res["logs"][-1]["message"], "已完成状态复位与残留清理")

    async def test_health_probe_execute_activity_false_does_not_extend_idle_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rt = self._make_runtime(root, mode="temporary", endpoint="ep-health", ready_at=1000.0, started_at=900.0)

            class FakeWS:
                def __init__(self, msg_id_box):
                    self.msg_id_box = msg_id_box
                    self.step = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def send(self, frame):
                    msg = json.loads(frame[12:].decode())
                    self.msg_id_box[0] = msg["header"]["msg_id"]

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    msg_id = self.msg_id_box[0]
                    self.step += 1
                    if self.step == 1:
                        return Runtime.pack({"header": {"msg_type": "execute_reply"}, "parent_header": {"msg_id": msg_id}, "content": {"status": "ok"}})
                    if self.step == 2:
                        return Runtime.pack({"header": {"msg_type": "status"}, "parent_header": {"msg_id": msg_id}, "content": {"execution_state": "idle"}})
                    raise StopAsyncIteration

            async def fake_conn():
                return "https://jupyter.example", "tok"

            class FakeAsyncClient:
                def __init__(self, *a, **kw):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, **kw):
                    if url.endswith("/api/kernels"):
                        return httpx.Response(200, json=[{"id": "k1"}])
                    return httpx.Response(200, json={})

            msg_id_box = ["id-1"]
            rt._connection = fake_conn
            rt._kernel_verified_id = "k1"
            rt._kernel_verified_at = time.monotonic()

            with patch("backend.runtime.httpx.AsyncClient", FakeAsyncClient), \
                 patch("backend.runtime.connect", side_effect=lambda *a, **kw: FakeWS(msg_id_box)):
                await rt.execute("print('probe')", activity=False)
                self.assertEqual(rt.last_activity, 1000.0)

                await rt.execute("print('work')", activity=True)
                self.assertGreater(rt.last_activity, 1000.0)

    async def test_legacy_ready_handle_without_ready_at_backfills_on_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "studio.sqlite3")
            rt = Runtime(lambda: {"colab_credentials_file": str(_dummy_cred(root)), "mode": "temporary"}, lambda *a: None)
            rt.path = root / "runtime.json"
            now = time.time()
            # Simulated v1.0.43 runtime.json loaded after upgrade: started 2h ago, no ready_at.
            rt.handle = {
                "state": "ready",
                "gpu": "A100",
                "mode": "temporary",
                "endpoint": "ep-legacy",
                "started_at": now - 7200.0,
            }
            rt._status_checked = 1e18
            rt._quota_checked = 1e18
            stop_calls = []

            async def fake_stop(**kwargs):
                stop_calls.append(kwargs)
                return rt._public()

            rt.stop = fake_stop
            st = await rt.status()
            self.assertIsNotNone(st["ready_at"])
            self.assertGreaterEqual(st["ready_at"], now - 5.0)

            with patch.object(main, "db", db), \
                 patch.object(main, "runtime", rt), \
                 patch.object(main, "tasks", SimpleNamespace(last_activity=now - 10800.0)), \
                 patch.object(main, "last_sync_activity", 0.0), \
                 patch.object(main, "settings", lambda: {"mode": "temporary", "idle_minutes": 60}):
                main.sync_locks.clear()
                main.sync_tasks.clear()
                self.assertFalse(await main.idle_watchdog_tick())
                self.assertEqual(stop_calls, [])
