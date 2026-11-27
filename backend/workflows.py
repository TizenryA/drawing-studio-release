"""Native ComfyUI workflows and user-editable field mappings.

Contract:
    store = WorkflowStore(data_dir)
    record = store.save({"name": "Custom", "workflow": api_or_ui_json,
                         "mapping": {"prompt": {"node": "7", "input": "text"}}},
                        object_info=comfy_object_info)
    built = store.build(params, models=db.list("models"), object_info=object_info)
    # built: prompt, params (normalized immutable snapshot), workflow_id,
    # workflow_version, required_models, output_nodes.

Mapping values are {node,input}, "node.input", or a list of those targets.
Dotted parameter paths (e.g. upscale.scale) and arbitrary custom fields work.
UI JSON conversion needs /object_info, or explicit widget_names per node/class;
unknown widgets, bypasses and subgraphs fail with an actionable import error.
No custom node is installed or downloaded implicitly. Model names are ComfyUI
relative paths; model IDs resolve through supplied model records or resolver.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import tempfile
from datetime import datetime, timezone


class WorkflowError(ValueError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


def now():
    return datetime.now(timezone.utc).isoformat()


def _path(value):
    value = str(value).replace("\\", "/")
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\x00" in value or ":" in value:
        raise WorkflowError(f"模型或输入文件路径无效: {value!r}")
    return value


def _get(data, path, default=None):
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def _targets(value):
    values = value if isinstance(value, list) else [value]
    for target in values:
        if isinstance(target, str):
            node, name = target.split(".", 1)
        else:
            node, name = target["node"], target["input"]
        yield str(node), name


def adapt_seedvr2(prompt, object_info):
    """Adapt only SeedVR2 optional scalar inputs to the installed node schema.

    Connections, model choices and requested values are never guessed. New
    required scalar fields may use the installed extension's declared default.
    """
    prompt = copy.deepcopy(prompt)
    for node in prompt.values():
        if not node["class_type"].startswith("SeedVR2") or node["class_type"] not in object_info:
            continue
        schema = object_info[node["class_type"]].get("input", {})
        known = {**schema.get("required", {}), **schema.get("optional", {})}
        node["inputs"] = {k: v for k, v in node["inputs"].items() if k in known}
        for key, spec in schema.get("required", {}).items():
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if key not in node["inputs"] and "default" in options:
                node["inputs"][key] = copy.deepcopy(options["default"])
    return prompt


def parse_video_crf(value, default=15):
    """Normalize video export CRF (0-30, lower = higher quality)."""
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise WorkflowError("视频导出 CRF 须在 0 到 30 之间")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("视频导出 CRF 须在 0 到 30 之间") from exc
    if not math.isfinite(numeric):
        raise WorkflowError("视频导出 CRF 须在 0 到 30 之间")
    crf = int(round(numeric))
    if crf < 0 or crf > 30:
        raise WorkflowError("视频导出 CRF 须在 0 到 30 之间")
    return crf


def _find_input_spec(schema, key):
    if not isinstance(schema, dict):
        return None
    for group in ("required", "optional"):
        group_dict = schema.get(group)
        if isinstance(group_dict, dict) and key in group_dict:
            return group_dict[key]
    return schema.get(key)


def _dynamic_option_inputs(spec, option_key):
    if not (isinstance(spec, (list, tuple)) and len(spec) >= 2):
        return None
    if spec[0] != "COMFY_DYNAMICCOMBO_V3" or not isinstance(spec[1], dict):
        return None
    for option in spec[1].get("options") or []:
        if isinstance(option, dict) and option.get("key") == option_key:
            return option.get("inputs") or {}
    return None


def adapt_save_video(prompt, object_info, crf=15):
    """Configure SaveVideo for high-quality H.264 re-encoding when ComfyUI exposes DynamicCombo CRF."""
    prompt = copy.deepcopy(prompt)
    schema = (object_info or {}).get("SaveVideo", {}).get("input")
    fmt_spec = _find_input_spec(schema, "format")
    mp4_inputs = _dynamic_option_inputs(fmt_spec, "mp4")
    codec_spec = _find_input_spec(mp4_inputs, "codec")
    h264_inputs = _dynamic_option_inputs(codec_spec, "h264")
    encoding_spec = _find_input_spec(h264_inputs, "encoding")
    reencode_inputs = _dynamic_option_inputs(encoding_spec, "re-encode")
    crf_spec = _find_input_spec(reencode_inputs, "crf")
    if crf_spec is None:
        return prompt
    target_crf = float(parse_video_crf(crf, default=15))
    for node in prompt.values():
        if node.get("class_type") != "SaveVideo":
            continue
        inputs = node.setdefault("inputs", {})
        fmt_val = inputs.get("format", "auto")
        codec_val = inputs.get("format.codec", inputs.get("codec", "auto"))
        if fmt_val in ("auto", "mp4") and codec_val in ("auto", "h264"):
            inputs["format"] = "mp4"
            inputs["format.codec"] = "h264"
            inputs.pop("codec", None)
            inputs["format.codec.encoding"] = "re-encode"
            inputs["format.codec.encoding.crf"] = target_crf
    return prompt


def ui_to_api(workflow, object_info=None, widget_names=None):
    """Convert regular LiteGraph JSON, without guessing unknown widget order."""
    info, names = object_info or {}, widget_names or {}
    nodes = {str(n["id"]): n for n in workflow.get("nodes", [])}
    links = {}
    for link in workflow.get("links", []):
        if isinstance(link, dict):
            links[str(link["id"])] = (str(link["origin_id"]), link["origin_slot"])
        else:
            links[str(link[0])] = (str(link[1]), link[2])

    def source(link_id, visited=None):
        visited = set() if visited is None else visited
        if str(link_id) in visited:
            raise WorkflowError("工作流链接存在循环")
        visited.add(str(link_id))
        if str(link_id) not in links:
            raise WorkflowError(f"找不到工作流链接 {link_id}")
        node_id, slot = links[str(link_id)]
        node = nodes[node_id]
        if node["type"] == "Reroute":
            return source(node["inputs"][0]["link"], visited)
        if node.get("mode", 0) == 2:
            raise WorkflowError(f"节点 {node_id} 已禁用，请启用后导出 API JSON")
        if node.get("mode", 0) == 4:
            output_type = node.get("outputs", [])[slot].get("type")
            candidates = [x for x in node.get("inputs", []) if x.get("type") == output_type and x.get("link") is not None]
            if len(candidates) == 1:
                return source(candidates[0]["link"], visited)
            if not candidates:
                return None  # bypassed optional configuration (e.g. torch.compile)
            raise WorkflowError(f"旁路节点 {node_id} 有多个候选输入，请导出 API JSON")
        if node["type"] == "PrimitiveNode":
            return copy.deepcopy(node["widgets_values"][0])
        return [node_id, slot]

    result = {}
    for node_id, node in nodes.items():
        cls = node["type"]
        if cls in ("Reroute", "PrimitiveNode", "Note", "MarkdownNote") or node.get("mode", 0) == 2:
            continue
        if node.get("mode", 0) == 4:
            continue
        node_info = info.get(cls, {})
        inputs, linked = {}, {}
        for item in node.get("inputs", []):
            if item.get("link") is not None:
                value = source(item["link"])
                if value is not None:
                    linked[item["name"]] = value
        values = node.get("widgets_values", [])
        if isinstance(values, dict):
            inputs.update(values)
        elif values:
            explicit = names.get(node_id, names.get(cls))
            if explicit is not None:
                if len(explicit) != len(values):
                    raise WorkflowError(f"节点 {node_id} widget_names 与 widgets_values 长度不同")
                inputs.update({k: v for k, v in zip(explicit, values) if k is not None})
            else:
                if not node_info:
                    raise WorkflowError(f"UI 导入需要节点 {cls} 的 /object_info 或 widget_names 映射")
                index = 0
                specs = {**node_info.get("input", {}).get("required", {}), **node_info.get("input", {}).get("optional", {})}
                ui_widgets = [i["name"] for i in node.get("inputs", []) if i.get("widget") and i.get("link") is None]
                order = ui_widgets or [name for group in ("required", "optional") for name in node_info.get("input_order", {}).get(group, list(node_info.get("input", {}).get(group, {})))]
                for name in order:
                    if name == "upload" and cls in ("LoadImage", "LoadImageMask"):
                        index += 1  # UI-only image upload chooser
                        continue
                    if name not in specs:
                        raise WorkflowError(f"节点 {node_id} 控件 {name} 无服务器定义，请提供 widget_names")
                    spec = specs[name]
                    kind, opts = spec[0], spec[1] if len(spec) > 1 else {}
                    if opts.get("forceInput") or not (isinstance(kind, list) or kind in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO")):
                        continue
                    if name in linked:
                        continue
                    if index >= len(values):
                        raise WorkflowError(f"节点 {node_id} 缺少控件 {name}；请导出 API JSON 或提供 widget_names")
                    inputs[name] = values[index]
                    index += 1
                    if opts.get("control_after_generate") or (name in ("seed", "noise_seed") and index < len(values) and values[index] in ("fixed", "increment", "decrement", "randomize")):
                        index += 1
                    if opts.get("image_upload") and not ui_widgets and index < len(values) and values[index] in ("image", "video"):
                        index += 1
                if index != len(values):
                    raise WorkflowError(f"节点 {node_id} 有无法识别的控件，请提供 widget_names（忽略项填 null）或导出 API JSON")
        inputs.update(linked)
        result[node_id] = {"class_type": cls, "inputs": inputs, "_meta": {"title": node.get("title", cls)}}
    if not result:
        raise WorkflowError("工作流没有可执行节点")
    return result


BUILTINS = [
    ("sdxl-txt2img", "SDXL 文生图 / 多 LoRA", "txt2img"),
    ("sdxl-img2img", "SDXL 图生图", "img2img"),
    ("sdxl-inpaint", "SDXL 局部修复", "inpaint"),
    ("sdxl-hires", "SDXL Latent 高清重绘", "hires"),
    ("esrgan", "ESRGAN 独立超分", "upscale"),
    ("seedvr2", "SeedVR2 单图 AI 精修", "seedvr2"),
    ("video-seedvr2", "SeedVR2 视频 AI 精修", "video_seedvr2"),
    ("supir", "SUPIR（导入 API 工作流）", "supir"),
]


MODEL_LOADERS = {
    "CheckpointLoaderSimple": {"ckpt_name": "checkpoints"},
    "UNETLoader": {"unet_name": "diffusion_models"},
    "CLIPLoader": {"clip_name": "text_encoders"},
    "DualCLIPLoader": {"clip_name1": "text_encoders", "clip_name2": "text_encoders"},
    "TripleCLIPLoader": {"clip_name1": "text_encoders", "clip_name2": "text_encoders", "clip_name3": "text_encoders"},
    "LoraLoader": {"lora_name": "loras"},
    "LoraLoaderModelOnly": {"lora_name": "loras"},
    "VAELoader": {"vae_name": "vae"},
    "UpscaleModelLoader": {"model_name": "upscale_models"},
    "SeedVR2LoadDiTModel": {"model": "seedvr2"},
    "SeedVR2LoadVAEModel": {"model": "seedvr2"},
    "MinimaxH3LatentUpscaler3DRefineHandoff": {"model_name": "latent_upscale_models"},
    "RIFE VFI": {"ckpt_name": "vfi_models"},
}


def combo_choices(spec):
    """Read legacy list enums and current COMBO/options schemas alike."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options")
        return options if isinstance(options, list) else None
    return None



