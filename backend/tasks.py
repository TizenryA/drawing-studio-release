"""Persistent single-worker queue for native ComfyUI.

Tasks(db, runtime, comfy, drive, workflows, emit, *, gallery=None,
      sync_model=None, poll_interval=1.0)

DB methods are synchronous list/get/save/delete(kind, ...). emit(event, row)
may be synchronous or async. sync_model(model_record, on_progress=None) may be sync or async;
it must finish actual transfer/verification, never just change registry status.
Without callback, comfy.sync_model(row, drive) is used when available. Models
already listed by real /object_info do not need syncing. Unknown files absent
from that list fail explicitly. No GPU is allocated by this worker.

runtime.status() -> {state: 'ready', instance_id/runtime_id/id: ...}.
comfy.request(method,path,json=None,data=None,files=None) -> JSON or bytes.
Uploads use the standard multipart files={'image': (filename, bytes, mime)}.
Gallery.archive(task_id, bytes, filename, params, parent_id=...) returns a
record with archive_status='saved' (or retry on failure). An archive failure
keeps results and Comfy output references and marks task failed/retryable.

submit() snapshots normalized params, model records and entire API graph.
start() restores queued work, resumes known prompt IDs (no duplicate submits),
and marks interrupted pre-submission work failed/retryable. stop() preserves
running prompt IDs; it does not interrupt somebody else's ComfyUI prompt.
Download progress is flushed GPU file bytes; generating has no estimated percentage.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import inspect
import hashlib
import json
import logging
import math
import mimetypes
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

from .database import task_summary, slim_task_result
from .runtime import AdapterError
from .workflows import WorkflowError, now, adapt_seedvr2, adapt_save_video, MODEL_LOADERS, combo_choices
from .gallery import normalize_project_name, record_project

log = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


async def _await(value):
    return await value if inspect.isawaitable(value) else value


def _finite_num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TaskCancelled(Exception):
    pass


class Tasks:
    def __init__(self, db, runtime, comfy, drive, workflows, emit, *, gallery=None, sync_model=None, poll_interval=1.25):
        self.db, self.runtime, self.comfy = db, runtime, comfy
        self.drive, self.workflows, self.emit = drive, workflows, emit
        self.gallery, self.sync_model = gallery, sync_model
        self.poll_interval = poll_interval
        self.worker_ports = (8188, 8189)
        self.workers = {}
        self.active_tasks = {p: None for p in self.worker_ports}
        self.active_heavy_port = None
        self.worker_signatures = {p: None for p in self.worker_ports}
        self.pending_ids = []
        self._cond = asyncio.Condition()
        self._ports_cache = (0.0, [8188])
        self._object_info_cache = {}
        self._object_info_ttl = 300.0
        self.cancelled = set()
        self.last_activity = time.time()
        self._stopping = False

    @staticmethod
    def _task_media_kind(params, prompt):
        p = params or {}
        if p.get("mode") == "video_seedvr2" or (p.get("workflow_id") or p.get("workflow")) == "video-seedvr2":
            return "video"
        video_nodes = {"MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo", "CreateVideo", "SaveVideo", "VHS_VideoCombine"}
        if any(isinstance(n, dict) and n.get("class_type") in video_nodes for n in (prompt or {}).values()):
            return "video"
        return "image"

    @property
    def current_id(self):
        for p in self.worker_ports:
            tid = self.active_tasks.get(p)
            if tid:
                return tid
        return None

    @staticmethod
    def _is_heavy_task(row):
        if (row or {}).get("media_kind") == "video":
            return True
        return Tasks._needs_gpu_free(row or {})

    @staticmethod
    def _task_signature(row):
        p = (row or {}).get("params") or {}
        wf = p.get("workflow_id") or p.get("workflow")
        model = p.get("model") or p.get("checkpoint")
        loras = tuple(sorted(
            str(l.get("name") or l.get("id") or "")
            for l in (p.get("loras") or []) if isinstance(l, dict)
        ))
        return (wf, model, loras)

    def invalidate_object_info(self, port=None):
        """Drop cached ComfyUI /object_info schemas for one port or all ports."""
        if port is None:
            self._object_info_cache.clear()
            return
        target = int(port)
        for key in [k for k in self._object_info_cache if k[1] == target]:
            self._object_info_cache.pop(key, None)

    def _desired_workers(self):
        cfg = self.db.get("settings", "main") or {}
        try:
            n = int(cfg.get("comfy_workers", 1))
        except (TypeError, ValueError):
            n = 1
        return max(1, min(len(self.worker_ports), n))

    async def _refresh_active_ports(self, force=False):
        now_m = time.monotonic()
        ts, cached = self._ports_cache
        if not force and now_m - ts < 15:
            return cached
        ports = [8188]
        fn = getattr(self.comfy, "active_ports", None)
        if callable(fn):
            try:
                res = await fn(self.worker_ports)
                if isinstance(res, (list, tuple)) and res:
                    ports = [int(p) for p in res]
            except Exception:
                pass
        self._ports_cache = (time.monotonic(), ports)
        return ports

    async def _comfy_req(self, port, method, path, **kwargs):
        try:
            return await self.comfy.request(method, path, port=port, **kwargs)
        except TypeError as exc:
            if "port" not in str(exc):
                raise
            return await self.comfy.request(method, path, **kwargs)

    async def _enqueue(self, ident):
        async with self._cond:
            if ident not in self.pending_ids:
                self.pending_ids.append(ident)
            self._cond.notify_all()

    async def _sample_quota(self, *, force=False):
        quota_fn = getattr(self.runtime, "quota", None)
        if not callable(quota_fn):
            return None
        try:
            try:
                coro = quota_fn(force=force)
            except TypeError:
                coro = quota_fn()
            return await asyncio.wait_for(coro, timeout=8)
        except Exception:
            return None

    async def _finish_metrics(self, row, *, finished_at=None):
        finished = finished_at or now()
        patch = {"finished_at": finished}
        start_ep = _parse_iso(row.get("started_at"))
        fin_ep = _parse_iso(finished)
        total_seconds = round(max(0.0, fin_ep - start_ep), 2) if start_ep is not None and fin_ep is not None else None
        if total_seconds is not None:
            patch["total_seconds"] = total_seconds
        sync_ep = _parse_iso(row.get("sync_started_at"))
        gen_ep = _parse_iso(row.get("generating_started_at"))
        arch_ep = _parse_iso(row.get("archiving_started_at"))
        if row.get("sync_seconds") is None and sync_ep is not None and gen_ep is not None:
            patch["sync_seconds"] = round(max(0.0, gen_ep - sync_ep), 2)
        if row.get("gen_seconds") is None and gen_ep is not None:
            end_gen_ep = arch_ep if arch_ep is not None else fin_ep
            if end_gen_ep is not None:
                patch["gen_seconds"] = round(max(0.0, end_gen_ep - gen_ep), 2)
        q_end = await self._sample_quota(force=True)
        cu_end = _finite_num((q_end or {}).get("balance"))
        rate = _finite_num((q_end or {}).get("consumption_rate"))
        if rate is None:
            rate = _finite_num(row.get("cu_rate_hourly"))
        if cu_end is not None:
            patch["cu_balance_end"] = cu_end
        if rate is not None:
            patch["cu_rate_hourly"] = rate
        if total_seconds is not None and rate is not None and rate > 0:
            patch["cu_estimated"] = round((total_seconds / 3600.0) * rate, 3)
        cu_start = _finite_num(row.get("cu_balance_start"))
        if cu_start is not None and cu_end is not None:
            patch["cu_actual_delta"] = max(0.0, round(cu_start - cu_end, 3))
        return patch

    async def _save(self, row, **changes):
        latest = self.db.get("tasks", row["id"])
        if latest and latest.get("cancel_requested"):
            changes["cancel_requested"] = True
        row = self.db.save("tasks", {**row, **changes})
        self.last_activity = time.time()
        try:
            await _await(self.emit("task", task_summary(row)))
        except Exception:
            log.exception("Task event subscriber failed")
        return row

    async def submit(self, params):
        if not isinstance(params, dict):
            raise WorkflowError("任务参数应为对象")
        p = copy.deepcopy(params)
        extras = p.pop("comfy", {}) or {}
        p.update(extras)
        models = self.db.list("models")
        reference = p.get("model") or p.get("checkpoint")
        selected = next((m for m in models if reference in (m.get("id"), m.get("name"), m.get("filename"), m.get("comfy_name"))), None) if reference else None
        config = self.db.get("settings", "main") or {}
        defaults = copy.deepcopy(config.get("generation_defaults", {}))
        defaults.update((selected or {}).get("defaults", {}))
        defaults.update(p)
        if not defaults.get("vae") and (selected or {}).get("default_vae"):
            defaults["vae"] = selected["default_vae"]
        if p.get("workflow"):
            defaults["workflow_id"] = p["workflow"]
        elif "mode" in p and defaults.get("workflow_id"):
            old = self.workflows.get(defaults["workflow_id"])
            if old and old.get("builtin") and old.get("mode") != p["mode"]:
                defaults.pop("workflow_id", None)
        if defaults.get("input_image") and not defaults.get("image") and not str(defaults["input_image"]).startswith(("http:", "https:", "/api/")):
            defaults["image"] = defaults["input_image"]
        source_video = None
        source_video_id = defaults.get("source_video_id")
        if source_video_id:
            source_video = self.db.get("gallery", source_video_id)
            if not source_video:
                raise WorkflowError("源视频不在图库中")
            if source_video.get("media_type") and source_video.get("media_type") != "video":
                raise WorkflowError("所选图库作品不是视频")
            defaults["source_video"] = source_video.get("filename") or Path(source_video.get("local_path", "")).name
            defaults.setdefault("parent_id", source_video_id)
        parent = None
        parent_id = defaults.get("parent_id") or defaults.get("image_id")
        if parent_id and parent_id != source_video_id:
            parent = self.db.get("gallery", parent_id)
            if not parent:
                raise WorkflowError("原图不在图库中")
            defaults["image"] = parent.get("filename") or Path(parent.get("local_path", "")).name
        if "project" in p or "album" in p:
            raw_project = p.get("project") if "project" in p else p.get("album")
        elif source_video is not None:
            raw_project = record_project(source_video)
        elif parent is not None:
            raw_project = record_project(parent)
        else:
            raw_project = defaults.get("project") if defaults.get("project") is not None else defaults.get("album")
        try:
            project_name = normalize_project_name(raw_project)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        defaults["project"] = project_name
        defaults.pop("album", None)
        built = self.workflows.build(defaults, models=models)
        # JSON round-trip guarantees DB-safe snapshot and isolates caller mutation.
        snapshot = json.loads(json.dumps(built["params"]))
        row = {"id": uuid.uuid4().hex, "status": "queued", "progress": 0,
               "params": snapshot, "media_kind": self._task_media_kind(snapshot, built["prompt"]),
               "results": [], "error": None, "created_at": now(),
               "prompt": built["prompt"], "workflow_hash": built["workflow_hash"],
               "required_models": built["required_models"], "output_nodes": built["output_nodes"],
               "prompt_id": None, "retryable": False, "stage": "queued",
               "current_node": None, "node_type": None, "node_label": None,
               "execution_phase": None, "sampling_step": None, "total_steps": None}
        row = await self._save(row)
        await self._enqueue(row["id"])
        return row

    async def start(self):
        if any(w and not w.done() for w in self.workers.values()):
            return
        self._stopping = False
        async with self._cond:
            self.pending_ids = []
            for row in sorted(self.db.list("tasks"), key=lambda r: r.get("created_at", "")):
                if row.get("status") == "queued":
                    self.pending_ids.append(row["id"])
                elif row.get("status") == "running":
                    if row.get("cancel_requested"):
                        await self._save(row, status="cancelled", stage="cancelled", finished_at=now(), retryable=False)
                    elif row.get("prompt_id"):
                        self.pending_ids.append(row["id"])
                    else:
                        await self._save(row, status="failed", stage="interrupted", retryable=True,
                                         error="服务重启中断了提交；未确认的请求不会自动重复执行，请重试", finished_at=now())
            self._cond.notify_all()
        for port in self.worker_ports:
            self.workers[port] = asyncio.create_task(self._run_worker(port), name=f"drawing-studio-worker-{port}")

    async def stop(self):
        self._stopping = True
        async with self._cond:
            self._cond.notify_all()
        for w in tuple(self.workers.values()):
            if w:
                w.cancel()
        for w in tuple(self.workers.values()):
            if w:
                try:
                    await w
                except asyncio.CancelledError:
                    pass
        self.workers.clear()

    async def cancel(self, ident):
        row = self.db.get("tasks", ident)
        if not row:
            raise KeyError("任务不存在")
        if row["status"] in TERMINAL:
            return row
        self.cancelled.add(ident)
        row = await self._save(row, cancel_requested=True)
        if row.get("prompt_id"):
            try:
                await self._cancel_remote(row["prompt_id"], port=row.get("worker_port") or 8188)
            except Exception as exc:
                row = await self._save(row, cancel_error=str(exc))
        if row["status"] == "queued":
            row = await self._save(row, status="cancelled", stage="cancelled", finished_at=now())
            async with self._cond:
                self._cond.notify_all()
        return row

    async def _cancel_remote(self, prompt_id, port=8188):
        queue = await self._comfy_req(port, "GET", "/queue")
        # Native /interrupt is global per instance. Only interrupt when this exact prompt runs.
        if any(str(item[1]) == prompt_id for item in queue.get("queue_running", [])):
            await self._comfy_req(port, "POST", "/interrupt", json={})
        elif any(str(item[1]) == prompt_id for item in queue.get("queue_pending", [])):
            await self._comfy_req(port, "POST", "/queue", json={"delete": [prompt_id]})

    def _check_cancel(self, ident):
        row = self.db.get("tasks", ident)
        if ident in self.cancelled or (row and row.get("cancel_requested")):
            raise TaskCancelled()

    async def _acquire_next(self, port):
        while not self._stopping:
            if port != 8188:
                desired = self._desired_workers()
                ports = await self._refresh_active_ports()
                if desired < 2 or port not in ports:
                    async with self._cond:
                        if self._stopping:
                            return None, False
                        try:
                            await asyncio.wait_for(self._cond.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                    continue
            async with self._cond:
                if self._stopping:
                    return None, False
                cleaned = []
                rows_map = {}
                for tid in self.pending_ids:
                    r = self.db.get("tasks", tid)
                    if r and r.get("status") not in TERMINAL:
                        cleaned.append(tid)
                        rows_map[tid] = r
                self.pending_ids = cleaned

                if self.active_heavy_port is not None:
                    await self._cond.wait()
                    continue

                chosen_idx = None
                is_heavy = False

                for idx, tid in enumerate(self.pending_ids):
                    r = rows_map[tid]
                    if r.get("prompt_id") and int(r.get("worker_port") or 8188) == port:
                        chosen_idx = idx
                        is_heavy = self._is_heavy_task(r)
                        break

                if chosen_idx is None:
                    my_sig = self.worker_signatures.get(port)
                    normal_candidates = []
                    for idx, tid in enumerate(self.pending_ids):
                        r = rows_map[tid]
                        if r.get("prompt_id"):
                            continue
                        if self._is_heavy_task(r):
                            if not normal_candidates:
                                if port == 8188 and all(v is None for p, v in self.active_tasks.items() if p != 8188):
                                    chosen_idx = idx
                                    is_heavy = True
                            break
                        normal_candidates.append((idx, r))
                        if len(normal_candidates) >= 6:
                            break

                    if chosen_idx is None and normal_candidates:
                        for idx, r in normal_candidates:
                            if my_sig is not None and self._task_signature(r) == my_sig:
                                chosen_idx = idx
                                break
                        if chosen_idx is None:
                            chosen_idx = normal_candidates[0][0]

                if chosen_idx is not None:
                    ident = self.pending_ids.pop(chosen_idx)
                    self.active_tasks[port] = ident
                    if is_heavy:
                        self.active_heavy_port = port
                    return ident, is_heavy

                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=5.0 if port != 8188 else 30.0)
                except asyncio.TimeoutError:
                    pass
        return None, False

    async def _run_worker(self, port):
        while not self._stopping:
            ident, is_heavy = await self._acquire_next(port)
            if not ident:
                continue
            try:
                if is_heavy:
                    ports = await self._refresh_active_ports(force=True)
                    for other_p in ports:
                        if other_p != port:
                            await self._free_gpu_memory(port=other_p)
                row = self.db.get("tasks", ident)
                if row and row["status"] not in TERMINAL:
                    self.worker_signatures[port] = self._task_signature(row)
                    await self._execute(row, port=port)
            except TaskCancelled:
                row = self.db.get("tasks", ident)
                if row:
                    if row.get("prompt_id"):
                        try:
                            await self._cancel_remote(row["prompt_id"], port=port)
                        except Exception as exc:
                            row = await self._save(row, cancel_error=str(exc))
                    metrics = await self._finish_metrics(row)
                    await self._save(row, status="cancelled", stage="cancelled",
                                     download_stage="cancelled" if row.get("stage") == "syncing" else row.get("download_stage"),
                                     retryable=False, **metrics)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                row = self.db.get("tasks", ident)
                err_msg = str(exc) or type(exc).__name__
                if row:
                    metrics = await self._finish_metrics(row)
                    await self._save(row, status="failed", stage="failed", error=err_msg,
                                     error_details=getattr(exc, "details", {}), retryable=True, **metrics)
                log.warning("Task %s (port %s) failed: %s", ident, port, err_msg)
            finally:
                async with self._cond:
                    self.active_tasks[port] = None
                    if self.active_heavy_port == port:
                        self.active_heavy_port = None
                    self.cancelled.discard(ident)
                    self._cond.notify_all()

    async def _ready(self):
        status = await self.runtime.status()
        if status.get("state", status.get("status")) != "ready":
            raise RuntimeError("GPU/ComfyUI 未就绪，请先开机: " + str(status.get("error") or status.get("state") or status.get("status")))
        return status

    @staticmethod
    def _instance(status):
        return status.get("instance_id") or status.get("runtime_id") or status.get("endpoint") or status.get("id")

    @staticmethod
    def _loader_choices(prompt, info):
        references = {}
        for node in (prompt or {}).values():
            cls = node["class_type"]
            for field in MODEL_LOADERS.get(cls, {}):
                value = node["inputs"].get(field)
                if isinstance(value, str):
                    schema = info.get(cls, {}).get("input", {})
                    spec = schema.get("required", {}).get(field, schema.get("optional", {}).get(field))
                    references.setdefault(value, []).append(combo_choices(spec))
        return references

    def _missing_models(self, row, info):
        """Return required_models not yet present in exact loader node choices."""
        references = self._loader_choices(row.get("prompt", {}), info)
        missing = []
        for model in row.get("required_models", []):
            name = model["comfy_name"]
            choices = references.get(name, [])
            if choices and all(options is not None and name in options for options in choices):
                continue
            missing.append(model)
        return missing

    async def _get_object_info(self, instance, port, classes, *, force=False):
        """Return cached /object_info node schemas for (instance, port) when valid, else fetch."""
        key = (instance or "", int(port or 8188))
        now_m = time.monotonic()
        entry = self._object_info_cache.get(key)
        fresh = entry is not None and (now_m - entry["ts"]) < self._object_info_ttl
        needed = [str(c) for c in (classes or []) if c]
        if not force and fresh and all(c in entry["nodes"] for c in needed):
            return entry["nodes"], True

        if hasattr(self.comfy, "object_info_for"):
            to_fetch = needed if (force or not fresh) else [c for c in needed if c not in entry["nodes"]]
            fetched = await self.comfy.object_info_for(to_fetch, port=port)
            merged = {} if (force or not fresh) else dict(entry["nodes"])
            if isinstance(fetched, dict):
                merged.update(fetched)
        else:
            fetched = await self._comfy_req(port, "GET", "/object_info")
            merged = dict(fetched) if isinstance(fetched, dict) else {}

        self._object_info_cache[key] = {"ts": time.monotonic(), "nodes": merged}
        return merged, False

    async def _sync(self, row, info):
        # Check each exact loader field on this runtime, never registry status
        # or an unrelated node's enum with the same filename.
        for model in self._missing_models(row, info):
            name = model["comfy_name"]
            self._check_cancel(row["id"])
            async def report(progress):
                self._check_cancel(row["id"])
                current = self.db.get("tasks", row["id"])
                if not current or current.get("status") in TERMINAL:
                    raise TaskCancelled()
                fields = {k: progress.get(k) for k in ("filename", "downloaded_bytes", "total_bytes", "download_progress")}
                await self._save(current, stage="syncing", progress=None, model_id=model.get("id"),
                                 download_stage=progress.get("stage"), **fields)

            await report({"stage": "preparing", "filename": model.get("filename") or name,
                          "downloaded_bytes": 0, "total_bytes": int(model.get("size") or 0),
                          "download_progress": None})
            async def transfer():
                if self.sync_model:
                    # Keep compatibility with existing single-argument integrations.
                    parameters = inspect.signature(self.sync_model).parameters
                    supports_progress = "on_progress" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
                    options = {"on_progress": report} if supports_progress else {}
                    return await _await(self.sync_model(copy.deepcopy(model), **options))
                if hasattr(self.comfy, "sync_model"):
                    return await self.comfy.sync_model(copy.deepcopy(model), self.drive, on_progress=report)
                raise RuntimeError(f"模型 {name} 未在 GPU；尚未接入 sync_model 回调，请完成 Drive 下载及 GPU 上传")

            operation = asyncio.create_task(transfer())
            try:
                while not operation.done():
                    self._check_cancel(row["id"])
                    await asyncio.wait({operation}, timeout=self.poll_interval)
                result = await operation
                self._check_cancel(row["id"])
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            if result is False or result is None:
                raise RuntimeError(f"模型同步未返回成功结果: {name}")
        return self.db.get("tasks", row["id"]) or row

    @staticmethod
    def _read_video_for_h3(local: Path, is_h3_continue: bool) -> bytes:
        """Read video bytes; if H3 continue source is not 24fps (e.g. 48/60fps RIFE), conform to 24fps."""
        if not is_h3_continue:
            return local.read_bytes()
        import subprocess
        import tempfile
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate", "-of", "default=nk=1:nw=1", str(local)],
                capture_output=True, text=True, timeout=15, check=True,
            )
            rate = (probe.stdout or "").strip().splitlines()[0]
            if "/" in rate:
                num, den = rate.split("/", 1)
                fps = float(num) / float(den) if float(den) else 24.0
            else:
                fps = float(rate)
        except Exception:
            return local.read_bytes()
        if abs(fps - 24.0) <= 0.05:
            return local.read_bytes()
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="h3-24fps-", suffix=".mp4", delete=False) as tf:
                tmp_path = Path(tf.name)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(local),
                 "-vf", "fps=24", "-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
                 "-c:a", "copy", "-movflags", "+faststart", str(tmp_path)],
                capture_output=True, timeout=300, check=True,
            )
            return tmp_path.read_bytes()
        except Exception:
            return local.read_bytes()
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    async def _upload_inputs(self, row, port=8188):
        prompt = copy.deepcopy(row["prompt"])
        replacements = {}
        root = self.workflows.data_dir
        is_h3_continue = any(n["class_type"] == "MiniMaxH3ImageToVideo" for n in prompt.values())
        for node in prompt.values():
            cls = node["class_type"]
            if cls in ("LoadImage", "LoadImageMask"):
                field = "image"
            elif cls == "LoadVideo":
                field = "file"
            else:
                continue
            name = node["inputs"].get(field)
            if not isinstance(name, str):
                continue
            cache_key = f"{cls}:{name}"
            if cache_key in replacements:
                node["inputs"][field] = replacements[cache_key]
                continue
            local = root / "uploads" / Path(name).name
            gallery_record = None
            if cls == "LoadVideo":
                source_video_id = row["params"].get("source_video_id")
                if source_video_id and not local.is_file():
                    gallery_record = self.db.get("gallery", source_video_id)
                    if gallery_record and gallery_record.get("local_path"):
                        local = Path(gallery_record["local_path"])
                if not local.is_file():
                    candidate = root / "images" / Path(name).name
                    if candidate.is_file():
                        local = candidate
            else:
                parent_id = row["params"].get("parent_id") or row["params"].get("image_id")
                if parent_id and name == row["params"].get("image") and not local.is_file():
                    gallery_record = self.db.get("gallery", parent_id)
                    if gallery_record and gallery_record.get("local_path"):
                        local = Path(gallery_record["local_path"])
            if not local.is_file() and gallery_record and gallery_record.get("drive_file_id") and self.drive:
                local.parent.mkdir(parents=True, exist_ok=True)
                await self.drive.download(gallery_record["drive_file_id"], local)
            if not local.is_file():
                # The caller may supply a filename already present in Comfy input.
                continue
            if cls == "LoadVideo":
                content = await asyncio.to_thread(self._read_video_for_h3, local, is_h3_continue)
            else:
                content = await asyncio.to_thread(local.read_bytes)
            remote_name = f"drawing-{row['id']}-{local.name}"
            default_mime = "video/mp4" if cls == "LoadVideo" else "image/png"
            response = await self._comfy_req(port, "POST", "/upload/image", data={"type": "input", "overwrite": "true"}, files={"image": (remote_name, content, mimetypes.guess_type(local.name)[0] or default_mime)})
            if not isinstance(response, dict) or not response.get("name"):
                raise RuntimeError("ComfyUI 上传输入素材未返回文件名")
            uploaded = "/".join(filter(None, (response.get("subfolder", ""), response["name"])))
            replacements[cache_key] = uploaded
            node["inputs"][field] = uploaded
        return prompt

    @staticmethod
    def _node_phase(node_type):
        kind = node_type.lower()
        if 'seedvr2' in kind:
            return 'model_loading' if 'loader' in kind or node_type in {'SeedVR2LoadDiTModel', 'SeedVR2LoadVAEModel'} else 'upscaling'
        if node_type in {'KSampler', 'KSamplerAdvanced', 'SamplerCustom', 'SamplerCustomAdvanced'}:
            return 'sampling'
        if 'vaedecode' in kind: return 'vae_decoding'
        if 'vaeencode' in kind: return 'vae_encoding'
        if 'upscale' in kind: return 'upscaling'
        if kind.startswith(('saveimage', 'saveanimated', 'savevideo')): return 'saving'
        if 'loader' in kind: return 'model_loading'
        if 'textencode' in kind: return 'conditioning'
        return 'executing'

    async def _apply_execution_events(self, ident, events):
        if not events:
            return
        row = self.db.get('tasks', ident)
        if not row or row.get('status') in TERMINAL or row.get('stage') != 'generating':
            return
        cur = dict(row)
        dirty = {}
        graph = row.get('submitted_prompt', row.get('prompt', {}))
        for event in events:
            data = event.get('data')
            if not isinstance(data, dict) or str(data.get('prompt_id')) != cur.get('prompt_id'):
                continue
            kind = event.get('type')
            if kind not in {'executing', 'progress', 'execution_start', 'execution_success', 'execution_error', 'execution_interrupted'}:
                continue
            fields = {}
            if kind == 'executing' or (kind == 'progress' and data.get('node') is not None):
                node_id = str(data['node']) if data.get('node') is not None else None
                node = graph.get(node_id, {})
                node_type = node.get('class_type', '')
                fields.update(
                    current_node=node_id,
                    node_type=node_type or None,
                    node_label=(node.get('_meta') or {}).get('title') or node_type or node_id,
                    execution_phase=self._node_phase(node_type) if node_id else None,
                )
                if node_id != cur.get('current_node') or kind == 'executing':
                    fields.update(sampling_step=None, total_steps=None)
            if kind == 'progress':
                phase = fields.get('execution_phase', cur.get('execution_phase'))
                if phase != 'sampling':
                    fields.update(sampling_step=None, total_steps=None)
                else:
                    value, maximum = data.get('value'), data.get('max')
                    if type(value) is int and type(maximum) is int and 0 <= value <= maximum and maximum > 0:
                        fields.update(sampling_step=value, total_steps=maximum)
            if kind in {'execution_start', 'execution_success', 'execution_error', 'execution_interrupted'}:
                fields.update(current_node=None, node_type=None, node_label=None, execution_phase=None, sampling_step=None, total_steps=None)
            if fields:
                cur.update(fields)
                dirty.update(fields)
        changed = {k: v for k, v in dirty.items() if row.get(k) != v}
        if changed:
            await self._save(row, **changed)

    async def _execution_event(self, ident, event):
        await self._apply_execution_events(ident, [event])

    async def _open_execution(self, ident, port=8188):
        if not hasattr(self.comfy, 'open_execution'): return
        try:
            try:
                await self.comfy.open_execution(ident, port=port)
            except TypeError as exc:
                if "port" not in str(exc): raise
                await self.comfy.open_execution(ident)
        except Exception: log.warning('Execution subscription unavailable; history polling continues')

    async def _poll_execution(self, ident, port=8188):
        if not hasattr(self.comfy, 'poll_execution'): return
        try: events = await self.comfy.poll_execution(ident)
        except Exception:
            await self._open_execution(ident, port=port)
            current = self.db.get('tasks', ident)
            if current and current.get('stage') == 'generating':
                await self._save(current, current_node=None, node_type=None, node_label=None,
                                 execution_phase=None, sampling_step=None, total_steps=None)
            return
        await self._apply_execution_events(ident, events)

    async def _execute(self, row, port=8188):
        try: await self._execute_subscribed(row, port=port)
        finally:
            if hasattr(self.comfy, 'close_execution'):
                try:
                    await self.comfy.close_execution(row['id'])
                except Exception:
                    log.warning('Task execution subscription cleanup failed; reader lease will expire')

    @staticmethod
    def _needs_gpu_free(row):
        params = row.get("params") or {}
        if params.get("free_gpu_before_run"):
            return True
        classes = {n.get("class_type") for n in (row.get("prompt") or {}).values() if isinstance(n, dict)}
        return "SeedVR2VideoUpscaler" in classes and "LoadVideo" in classes

    async def _free_gpu_memory(self, port=8188):
        try:
            await self._comfy_req(port, "POST", "/free", json={"unload_models": True, "free_memory": True})
        except Exception as exc:
            log.warning("ComfyUI /free before task failed (continuing): %s", exc)

    async def _execute_subscribed(self, row, port=8188):
        ident = row["id"]
        self._check_cancel(ident)
        status = await self._ready()
        instance = self._instance(status)
        if row.get("instance_id") and instance and row["instance_id"] != instance:
            raise RuntimeError("GPU 实例已更换，原任务中断；请重试")
        if not row.get("prompt_id"):
            started = now()
            q_start = await self._sample_quota(force=False)
            start_fields = {
                "status": "running", "stage": "preparing", "started_at": started,
                "instance_id": instance, "worker_port": port, "error": None,
            }
            if status.get("gpu") or status.get("actual_gpu"):
                start_fields["gpu"] = status.get("actual_gpu") or status.get("gpu")
            if status.get("actual_gpu"):
                start_fields["actual_gpu"] = status["actual_gpu"]
            cu_s = _finite_num((q_start or {}).get("balance"))
            cu_r = _finite_num((q_start or {}).get("consumption_rate"))
            if cu_s is not None:
                start_fields["cu_balance_start"] = cu_s
            if cu_r is not None:
                start_fields["cu_rate_hourly"] = cu_r
            row = await self._save(row, **start_fields)

            base_prompt = row["prompt"]
            needed_classes = sorted({n["class_type"] for n in base_prompt.values()})
            info, from_cache = await self._get_object_info(instance, port, needed_classes, force=False)
            missing = sorted(set(needed_classes) - set(info))
            if missing and from_cache:
                info, from_cache = await self._get_object_info(instance, port, needed_classes, force=True)
                missing = sorted(set(needed_classes) - set(info))
            if missing:
                raise WorkflowError("ComfyUI 缺少节点: " + ", ".join(missing) + "；请安装对应扩展或导入兼容的 API 工作流", details={"missing_nodes": missing})

            missing_models = self._missing_models(row, info)
            if missing_models and from_cache:
                info, from_cache = await self._get_object_info(instance, port, needed_classes, force=True)
                missing_models = self._missing_models(row, info)

            if missing_models:
                sync_started = now()
                row = await self._save(row, stage="syncing", sync_started_at=sync_started)
                row = await self._sync(row, info)
                sync_ep = _parse_iso(sync_started)
                sync_done_ep = _parse_iso(now())
                sync_sec = round(max(0.0, sync_done_ep - sync_ep), 2) if sync_ep is not None and sync_done_ep is not None else 0.0
                self.invalidate_object_info(port=port)
                info, from_cache = await self._get_object_info(instance, port, needed_classes, force=True)
            else:
                sync_sec = 0.0

            def _adapt_and_validate(raw_prompt, schema_info, params):
                adapted = adapt_seedvr2(raw_prompt, schema_info)
                adapted = adapt_save_video(adapted, schema_info, crf=(params or {}).get("video_crf", 15))
                errs = []
                for node_id, node in adapted.items():
                    metadata = schema_info.get(node["class_type"])
                    if not isinstance(metadata, dict):
                        errs.append(f"缺少节点 {node['class_type']}")
                        continue
                    for field, spec in metadata.get("input", {}).get("required", {}).items():
                        if field not in node["inputs"]:
                            errs.append(f"{node_id}.{field} 缺少必填输入")
                    for group in ("required", "optional"):
                        for field, spec in metadata.get("input", {}).get(group, {}).items():
                            value = node["inputs"].get(field)
                            if (node["class_type"] in ("LoadImage", "LoadImageMask") and field == "image") or (node["class_type"] == "LoadVideo" and field == "file"):
                                continue  # local input is uploaded before native validation
                            choices = combo_choices(spec)
                            if choices is not None and value is not None and not isinstance(value, list) and value not in choices:
                                errs.append(f"{node_id}.{field} 不在 ComfyUI 可选项中: {value}")
                return adapted, errs

            adapted_prompt, errors = _adapt_and_validate(base_prompt, info, row.get("params"))
            if errors and from_cache:
                info, from_cache = await self._get_object_info(instance, port, needed_classes, force=True)
                adapted_prompt, errors = _adapt_and_validate(base_prompt, info, row.get("params"))
            if errors:
                raise WorkflowError("；".join(errors), details={"invalid_inputs": errors})

            work_row = {**row, "prompt": adapted_prompt}
            prompt = await self._upload_inputs(work_row, port=port)
            self._check_cancel(ident)
            if self._needs_gpu_free(work_row):
                await self._free_gpu_memory(port=port)
                self._check_cancel(ident)
            row = await self._save(row, stage="submitting", download_stage=None, submitted_prompt=prompt,
                                   submitted_workflow_hash=hashlib.sha256(json.dumps(prompt, sort_keys=True).encode()).hexdigest())
            await self._open_execution(ident, port=port)
            try:
                response = await self._comfy_req(port, "POST", "/prompt", json={"prompt": prompt, "client_id": ident, "extra_data": {"extra_pnginfo": {"drawing_studio": row["params"]}}})
            except AdapterError as exc:
                if from_cache and "HTTP 400" in str(exc):
                    self.invalidate_object_info(port=port)
                raise
            if not isinstance(response, dict) or not response.get("prompt_id"):
                raise RuntimeError("ComfyUI 拒绝工作流: " + json.dumps(response, ensure_ascii=False))
            gen_started = now()
            row = await self._save(row, prompt_id=str(response["prompt_id"]), stage="generating", progress=None,
                                   generating_started_at=gen_started, sync_seconds=sync_sec)
        else:
            row = await self._save(row, worker_port=port)
            await self._open_execution(ident, port=port)
        history = await self._wait(row, port=port)
        self._check_cancel(ident)
        latest = self.db.get("tasks", ident) or row
        arch_started = now()
        gen_start_ep = _parse_iso(latest.get("generating_started_at") or latest.get("started_at"))
        arch_ep = _parse_iso(arch_started)
        gen_sec = round(max(0.0, arch_ep - gen_start_ep), 2) if gen_start_ep is not None and arch_ep is not None else None
        row = await self._save(latest, stage="archiving", progress=0.95, output_refs=history.get("outputs", {}),
                               archiving_started_at=arch_started, **({"gen_seconds": gen_sec} if gen_sec is not None else {}),
                               current_node=None, node_type=None, node_label=None, execution_phase=None,
                               sampling_step=None, total_steps=None)
        await self._archive(row, history, port=port)

    async def _wait(self, row, port=8188):
        ident, prompt_id = row["id"], row["prompt_id"]
        use_combined = hasattr(self.comfy, "poll_task_state")
        poll_failures = 0
        first_failure_at = None
        while True:
            self._check_cancel(ident)
            try:
                if use_combined:
                    state_bundle = await self.comfy.poll_task_state(ident, prompt_id, port=port)
                    events = state_bundle.get("events") or []
                    if events:
                        await self._apply_execution_events(ident, events)
                    history = state_bundle.get("history") or {}
                    item = history.get(prompt_id)
                    if item:
                        state = item.get("status", {})
                        if state.get("status_str") == "error":
                            messages = state.get("messages", [])
                            raise RuntimeError("ComfyUI 执行失败: " + json.dumps(messages, ensure_ascii=False))
                        if state.get("completed"):
                            return item
                    queue = state_bundle.get("queue") or {}
                    active = queue.get("queue_running", []) + queue.get("queue_pending", [])
                    if not item and not any(str(q[1]) == prompt_id for q in active):
                        raise RuntimeError("ComfyUI 已丢失该任务（队列被清空或进程重启）；可重试")
                else:
                    await self._poll_execution(ident, port=port)
                    history = await self._comfy_req(port, "GET", "/history/" + prompt_id)
                    item = history.get(prompt_id)
                    if item:
                        state = item.get("status", {})
                        if state.get("status_str") == "error":
                            messages = state.get("messages", [])
                            raise RuntimeError("ComfyUI 执行失败: " + json.dumps(messages, ensure_ascii=False))
                        if state.get("completed"):
                            return item
                    queue = await self._comfy_req(port, "GET", "/queue")
                    active = queue.get("queue_running", []) + queue.get("queue_pending", [])
                    if not item and not any(str(q[1]) == prompt_id for q in active):
                        # Re-read history to cover completion between /history and /queue.
                        history = await self._comfy_req(port, "GET", "/history/" + prompt_id)
                        if prompt_id not in history:
                            raise RuntimeError("ComfyUI 已丢失该任务（队列被清空或进程重启）；可重试")
                        poll_failures = 0
                        first_failure_at = None
                        continue
                poll_failures = 0
                first_failure_at = None
            except (TimeoutError, asyncio.TimeoutError, AdapterError, OSError) as exc:
                poll_failures += 1
                if first_failure_at is None:
                    first_failure_at = time.monotonic()
                err_text = str(exc) or type(exc).__name__
                log.warning("Task %s (port %s) poll transient error (%d): %s", ident, port, poll_failures, err_text)
                if poll_failures >= 3:
                    try:
                        st = await self.runtime.status()
                    except Exception:
                        st = {}
                    if st.get("state", st.get("status")) in {"off", "disconnected", "error"}:
                        raise RuntimeError("GPU 运行时连接中断，任务无法继续追踪；请检查机器状态后重试") from exc
                if poll_failures >= 6 or (time.monotonic() - first_failure_at) >= 120:
                    raise RuntimeError(f"轮询 ComfyUI 状态连续失败 {poll_failures} 次：{err_text}") from exc
                await asyncio.sleep(min(5.0, max(self.poll_interval, 1.5 * poll_failures)))
                continue
            # Overall graph completion is not a sampling percentage.
            current = self.db.get("tasks", ident)
            if current.get("progress") is not None:
                await self._save(current, progress=None)
            self.last_activity = time.time()
            await asyncio.sleep(self.poll_interval)

    @staticmethod
    def _output_parents(graph):
        # Match graph identity, never resolution, filename or output ordering.
        def link(value):
            return (str(value[0]), value[1]) if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int) else None
        def ancestors(node_id, seen=None):
            seen = set() if seen is None else seen
            if node_id in seen: return set()
            seen.add(node_id)
            result = set()
            for value in graph.get(node_id, {}).get('inputs', {}).values():
                edge = link(value)
                if edge:
                    result.add(edge[0]); result.update(ancestors(edge[0], seen))
            return result
        saves = {key: link(node.get('inputs', {}).get('images')) for key, node in graph.items() if node.get('class_type') == 'SaveImage'}
        parents = {}
        for key, edge in saves.items():
            if not edge: continue
            upscalers = [n for n in {edge[0]} | ancestors(edge[0]) if graph.get(n, {}).get('class_type') == 'SeedVR2VideoUpscaler']
            if len(upscalers) != 1: continue
            inputs = graph[upscalers[0]].get('inputs', {})
            source = link(inputs.get('image')) or link(inputs.get('images'))
            if not source: continue
            matches = [other for other, original in saves.items() if other != key and original == source]
            if len(matches) == 1: parents[key] = matches[0]
        return parents

    async def _archive(self, row, history, port=8188):
        if self.gallery is None:
            raise RuntimeError("尚未接入 Gallery.archive；原始输出保留在 ComfyUI")
        results = copy.deepcopy(row.get("results", []))
        archived = {r.get("comfy_output_key") for r in results}
        outputs = []
        for node_id, output in history.get("outputs", {}).items():
            if row.get("output_nodes") and str(node_id) not in row["output_nodes"]:
                continue
            seen = set()
            for group in ("images", "videos", "gifs"):
                for image in output.get(group, []):
                    if not isinstance(image, dict) or not image.get("filename"):
                        continue
                    suffix = Path(image["filename"]).suffix.lower()
                    identity = tuple(image.get(k, "output" if k == "type" else "") for k in ("filename", "subfolder", "type"))
                    if image.get("type", "output") == "output" and suffix in {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm'} and identity not in seen:
                        seen.add(identity)
                        outputs.append((str(node_id), image))
        if not outputs:
            raise RuntimeError("ComfyUI 运行结束但没有可归档图片或视频；请使用保存输出节点")
        graph = row.get('submitted_prompt', row.get('prompt', {}))
        parent_nodes = self._output_parents(graph)
        # Only a unique image on each save node is safely pairable without batch indices.
        counts = {node: sum(1 for key, _ in outputs if key == node) for node, _ in outputs}
        parent_nodes = {child: parent for child, parent in parent_nodes.items() if counts.get(child) == counts.get(parent) == 1}
        outputs.sort(key=lambda item: item[0] in parent_nodes)
        for node_id, image in outputs:
            self._check_cancel(row["id"])
            key = node_id + ":" + json.dumps(image, sort_keys=True)
            if key in archived:
                continue
            content = await self._comfy_req(port, "GET", "/view?" + urlencode({k: image[k] for k in ("filename", "subfolder", "type") if k in image}))
            if not isinstance(content, bytes):
                raise RuntimeError("ComfyUI /view 未返回图片二进制")
            metadata = copy.deepcopy(row["params"])
            metadata.update(api_workflow=row.get("submitted_prompt", row["prompt"]), workflow_hash=row.get("submitted_workflow_hash", row.get("workflow_hash")), comfy_prompt_id=row["prompt_id"])
            parent_id = row['params'].get('parent_id') or row['params'].get('image_id')
            source_node = parent_nodes.get(node_id)
            if source_node:
                candidates = [r for r in results if r.get('comfy_output_key', '').split(':', 1)[0] == source_node]
                if len(candidates) == 1 and candidates[0].get('id'):
                    parent_id = candidates[0]['id']
            metadata.update(comfy_output_node=node_id)
            if parent_id:
                metadata['parent_id'] = parent_id
            result = await self.gallery.archive(row['id'], content, image['filename'], metadata, parent_id=parent_id)
            result = slim_task_result({**result, "comfy_output_key": key})
            results.append(result)
            row = await self._save(row, results=results)
        self._check_cancel(row["id"])
        metrics = await self._finish_metrics(row)
        await self._save(row, status="completed", stage="completed", progress=1.0, error=None, retryable=False, **metrics)
