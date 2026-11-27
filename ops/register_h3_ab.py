#!/usr/bin/env python3
"""Register updated Ref2VA + new Route A (keyframes) & Route B (continue) H3 workflows into Draw API.

Usage:
  python3 ops/register_h3_ab.py --dry-run
  python3 ops/register_h3_ab.py [workflows_dir]
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

REQUIRED_FL2VA_MODELS = {
    "diffusion_models": [
        {
            "filename": "minimax_h3_fl2va_bf16.safetensors",
            "precision": "BF16",
            "source": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_bf16.safetensors",
        },
        {
            "filename": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "precision": "INT8",
            "source": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        },
    ],
    "loras": [
        {
            "filename": "minimax_h3_fl2v_turbo_8step_v1.2_768p_comfyui_bf16.safetensors",
            "precision": "BF16+INT8 共用",
            "source": "https://huggingface.co/lightx2v/Minimax-h3-Turbo/resolve/main/minimax_h3_fl2v_turbo_8step_v1.2_768p_comfyui_bf16.safetensors",
        },
    ],
}


def req(base: str, key: str, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def main():
    parser = argparse.ArgumentParser(description="Register H3 Ref2VA + Route A/B workflows into Draw")
    parser.add_argument("workflows_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent / "workflows"))
    parser.add_argument("--base", default=os.environ.get("DRAW_BASE_URL", "http://127.0.0.1:8790"))
    parser.add_argument("--key-file", default=os.environ.get("DRAW_KEY_FILE", ""))
    parser.add_argument("--dry-run", action="store_true", help="Print workflows and required models without POSTing")
    args = parser.parse_args()

    wf_dir = Path(args.workflows_dir)
    files = sorted(wf_dir.glob("h3-*.json"))
    if not files:
        raise SystemExit(f"找不到任何 h3-*.json: {wf_dir}")

    summary = []
    for path in files:
        body = json.loads(path.read_text("utf-8"))
        unet = next((n["inputs"]["unet_name"] for n in body["workflow"].values() if n["class_type"] == "UNETLoader"), "")
        summary.append({"id": body["id"], "name": body["name"], "unet": unet, "file": path.name})

    if args.dry_run:
        print(json.dumps({"workflows": summary, "required_fl2va_models": REQUIRED_FL2VA_MODELS}, ensure_ascii=False, indent=2))
        return

    key = os.environ.get("DRAW_API_KEY", "").strip()
    if not key and args.key_file:
        key = Path(args.key_file).read_text("utf-8").strip()
    if not key:
        raise SystemExit("请设置环境变量 DRAW_API_KEY 或通过 --key-file 指定 API Key 文件路径")
    models = req(args.base, key, "GET", "/api/models") or []
    present = {m.get("filename") or m.get("name") for m in models}
    missing = []
    for kind, items in REQUIRED_FL2VA_MODELS.items():
        for item in items:
            if item["filename"] not in present:
                missing.append({"kind": kind, **item})

    saved = []
    for path in files:
        body = json.loads(path.read_text("utf-8"))
        rec = req(args.base, key, "POST", "/api/workflows", body)
        saved.append({"id": rec["id"], "name": rec["name"], "version": rec.get("version")})

    print(json.dumps({"registered": saved, "missing_fl2va_models": missing}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