def parse_frame_interp(value):
    """H3 补帧：关闭、48fps（2×）或 60fps（3× 到 72 再抽帧到 60）。"""
    if value in (None, "", "off", "none", "关", "关闭", False, 0, "0", 24, "24"):
        return None
    if value in (48, "48", "48fps", 2, "2", "2x", "2×"):
        return 48
    if value in (60, "60", "60fps"):
        return 60
    raise WorkflowError("补帧只支持关闭、48fps 或 60fps")


H3_VIDEO_NODES = ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")


def parse_continue_frames(value, length):
    """H3 路线B 尾段惯性续写重叠帧数：须满足 17k+5（如 5、22、39）且小于本段总帧数。"""
    if value in (None, ""):
        return 5
    try:
        frames = int(value)
    except (ValueError, TypeError) as exc:
        raise WorkflowError("续写重叠帧数须为整数（如 5 或 22）") from exc
    if frames < 5 or (frames - 5) % 17 != 0 or frames >= length:
        raise WorkflowError(f"续写重叠帧数须满足 17k+5（如 5、22、39）且小于视频总帧数 {length}")
    return frames


def compute_keyframe_indices(count, length, *, continue_mode=False, continue_frames=5, custom_frames=None):
    """计算 H3 多关键帧在时间轴上的锚定帧号（0 .. length-1）。"""
    if count <= 0:
        return []
    if continue_mode:
        if count == 1:
            auto = [length - 1]
        else:
            auto = [round((i + 1) * (length - 1) / count) for i in range(count)]
            min_idx = min(length - 1, continue_frames)
            for i in range(count):
                floor = min_idx if i == 0 else min(length - 1, auto[i - 1] + 1)
                if auto[i] < floor:
                    auto[i] = floor
            auto[-1] = length - 1
    else:
        if count == 1:
            auto = [0]
        else:
            auto = [round(i * (length - 1) / (count - 1)) for i in range(count)]

    if custom_frames is None:
        return auto
    if not isinstance(custom_frames, list) or len(custom_frames) != count:
        raise WorkflowError("keyframe_frames 长度须与 keyframes 一致")
    min_allowed = continue_frames if continue_mode else 0
    resolved = []
    for i, raw in enumerate(custom_frames):
        if raw in (None, ""):
            idx = auto[i]
        else:
            try:
                idx = int(raw)
            except (ValueError, TypeError) as exc:
                raise WorkflowError("关键帧帧号须为整数") from exc
            if idx < 0:
                idx = length + idx
        if idx < min_allowed or idx >= length:
            raise WorkflowError(f"关键帧帧号 {idx} 超出允许范围 [{min_allowed}, {length - 1}]")
        resolved.append(idx)
    return resolved


