#!/usr/bin/env python3
"""Batch-import public ComfyUI workflow JSON files into a running Drawing Studio instance.

Usage:
  export DRAW_BASE="http://127.0.0.1:8790"
  export DRAW_API_KEY="ds-your-api-key"
  python3 ops/import_workflows.py --dry-run
  python3 ops/import_workflows.py
  python3 ops/import_workflows.py moss-base.json moss-sft.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def post_workflow(base: str, key: str, body: dict) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/workflows",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main() -> int:
    default_dir = Path(__file__).resolve().parent.parent / "workflows"
    parser = argparse.ArgumentParser(description="Import workflows/*.json into Drawing Studio via POST /api/workflows")
    parser.add_argument("files", nargs="*", help="Optional workflow filenames or IDs to import (default: all *.json)")
    parser.add_argument("--dir", default=str(default_dir), help="Workflows directory")
    parser.add_argument("--base", default=os.environ.get("DRAW_BASE", os.environ.get("DRAW_BASE_URL", "http://127.0.0.1:8790")))
    parser.add_argument("--key-file", default=os.environ.get("DRAW_KEY_FILE", ""), help="Path to file containing ds-... API key")
    parser.add_argument("--dry-run", action="store_true", help="List workflows that would be imported without making HTTP calls")
    args = parser.parse_args()

    wf_dir = Path(args.dir)
    if not wf_dir.is_dir():
        print(f"Workflows directory not found: {wf_dir}", file=sys.stderr)
        return 1

    all_files = sorted(wf_dir.glob("*.json"))
    if args.files:
        wanted = set(args.files)
        selected = [
            p for p in all_files
            if p.name in wanted or p.stem in wanted or str(p) in wanted
        ]
    else:
        selected = all_files

    if not selected:
        print("No matching workflow JSON files found.", file=sys.stderr)
        return 1

    records = []
    for path in selected:
        data = json.loads(path.read_text("utf-8"))
        records.append((path, data))

    if args.dry_run:
        summary = [
            {"file": p.name, "id": d.get("id"), "name": d.get("name"), "mode": d.get("mode")}
            for p, d in records
        ]
        print(json.dumps({"count": len(summary), "workflows": summary}, ensure_ascii=False, indent=2))
        return 0

    key = os.environ.get("DRAW_API_KEY", "").strip()
    if not key and args.key_file:
        key = Path(args.key_file).read_text("utf-8").strip()
    if not key:
        print("Missing API key: set DRAW_API_KEY or pass --key-file.", file=sys.stderr)
        return 1

    imported = []
    for path, body in records:
        try:
            res = post_workflow(args.base, key, body)
            imported.append({"file": path.name, "id": res.get("id"), "version": res.get("version")})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"Failed importing {path.name}: HTTP {exc.code} {detail}", file=sys.stderr)
            return 1

    print(json.dumps({"imported": len(imported), "workflows": imported}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