def apply_h3_frame_interp(prompt, interp):
    """Insert RIFE after decode and pad so duration stays length/24."""
    multiplier, container_fps, pads = (2, 48, 1) if interp == 48 else (3, 72, 2)
    # RIFE has no fractional / target-fps mode; 24→60 is 2.5×, so 3× then later resample 72→60.

    def fresh(prefix):
        ident = prefix
        while ident in prompt:
            ident += "_"
        return ident

    for node in list(prompt.values()):
        if node.get("class_type") not in ("CreateVideo", "VHS_VideoCombine"):
            continue
        fps_key = "fps" if node["class_type"] == "CreateVideo" else "frame_rate"
        images = node["inputs"].get("images")
        if not (isinstance(images, list) and len(images) == 2):
            raise WorkflowError("补帧需要 CreateVideo 的 images 连接到解码节点")
        rife_id = fresh("h3_rife")
        prompt[rife_id] = {"class_type": "RIFE VFI", "inputs": {
            "ckpt_name": "rife49.pth",
            "frames": copy.deepcopy(images),
            "clear_cache_after_n_frames": 10,
            "multiplier": multiplier,
            "fast_mode": False,
            "ensemble": True,
            "scale_factor": 1.0,
            "dtype": "float32",
            "torch_compile": False,
            "batch_size": 1,
        }}
        frames = [rife_id, 0]
        if pads:
            last_id = fresh("h3_rife_last")
            prompt[last_id] = {"class_type": "ImageFromBatch", "inputs": {
                "image": [rife_id, 0], "batch_index": -1, "length": 1,
            }}
            current = [rife_id, 0]
            for i in range(pads):
                pad_id = fresh(f"h3_rife_pad{i+1}")
                prompt[pad_id] = {"class_type": "ImageBatch", "inputs": {
                    "image1": current, "image2": [last_id, 0],
                }}
                current = [pad_id, 0]
            frames = current
        node["inputs"]["images"] = frames
        node["inputs"][fps_key] = container_fps
    return container_fps


class WorkflowStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.directory = self.data_dir / "workflows"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _file(self, ident):
        ident = str(ident)
        if not ident or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in ident):
            raise WorkflowError("工作流 ID 只能包含字母、数字、下划线和短横线")
        return self.directory / f"{ident}.json"

    def list(self):
        records = {ident: self.get(ident) for ident, _, _ in BUILTINS}
        for path in sorted(self.directory.glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            records[record["id"]] = record
        return list(records.values())

    def get(self, ident):
        path = self._file(ident)
        if path.exists():
            return json.loads(path.read_text("utf-8"))
        for key, name, mode in BUILTINS:
            if key == ident:
                return {"id": key, "name": name, "mode": mode, "builtin": True, "version": 1,
                        "requires_import": mode == "supir",
                        "dependency": "ComfyUI-SeedVR2_VideoUpscaler" if mode in ("seedvr2", "video_seedvr2") else "ComfyUI-SUPIR" if mode == "supir" else None}
        return None

    def save(self, record, *, object_info=None, widget_names=None):
        record = copy.deepcopy(record)
        record.setdefault("id", secrets.token_hex(8))
        path = self._file(record["id"])
        previous = self.get(record["id"])
        raw = record.get("workflow", record.get("prompt"))
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise WorkflowError("请提供原生 API 工作流 JSON 或 UI workflow JSON")
        record["source_workflow"] = copy.deepcopy(raw)
        if "nodes" in raw:
            raw = ui_to_api(raw, object_info or record.get("object_info"), widget_names or record.get("widget_names"))
        elif "prompt" in raw and isinstance(raw["prompt"], dict):
            raw = raw["prompt"]
        record["workflow"] = self._validate(raw)
        record.setdefault("mapping", {})
        for field, target in record["mapping"].items():
            for node, name in _targets(target):
                if node not in raw or name not in raw[node]["inputs"]:
                    raise WorkflowError(f"字段 {field} 映射不存在: {node}.{name}")
        record.update(builtin=False, version=(previous or {}).get("version", 0) + 1, updated_at=now())
        fd, temp = tempfile.mkstemp(dir=self.directory, prefix=".workflow-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return record

    def delete(self, ident):
        path = self._file(ident)
        if not path.exists():
            return False
        path.unlink()
        return True

    @staticmethod
    def _validate(prompt):
        if not isinstance(prompt, dict) or not prompt:
            raise WorkflowError("API 工作流为空")
        prompt = {str(key): value for key, value in prompt.items()}
        for node_id, node in prompt.items():
            if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
                raise WorkflowError(f"节点 {node_id} 不是有效 API 节点")
        return prompt

    def build(self, params, models=None, resolver=None, object_info=None):
        """resolver(reference, kind)-> model record or relative ComfyUI filename.

        Model records: id,name,filename/comfy_name/path,kind/type/category.
        model_snapshot supplied on params freezes registry resolution at submit.
        object_info=None skips dependency checks; worker must check before enqueue.
        """
        p = copy.deepcopy(params)
        extra = p.pop("comfy", {}) or {}
        p.update(extra)
        if p.get("input_image") and not p.get("image"):
            p["image"] = p["input_image"]
        if p.get("sampler_name") and not p.get("sampler"):
            p["sampler"] = p["sampler_name"]
        aliases = {"text2img": "txt2img", "image2image": "img2img", "inpainting": "inpaint", "latent": "hires", "standard": "upscale", "esrgan": "upscale", "video-seedvr2": "video_seedvr2"}
        mode = aliases.get(p.get("mode"), p.get("mode", "txt2img"))
        ident = p.get("workflow_id") or p.get("workflow") or {"txt2img": "sdxl-txt2img", "img2img": "sdxl-img2img", "inpaint": "sdxl-inpaint", "hires": "sdxl-hires", "upscale": "esrgan", "seedvr2": "seedvr2", "video_seedvr2": "video-seedvr2", "supir": "supir"}.get(mode, mode)
        if isinstance(ident, dict):
            raise WorkflowError("自定义工作流请先保存，再用 workflow_id 引用")
        record = self.get(ident)
        if not record:
            raise WorkflowError(f"找不到工作流 {ident}")
        p["mode"] = record.get("mode", mode)
        if p.get("size") and p["size"] != "auto":
            try:
                width, height = str(p["size"]).lower().split("x")
                p.setdefault("width", int(width)); p.setdefault("height", int(height))
            except ValueError as exc:
                raise WorkflowError("size 应为 1024x1024 形式") from exc
        h3_nodes = [(key, node) for key, node in record.get("workflow", {}).items()
                    if node.get("class_type") in H3_VIDEO_NODES]
        h3_ref_nodes = [(key, node) for key, node in h3_nodes if node.get("class_type") == "MiniMaxH3ReferenceToVideo"]
        h3_i2v_nodes = [(key, node) for key, node in h3_nodes if node.get("class_type") == "MiniMaxH3ImageToVideo"]
        h3_continue_mode = bool(h3_i2v_nodes and any(n.get("class_type") == "LoadVideo" for n in record.get("workflow", {}).values()))
        if h3_nodes:
            original = h3_nodes[0][1]["inputs"]
            for key in ("width", "height", "length"):
                p.setdefault(key, original.get(key, 124 if key == "length" else 864 if key == "width" else 480))
            sampler = next((n["inputs"] for n in record["workflow"].values()
                            if n.get("class_type") in ("KSampler", "KSamplerAdvanced", "BasicScheduler") and "steps" in n["inputs"]), {})
            p.setdefault("steps", sampler.get("steps", 8))
            try:
                fps = float(p.get("fps", 24))
                if not math.isfinite(fps) or fps != 24:
                    raise ValueError("fps")
                for key in ("width", "height"):
                    value = float(p[key])
                    if not math.isfinite(value) or not 32 <= value <= 16384 or value % 32:
                        raise ValueError(key)
                    p[key] = int(value)
                if p.get("duration_seconds") is not None:
                    seconds = float(p["duration_seconds"])
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError("duration_seconds")
                    p["length"] = 5 + 17 * max(0, math.ceil((seconds * fps - 5) / 17 - 1e-9))
                length = float(p["length"])
                if not math.isfinite(length) or not 5 <= length <= 3600:
                    raise ValueError("length")
                p["length"] = 5 + 17 * max(0, math.ceil((length - 5) / 17 - 1e-9))
                if p["length"] > 3600:
                    raise ValueError("length")
                for node in record["workflow"].values():
                    if node.get("class_type") in ("CreateVideo", "VHS_VideoCombine"):
                        graph_fps = node["inputs"].get("fps", node["inputs"].get("frame_rate", 24))
                        if isinstance(graph_fps, list) or float(graph_fps) != 24:
                            raise ValueError("graph fps")
            except (ValueError, TypeError, OverflowError) as exc:
                raise WorkflowError("H3 视频参数无效：宽高须为 32 的倍数，帧率为 24，时长须为正数且对齐后不超过 3600 帧") from exc
            p["fps"] = 24
            p["actual_duration_seconds"] = p["length"] / 24
            interp = parse_frame_interp(p.get("frame_interp"))
            p["frame_interp"] = interp or "off"
            p["output_fps"] = interp or 24
            p["video_crf"] = parse_video_crf(p.get("video_crf"), default=15)
        elif any(n.get("class_type") in ("CreateVideo", "VHS_VideoCombine", "SaveVideo")
                 for n in record.get("workflow", {}).values()):
            p["video_crf"] = parse_video_crf(p.get("video_crf"), default=15)
        elif p["mode"] != "video_seedvr2":
            for key in ("duration_seconds", "fps", "length", "actual_duration_seconds", "frame_interp", "output_fps", "video_crf"):
                p.pop(key, None)
        if not h3_nodes:
            for key in ("ref_images", "keyframes", "keyframe_frames", "continue_frames"):
                p.pop(key, None)
            has_load_video = any(n.get("class_type") == "LoadVideo" for n in record.get("workflow", {}).values())
            if p["mode"] != "video_seedvr2" and not has_load_video:
                for key in ("source_video", "source_video_id"):
                    p.pop(key, None)
        if p["mode"] == "video_seedvr2":
            source_video = p.get("source_video")
            if not source_video or not isinstance(source_video, str):
                raise WorkflowError("缺少待精修的源视频")
            p["source_video"] = _path(source_video)

            def _even_int(val, default, lo, hi, label, *, allow_zero=False):
                if val in (None, ""):
                    return default
                if isinstance(val, bool):
                    raise WorkflowError(f"{label}须为整数")
                try:
                    num = float(val)
                except (TypeError, ValueError) as exc:
                    raise WorkflowError(f"{label}须为整数") from exc
                if not math.isfinite(num) or num != int(num):
                    raise WorkflowError(f"{label}须为整数")
                iv = int(num)
                if allow_zero and iv == 0:
                    return 0
                if iv < lo or iv > hi or iv % 2 != 0:
                    raise WorkflowError(f"{label}须为 {lo} 到 {hi} 之间的偶数")
                return iv

            def _range_int(val, default, lo, hi, label):
                if val in (None, ""):
                    return default
                if isinstance(val, bool):
                    raise WorkflowError(f"{label}须为整数")
                try:
                    num = float(val)
                except (TypeError, ValueError) as exc:
                    raise WorkflowError(f"{label}须为整数") from exc
                if not math.isfinite(num) or num != int(num):
                    raise WorkflowError(f"{label}须为整数")
                iv = int(num)
                if iv < lo or iv > hi:
                    raise WorkflowError(f"{label}须在 {lo} 到 {hi} 之间")
                return iv

            p["resolution"] = _even_int(p.get("resolution"), 1080, 128, 4096, "精修短边分辨率")
            p["max_resolution"] = _even_int(p.get("max_resolution"), 0, 128, 8192, "最大边分辨率", allow_zero=True)
            fbs = _range_int(p.get("frame_batch_size"), 5, 1, 65, "时序批帧数")
            if (fbs - 1) % 4 != 0:
                raise WorkflowError("时序批帧数须满足 4k+1（如 1、5、9、13）")
            p["frame_batch_size"] = fbs
            default_overlap = 0 if fbs == 1 else min(2, fbs - 1)
            ov = _range_int(p.get("temporal_overlap"), default_overlap, 0, 16, "批次重叠帧数")
            if ov >= fbs:
                raise WorkflowError(f"批次重叠帧数 {ov} 必须小于时序批帧数 {fbs}")
            p["temporal_overlap"] = ov
            p["prepend_frames"] = _range_int(p.get("prepend_frames"), 0, 0, 32, "前置缓冲帧数")
            cc = str(p.get("color_correction") or "lab")
            if cc not in {"lab", "wavelet", "wavelet_adaptive", "hsv", "adain", "none"}:
                raise WorkflowError("不支持的 SeedVR2 色彩校正模式")
            p["color_correction"] = cc
            p["blocks_to_swap"] = _range_int(p.get("blocks_to_swap"), 0, 0, 36, "BlockSwap 层数")
            p["swap_io_components"] = bool(p.get("swap_io_components", False))
            p["video_crf"] = parse_video_crf(p.get("video_crf"), default=15)
            p.setdefault("free_gpu_before_run", True)
            p.setdefault("dit_model", "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
            p.setdefault("seedvr2_vae", "ema_vae_fp16.safetensors")
            p["model"] = p["dit_model"]
            p["n"] = 1
            p["batch_size"] = 1
        else:
            if h3_ref_nodes:
                if "ref_images" in p:
                    if not isinstance(p["ref_images"], list) or len(p["ref_images"]) > 9:
                        raise WorkflowError("H3 参考图片须为有序数组，最多 9 张")
                    if any(not isinstance(v, str) for v in p["ref_images"]):
                        raise WorkflowError("H3 参考图片路径须为字符串")
                    p["ref_images"] = [_path(v) for v in p["ref_images"]]
                for key in ("keyframes", "keyframe_frames", "source_video", "source_video_id", "continue_frames"):
                    p.pop(key, None)
            if h3_i2v_nodes:
                # Route A (keyframes) & Route B (video continuation): normalize keyframes/continue settings.
                if h3_continue_mode:
                    default_cf = next(
                        (n["inputs"].get("length", 5) for n in record["workflow"].values()
                         if n.get("class_type") == "ImageFromBatch" and isinstance(n["inputs"].get("image"), list)
                         and record["workflow"].get(str(n["inputs"]["image"][0]), {}).get("class_type") == "GetVideoComponents"),
                        5,
                    )
                    p["continue_frames"] = parse_continue_frames(p.get("continue_frames", default_cf), p["length"])
                    if p.get("source_video"):
                        p["source_video"] = _path(p["source_video"])
                else:
                    for key in ("source_video", "source_video_id", "continue_frames"):
                        p.pop(key, None)
                if "keyframes" not in p and "ref_images" in p:
                    p["keyframes"] = p.pop("ref_images")
                else:
                    p.pop("ref_images", None)
                if "keyframes" in p:
                    if not isinstance(p["keyframes"], list) or len(p["keyframes"]) > 9:
                        raise WorkflowError("H3 关键帧须为有序数组，最多 9 张")
                    if any(not isinstance(v, str) for v in p["keyframes"]):
                        raise WorkflowError("H3 关键帧路径须为字符串")
                    p["keyframes"] = [_path(v) for v in p["keyframes"]]
                    p["keyframe_frames"] = compute_keyframe_indices(
                        len(p["keyframes"]),
                        p["length"],
                        continue_mode=h3_continue_mode,
                        continue_frames=p.get("continue_frames", 5),
                        custom_frames=p.get("keyframe_frames"),
                    )
                else:
                    p.pop("keyframe_frames", None)
        up = p.get("upscale", {})
        if isinstance(up, dict):
            up = copy.deepcopy(up)
            for old, new in (("target_width", "width"), ("target_height", "height")):
                if up.get(old):
                    up.setdefault(new, up[old])
            p["upscale"] = up
        if p.get("seed") in (None, -1, "-1", ""):
            p["seed"] = secrets.randbits(32 if (p["mode"] in ("seedvr2", "video_seedvr2") or ident in ("seedvr2", "video-seedvr2")) else 63)
        else:
            p["seed"] = int(p["seed"])
            if p["mode"] in ("seedvr2", "video_seedvr2") or ident in ("seedvr2", "video-seedvr2"):
                p["seed"] = p["seed"] % 4294967295
        if "n" in p:
            p["batch_size"] = p["n"]
        if "shift" in p:
            raw_shift = p.get("shift")
            if raw_shift in (None, ""):
                p.pop("shift", None)
            elif isinstance(raw_shift, bool):
                raise WorkflowError("Shift 须为 0 到 100 之间的数字")
            else:
                try:
                    shift_val = float(raw_shift)
                except (TypeError, ValueError) as exc:
                    raise WorkflowError("Shift 须为 0 到 100 之间的数字") from exc
                if not math.isfinite(shift_val) or shift_val < 0.0 or shift_val > 100.0:
                    raise WorkflowError("Shift 须在 0 到 100 之间")
                p["shift"] = shift_val
        if record.get("workflow"):
            for field, target in record.get("mapping", {}).items():
                if "." in field or (field in p and p[field] is not None):
                    continue
                for node, name in _targets(target):
                    baked = record["workflow"].get(node, {}).get("inputs", {}).get(name)
                    if baked is not None and not isinstance(baked, list):
                        p[field] = copy.deepcopy(baked)
                        break
        for key, value in {"width": 1024, "height": 1024, "steps": 28, "cfg": 5.5, "sampler": "euler", "scheduler": "normal", "prompt": "", "negative_prompt": "", "batch_size": p.get("n", 1), "denoise": 0.65, "loras": []}.items():
            p.setdefault(key, value)
        required = []
        catalog = p.get("model_snapshot", models or [])

        def resolve(ref, kind):
            if not ref:
                raise WorkflowError(f"未选择 {kind} 模型")
            matches = [m for m in catalog if ref in (m.get("id"), m.get("name"), m.get("filename"), m.get("comfy_name"))]
            matches.sort(key=lambda m: (bool(m.get("drive_file_id")), m.get("kind") == kind, m.get("status") in ("ready", "cached")), reverse=True)
            item = matches[0] if matches else None
            if item is None and resolver:
                item = resolver(ref, kind)
            item = copy.deepcopy(item) if isinstance(item, dict) else {"id": ref, "filename": item or ref, "kind": kind}
            name = item.get("comfy_name") or item.get("filename") or item.get("path") or item.get("name")
            name = _path(name)
            item["comfy_name"] = name
            item.setdefault("kind", kind)
            if item not in required:
                required.append(item)
            return name

        if record.get("workflow"):
            prompt = copy.deepcopy(record["workflow"])
            fields = copy.deepcopy(p)
            fields.update(p.get("fields", {}))
            for field, target in record.get("mapping", {}).items():
                sentinel = object()
                value = _get(fields, field, sentinel)
                if value is sentinel:
                    continue
                kind = record.get("model_fields", {}).get(field)
                if kind:
                    value = resolve(value, kind)
                for node, name in _targets(target):
                    prompt[node]["inputs"][name] = copy.deepcopy(value)
            if "shift" in p and "shift" not in record.get("mapping", {}):
                for node in prompt.values():
                    if node.get("class_type") == "ModelSamplingAuraFlow" and isinstance(node.get("inputs"), dict) and "shift" in node["inputs"]:
                        node["inputs"]["shift"] = p["shift"]
            # H3 controls work for every imported graph, including legacy manifests.
            # Apply the validated native values after arbitrary user mappings.
            for node_id, _ in h3_nodes:
                prompt[node_id]["inputs"].update({key: p[key] for key in ("width", "height", "length")})
            # Match imported slots by node identity or registered filename, never list order.
            slots = {key: node for key, node in prompt.items()
                     if node.get("class_type") in ("LoraLoader", "LoraLoaderModelOnly")}
            user_loras = [({"name": item} if isinstance(item, str) else item)
                          for item in (p.get("loras") or [])]
            extra_loras = []
            for lora in user_loras:
                ref = lora.get("id") or lora.get("name")
                if not ref:
                    continue
                model = next((m for m in catalog if ref in (m.get("id"), m.get("name"), m.get("filename"), m.get("comfy_name"))), {})
                names = {v for v in [ref, lora.get("name"), model.get("id"), model.get("name"), model.get("filename"), model.get("comfy_name")] if v}
                target = lora.get("node_id")
                matches = [node for key, node in slots.items()
                           if (str(key) == str(target) if target is not None else node["inputs"].get("lora_name") in names)]
                if not matches and target is None and len(slots) == len(user_loras) == 1:
                    matches = list(slots.values())  # Legacy single-slot replacement.
                enabled = lora.get("enabled", True) is not False
                if not matches:
                    if enabled:
                        extra_loras.append(lora)
                    continue
                resolved = resolve(ref, "loras")
                strength = float(lora.get("strength", lora.get("weight", 1.0)))
                for node in matches:
                    node["inputs"]["lora_name"] = resolved
                    for field in ("strength_model", "strength_clip"):
                        if field in node["inputs"]:
                            node["inputs"][field] = float(lora.get(field, strength)) if enabled else 0.0
            if extra_loras:
                def _upstream_is_moss(node_id):
                    cur, seen = str(node_id), set()
                    for _ in range(20):
                        if cur in seen or cur not in prompt:
                            return False
                        seen.add(cur)
                        node = prompt[cur]
                        ctype = node.get("class_type")
                        inputs = node.get("inputs") or {}
                        if ctype == "ModelSamplingAuraFlow":
                            return True
                        if ctype == "UNETLoader" and "moss" in str(inputs.get("unet_name", "")).lower():
                            return True
                        link = inputs.get("model")
                        if isinstance(link, list) and len(link) >= 1:
                            cur = str(link[0])
                        else:
                            return False
                    return False

                anchor_id = None
                anchor_class = None
                if slots:
                    referenced_by_slots = {
                        str(n["inputs"]["model"][0])
                        for n in slots.values()
                        if isinstance((n.get("inputs") or {}).get("model"), list) and len(n["inputs"]["model"]) >= 1
                    }
                    tails = [k for k in slots if str(k) not in referenced_by_slots] or list(slots.keys())
                    moss_tails = [t for t in tails if _upstream_is_moss(t)]
                    anchor_id = moss_tails[-1] if moss_tails else tails[-1]
                    anchor_class = prompt[anchor_id]["class_type"]
                else:
                    for preferred in ("ModelSamplingAuraFlow", "ModelSamplingSD3", "UNETLoader", "CheckpointLoaderSimple"):
                        hits = [k for k, n in prompt.items() if n.get("class_type") == preferred]
                        if hits:
                            anchor_id = hits[-1]
                            anchor_class = "LoraLoader" if preferred == "CheckpointLoaderSimple" else "LoraLoaderModelOnly"
                            break

                if anchor_id is not None:
                    existing_node_ids = list(prompt.keys())
                    prev_id = str(anchor_id)
                    next_idx = 1
                    for lora in extra_loras:
                        ref = lora.get("id") or lora.get("name")
                        resolved = resolve(ref, "loras")
                        strength = float(lora.get("strength", lora.get("weight", 0.8)))
                        while f"extra_lora_{next_idx}" in prompt:
                            next_idx += 1
                        new_id = f"extra_lora_{next_idx}"
                        next_idx += 1
                        if anchor_class == "LoraLoader":
                            prompt[new_id] = {
                                "class_type": "LoraLoader",
                                "inputs": {
                                    "model": [prev_id, 0],
                                    "clip": [prev_id, 1],
                                    "lora_name": resolved,
                                    "strength_model": float(lora.get("strength_model", strength)),
                                    "strength_clip": float(lora.get("strength_clip", strength)),
                                },
                            }
                        else:
                            prompt[new_id] = {
                                "class_type": "LoraLoaderModelOnly",
                                "inputs": {
                                    "model": [prev_id, 0],
                                    "lora_name": resolved,
                                    "strength_model": float(lora.get("strength_model", strength)),
                                },
                            }
                        prev_id = new_id
                    last_id = prev_id
                    for nid in existing_node_ids:
                        inputs = prompt[nid].get("inputs") or {}
                        for field_name, val in list(inputs.items()):
                            if isinstance(val, list) and len(val) == 2 and str(val[0]) == str(anchor_id):
                                if val[1] == 0:
                                    inputs[field_name] = [last_id, 0]
                                elif val[1] == 1 and anchor_class == "LoraLoader":
                                    inputs[field_name] = [last_id, 1]
        else:
            prompt = self._builtin(p, resolve, catalog=catalog)
        # Compose AI refinement after generation using mapped native graph edges.
        up = p.get("upscale", {})
        if isinstance(up, dict) and up.get("enabled") and up.get("mode") == "ai" and p["mode"] not in ("seedvr2", "video_seedvr2", "supir", "upscale"):
            advanced_id = up.get("workflow") or "seedvr2"
            if advanced_id == ident:
                raise WorkflowError("精修工作流不能引用自身")
            advanced = self.build({**p, "workflow_id": advanced_id, "workflow": advanced_id,
                                   "image": "__generated__.png", "seed": p["seed"] % (2 ** 32),
                                   "upscale": {"enabled": False}, "n": 1, "batch_size": 1, "resolution": up.get("resolution", up.get("height") or 2048)},
                                  models=models, resolver=resolver)
            base_outputs = [key for key, node in prompt.items() if node["class_type"] == "SaveImage"]
            if not base_outputs:
                raise WorkflowError("基础工作流缺少 SaveImage，无法连接精修")
            image_link = prompt[base_outputs[-1]]["inputs"]["images"]
            secondary = advanced["prompt"]
            loads = {key for key, node in secondary.items() if node["class_type"] == "LoadImage" and node["inputs"].get("image") == "__generated__.png"}
            if not loads:
                raise WorkflowError("精修工作流需将 image 字段映射到 LoadImage.image")
            for key in base_outputs:
                del prompt[key]
            for key, node in secondary.items():
                if key in loads:
                    continue
                node = copy.deepcopy(node)
                for field, value in list(node["inputs"].items()):
                    if isinstance(value, list) and len(value) == 2 and str(value[0]) in secondary:
                        if str(value[0]) in loads:
                            if value[1] != 0:
                                raise WorkflowError("精修工作流使用了输入 alpha mask，请直接导入组合 API 工作流")
                            node["inputs"][field] = copy.deepcopy(image_link)
                        else:
                            node["inputs"][field] = ["refine_" + str(value[0]), value[1]]
                prompt["refine_" + key] = node
            for model in advanced["required_models"]:
                if model not in required:
                    required.append(model)
            p["refinement_workflow"] = {"id": advanced_id, "version": advanced["workflow_version"]}
        if h3_nodes and parse_frame_interp(p.get("frame_interp")):
            apply_h3_frame_interp(prompt, parse_frame_interp(p.get("frame_interp")))
        # Gather imported loader references too, so model syncing is not bypassed.
        for node in prompt.values():
            for field, kind in MODEL_LOADERS.get(node["class_type"], {}).items():
                value = node["inputs"].get(field)
                if isinstance(value, str):
                    node["inputs"][field] = resolve(value, kind)
        if h3_nodes:
            # Finalize after custom field mappings, preserving audio/video references.
            interp = parse_frame_interp(p.get("frame_interp"))
            output_fps = 48 if interp == 48 else 72 if interp == 60 else 24
            for node in prompt.values():
                if node["class_type"] in ("CreateVideo", "VHS_VideoCombine"):
                    node["inputs"]["fps" if node["class_type"] == "CreateVideo" else "frame_rate"] = output_fps
            if h3_ref_nodes and "ref_images" in p:
                old_loads = set()
                for node_id, _ in h3_ref_nodes:
                    inputs = prompt[node_id]["inputs"]
                    for key in list(inputs):
                        if key == "ref_images" or key.startswith("ref_images.ref_image_"):
                            link = inputs[key]
                            if isinstance(link, list) and len(link) == 2:
                                old_loads.add(str(link[0]))
                            del inputs[key]
                    for index, image in enumerate(p["ref_images"]):
                        load_id = f"h3_ref_{node_id}_{index}"
                        while load_id in prompt:
                            load_id += "_"
                        prompt[load_id] = {"class_type": "LoadImage", "inputs": {"image": image}}
                        inputs[f"ref_images.ref_image_{index}"] = [load_id, 0]
                used = {str(v[0]) for node in prompt.values() for v in node["inputs"].values() if isinstance(v, list) and len(v) == 2}
                for key in old_loads - used:
                    if prompt.get(key, {}).get("class_type") == "LoadImage":
                        del prompt[key]
            if h3_i2v_nodes:
                if h3_continue_mode:
                    cf = p["continue_frames"]
                    cf_sec = round(cf / 24.0, 4)
                    for node in prompt.values():
                        if node["class_type"] == "LoadVideo" and p.get("source_video"):
                            node["inputs"]["file"] = p["source_video"]
                        elif node["class_type"] == "ImageFromBatch":
                            img_link = node["inputs"].get("image")
                            if isinstance(img_link, list) and len(img_link) == 2 and prompt.get(str(img_link[0]), {}).get("class_type") == "GetVideoComponents":
                                node["inputs"]["batch_index"] = -cf
                                node["inputs"]["length"] = cf
                        elif node["class_type"] == "TrimAudioDuration":
                            aud_link = node["inputs"].get("audio")
                            if isinstance(aud_link, list) and len(aud_link) == 2 and prompt.get(str(aud_link[0]), {}).get("class_type") == "GetVideoComponents":
                                node["inputs"]["start_index"] = -cf_sec
                                node["inputs"]["duration"] = cf_sec
                if "keyframes" in p:
                    old_loads = set()
                    indices = p["keyframe_frames"]
                    for node_id, _ in h3_i2v_nodes:
                        inputs = prompt[node_id]["inputs"]
                        # In continue mode, frame 0 is owned by the video tail guide; never touch first_frame.
                        clear_keys = ("last_frame",) if h3_continue_mode else ("first_frame", "last_frame")
                        for key in clear_keys:
                            link = inputs.pop(key, None)
                            if isinstance(link, list) and len(link) == 2:
                                old_loads.add(str(link[0]))
                        if not p["keyframes"]:
                            continue
                        # Find the tail of any existing MiniMaxH3AddGuide chain attached to this I2V node.
                        cond_ref = [node_id, 0]
                        visited = {node_id}
                        while True:
                            nxt = next(
                                (k for k, n in prompt.items()
                                 if k not in visited and n.get("class_type") == "MiniMaxH3AddGuide"
                                 and n["inputs"].get("positive") == cond_ref),
                                None,
                            )
                            if not nxt:
                                break
                            visited.add(nxt)
                            cond_ref = [nxt, 0]
                        consumers = []
                        for cid, cnode in prompt.items():
                            for cfield, cval in cnode["inputs"].items():
                                if cval == cond_ref:
                                    consumers.append((cid, cfield))
                        vae_ref = copy.deepcopy(inputs.get("vae"))
                        used_first = False
                        used_last = False
                        current_cond = cond_ref
                        for idx, (image, frame_idx) in enumerate(zip(p["keyframes"], indices)):
                            load_id = f"h3_kf_{node_id}_{idx}"
                            while load_id in prompt:
                                load_id += "_"
                            prompt[load_id] = {"class_type": "LoadImage", "inputs": {"image": image}}
                            if not h3_continue_mode and frame_idx == 0 and not used_first:
                                inputs["first_frame"] = [load_id, 0]
                                used_first = True
                            elif frame_idx == p["length"] - 1 and not used_last:
                                inputs["last_frame"] = [load_id, 0]
                                used_last = True
                            else:
                                guide_id = f"h3_guide_{node_id}_{idx}"
                                while guide_id in prompt:
                                    guide_id += "_"
                                guide_inputs = {
                                    "positive": current_cond,
                                    "latent": [node_id, 1],
                                    "image": [load_id, 0],
                                    "frame_idx": int(frame_idx),
                                }
                                if vae_ref is not None:
                                    guide_inputs["vae"] = copy.deepcopy(vae_ref)
                                prompt[guide_id] = {"class_type": "MiniMaxH3AddGuide", "inputs": guide_inputs}
                                current_cond = [guide_id, 0]
                        if current_cond != cond_ref:
                            for cid, cfield in consumers:
                                prompt[cid]["inputs"][cfield] = current_cond
                    used = {str(v[0]) for node in prompt.values() for v in node["inputs"].values() if isinstance(v, list) and len(v) == 2}
                    for key in old_loads - used:
                        if prompt.get(key, {}).get("class_type") == "LoadImage":
                            del prompt[key]
        prompt = self._validate(prompt)
        if object_info is not None:
            prompt = adapt_seedvr2(prompt, object_info)
            prompt = adapt_save_video(prompt, object_info, crf=p.get("video_crf", 15))
            missing = sorted({node["class_type"] for node in prompt.values()} - set(object_info))
            if missing:
                raise WorkflowError("ComfyUI 缺少节点: " + ", ".join(missing) + "；请安装对应扩展并重启 ComfyUI，或导入兼容工作流", details={"missing_nodes": missing, "dependency": record.get("dependency")})
            errors = []
            for node_id, node in prompt.items():
                metadata = object_info[node["class_type"]]
                for field, spec in metadata.get("input", {}).get("required", {}).items():
                    if field not in node["inputs"]:
                        errors.append(f"{node_id}.{field} 缺少必填输入")
                for group in ("required", "optional"):
                    for field, spec in metadata.get("input", {}).get(group, {}).items():
                        value = node["inputs"].get(field)
                        choices = combo_choices(spec)
                        if choices is not None and value is not None and not isinstance(value, list) and value not in choices:
                            errors.append(f"{node_id}.{field}: {value!r} 不在服务器可选项中（模型可能尚未同步或节点版本不同）")
            if errors:
                raise WorkflowError("；".join(errors), details={"invalid_inputs": errors})
        p.update(workflow_id=ident, workflow_version=record["version"], model_snapshot=required)
        return {"prompt": prompt, "params": p, "workflow_id": ident, "workflow_version": record["version"], "required_models": required,
                "output_nodes": [key for key, node in prompt.items() if node["class_type"] in {"SaveImage", "SaveVideo", "SaveAnimatedWEBP", "VHS_VideoCombine"}],
                "workflow_hash": hashlib.sha256(json.dumps(prompt, sort_keys=True).encode()).hexdigest()}

    @staticmethod
    def _builtin(p, resolve, catalog=None):
        graph = {}
        def add(cls, **inputs):
            key = str(len(graph) + 1)
            graph[key] = {"class_type": cls, "inputs": inputs}
            return [key, 0]
        def save(image):
            add("SaveImage", images=image, filename_prefix="DrawingStudio")
            return graph
        mode = p["mode"]
        up = p.get("upscale", {})
        up = up if isinstance(up, dict) else {}
        if p.get("face_restore"):
            raise WorkflowError("基础 SDXL 工作流未连接修脸节点；请导入带 FaceDetailer/修脸节点的 API 工作流并映射参数")
        if mode == "supir":
            raise WorkflowError("SUPIR 节点版本和模型组合不同；请安装 ComfyUI-SUPIR 并导入其官方 API 工作流，映射 image/prompt/seed 等字段")
        def esrgan(image, options):
            source = image
            model = add("UpscaleModelLoader", model_name=resolve(options.get("model") or p.get("upscale_model"), "upscale_models"))
            image = add("ImageUpscaleWithModel", upscale_model=model, image=image)
            width = options.get("width", p.get("target_width"))
            if width:
                image = add("ImageScale", image=image, upscale_method="lanczos", width=int(width), height=int(options.get("height", p.get("target_height", 0))), crop="disabled")
            elif options.get("scale", p.get("scale")) is not None:
                scaled = add("ImageScaleBy", image=source, upscale_method="lanczos", scale_by=float(options.get("scale", p.get("scale"))))
                size = add("GetImageSize", image=scaled)
                image = add("ImageScale", image=image, upscale_method="lanczos", width=size, height=[size[0], 1], crop="disabled")
            return image
        if mode == "video_seedvr2":
            video = add("LoadVideo", file=_path(p.get("source_video", "")))
            components = add("GetVideoComponents", video=video)
            dit = add("SeedVR2LoadDiTModel", model=resolve(p.get("dit_model", "seedvr2_ema_3b_fp8_e4m3fn.safetensors"), "seedvr2"), device="cuda:0", blocks_to_swap=int(p.get("blocks_to_swap", 0)), swap_io_components=bool(p.get("swap_io_components", False)), offload_device="cpu", cache_model=False, attention_mode="sdpa")
            vae = add("SeedVR2LoadVAEModel", model=resolve(p.get("seedvr2_vae", "ema_vae_fp16.safetensors"), "seedvr2"), device="cuda:0", encode_tiled=True, encode_tile_size=1024, encode_tile_overlap=128, decode_tiled=True, decode_tile_size=768, decode_tile_overlap=128, tile_debug="false", offload_device="cpu", cache_model=False)
            upscaled = add("SeedVR2VideoUpscaler", image=[components[0], 0], dit=dit, vae=vae, seed=p["seed"], resolution=int(p.get("resolution", 1080)), max_resolution=int(p.get("max_resolution", 0)), batch_size=int(p.get("frame_batch_size", 5)), uniform_batch_size=True, color_correction=str(p.get("color_correction", "lab")), temporal_overlap=int(p.get("temporal_overlap", 2)), prepend_frames=int(p.get("prepend_frames", 0)), input_noise_scale=0.0, latent_noise_scale=0.0, offload_device="cpu", enable_debug=False)
            created = add("CreateVideo", images=upscaled, fps=[components[0], 2], audio=[components[0], 1])
            add("SaveVideo", video=created, filename_prefix="video/SeedVR2", format="auto", **{"format.codec": "auto"})
            return graph
        if mode in ("upscale", "seedvr2"):
            image = add("LoadImage", image=_path(p.get("image", "")))
            if int(p["batch_size"]) != 1:
                image = add("RepeatImageBatch", image=image, amount=int(p["batch_size"]))
            if mode == "seedvr2":
                dit = add("SeedVR2LoadDiTModel", model=resolve(p.get("dit_model", "seedvr2_ema_3b_fp8_e4m3fn.safetensors"), "seedvr2"), device="cuda:0", blocks_to_swap=p.get("blocks_to_swap", 32), swap_io_components=False, offload_device="cpu", cache_model=False, attention_mode="sdpa")
                vae = add("SeedVR2LoadVAEModel", model=resolve(p.get("seedvr2_vae", "ema_vae_fp16.safetensors"), "seedvr2"), device="cuda:0", encode_tiled=True, encode_tile_size=1024, encode_tile_overlap=128, decode_tiled=True, decode_tile_size=768, decode_tile_overlap=128, tile_debug="false", offload_device="cpu", cache_model=False)
                image = add("SeedVR2VideoUpscaler", image=image, dit=dit, vae=vae, seed=p["seed"], resolution=p.get("resolution", 2048), max_resolution=p.get("max_resolution", 0), batch_size=1, uniform_batch_size=True, color_correction="lab", temporal_overlap=0, prepend_frames=0, input_noise_scale=0.0, latent_noise_scale=0.0, offload_device="cpu", enable_debug=False)
            else:
                image = esrgan(image, up)
            return save(image)
        if mode in ("img2img", "inpaint") and not p.get("image"):
            raise WorkflowError("图生图 / 局部修复需要先上传或选择参考底图")
        model_ref = p.get("model") or p.get("checkpoint")
        matched_model = next(
            (m for m in (catalog or []) if model_ref in (m.get("id"), m.get("name"), m.get("filename"), m.get("comfy_name"))),
            None,
        )
        matched_kind = str((matched_model or {}).get("kind") or "")
        matched_name = str((matched_model or {}).get("filename") or (matched_model or {}).get("name") or model_ref or "")
        is_moss = (
            (matched_kind in ("diffusion_models", "unet") and "moss" in matched_name.lower())
            or (isinstance(model_ref, str) and "moss_image" in model_ref.lower())
        )
        if is_moss:
            unet_name = resolve(model_ref, "diffusion_models")
            unet = add("UNETLoader", unet_name=unet_name, weight_dtype="default")
            shift_val = float(p["shift"]) if p.get("shift") is not None else (3.5 if "sft" in unet_name.lower() else 5.0)
            p["shift"] = shift_val
            model = add("ModelSamplingAuraFlow", model=unet, shift=shift_val, sampling="flow")
            for lora in p["loras"]:
                if isinstance(lora, str):
                    lora = {"name": lora}
                if lora.get("enabled", True) is False:
                    continue
                strength = float(lora.get("strength_model", lora.get("strength", lora.get("weight", 0.8))))
                model = add("LoraLoaderModelOnly", model=model, lora_name=resolve(lora.get("id") or lora.get("name"), "loras"), strength_model=strength)
            clip = add("CLIPLoader", clip_name=resolve(p.get("clip_model") or "qwen3.5_9b_int8_convrot.safetensors", "text_encoders"), type="moss_image", device="default")
            vae = add("VAELoader", vae_name=resolve(p.get("vae") or "flux2-vae.safetensors", "vae"))
        else:
            checkpoint = add("CheckpointLoaderSimple", ckpt_name=resolve(model_ref, "checkpoints"))
            model, clip, vae = checkpoint, [checkpoint[0], 1], [checkpoint[0], 2]
            for lora in p["loras"]:
                if isinstance(lora, str):
                    lora = {"name": lora}
                if lora.get("enabled", True) is False:
                    continue
                strength = float(lora.get("strength", lora.get("weight", 1.0)))
                model = add("LoraLoader", model=model, clip=clip, lora_name=resolve(lora.get("id") or lora.get("name"), "loras"), strength_model=float(lora.get("strength_model", strength)), strength_clip=float(lora.get("strength_clip", strength)))
                clip = [model[0], 1]
            if p.get("vae"):
                vae = add("VAELoader", vae_name=resolve(p["vae"], "vae"))
        positive = add("CLIPTextEncode", text=p["prompt"], clip=clip)
        negative = add("CLIPTextEncode", text=p["negative_prompt"], clip=clip)
        source_image = None
        mask = None
        if mode in ("img2img", "inpaint") or (mode == "hires" and p.get("image")):
            source_image = add("LoadImage", image=_path(p.get("image", "")))
            if mode == "inpaint":
                if p.get("mask"):
                    mask_image = add("LoadImage", image=_path(p["mask"]))
                    mask = add("ImageToMask", image=mask_image, channel=p.get("mask_channel", "red"))
                else:
                    mask = [source_image[0], 1]
                if is_moss:
                    encoded = add("VAEEncode", pixels=source_image, vae=vae)
                    grow = int(p.get("grow_mask_by", 8))
                    if grow != 0:
                        mask = add("GrowMask", mask=mask, expand=grow, tapered_corners=True)
                    latent = add("SetLatentNoiseMask", samples=encoded, mask=mask)
                else:
                    latent = add("VAEEncodeForInpaint", pixels=source_image, vae=vae, mask=mask, grow_mask_by=p.get("grow_mask_by", 6))
            else:
                latent = add("VAEEncode", pixels=source_image, vae=vae)
            if int(p["batch_size"]) != 1:
                latent = add("RepeatLatentBatch", samples=latent, amount=int(p["batch_size"]))
        else:
            empty_cls = "EmptyFlux2LatentImage" if is_moss else "EmptyLatentImage"
            latent = add(empty_cls, width=int(p["width"]), height=int(p["height"]), batch_size=int(p["batch_size"]))
        def sample(latent, denoise, steps=None):
            return add("KSampler", model=model, positive=positive, negative=negative, latent_image=latent, seed=p["seed"], steps=int(steps or p["steps"]), cfg=float(p["cfg"]), sampler_name=p["sampler"], scheduler=p["scheduler"], denoise=float(denoise))
        latent = sample(latent, p["denoise"] if source_image else 1.0)
        if mode == "hires" or (up.get("enabled") and up.get("mode") in ("latent", "hires")):
            latent = add("LatentUpscaleBy", samples=latent, upscale_method=up.get("method", "bislerp"), scale_by=float(up.get("scale", p.get("scale", 2.0))))
            latent = sample(latent, up.get("denoise", p.get("hires_denoise", 0.35)), up.get("steps"))
        image = add("VAEDecode", samples=latent, vae=vae)
        if mode == "inpaint" and p.get("composite", True):
            image = add("ImageCompositeMasked", destination=source_image, source=image, x=0, y=0, resize_source=is_moss, mask=mask)
        if up.get("enabled") and up.get("mode", "standard") in ("standard", "esrgan", "fast"):
            image = esrgan(image, up)
        return save(image)
