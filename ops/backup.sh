#!/bin/bash
set -euo pipefail
STUDIO_DIR="${STUDIO_DIR:-/opt/drawing-studio}"
BACKUP_ROOT="${BACKUP_ROOT:-$STUDIO_DIR/backups}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DIR="$BACKUP_ROOT/drawing-studio-$STAMP"
mkdir -p "$DIR/workflows"
chmod 700 "$DIR"
cp -a "$STUDIO_DIR/compose.yaml" "$DIR/compose.yaml"
[ -f "$STUDIO_DIR/data/runtime.json" ] && cp -a "$STUDIO_DIR/data/runtime.json" "$DIR/runtime.json" || true
[ -d "$STUDIO_DIR/data/workflows" ] && cp -a "$STUDIO_DIR/data/workflows/." "$DIR/workflows/" || true
# Online sqlite backup (container is running; do not copy wal hot)
python3 - << PY
import sqlite3, sys
src_path = "$STUDIO_DIR/data/studio.sqlite3"
dst_path = "$DIR/studio.sqlite3"
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
src.backup(dst)
dst.close(); src.close()
con = sqlite3.connect(dst_path)
row = con.execute("PRAGMA integrity_check").fetchone()
print("integrity_check", row[0])
if row[0] != "ok":
    sys.exit(1)
print("rows", {k: n for k,n in con.execute("SELECT kind, count(*) FROM records GROUP BY kind")})
PY
# Record current image/compose for rollback
{ echo "stamp=$STAMP"; echo "dir=$DIR"; docker inspect drawing-studio --format 'image={{.Config.Image}} id={{.Image}} status={{.State.Status}}' 2>/dev/null || true; cat "$STUDIO_DIR/compose.yaml"; } > "$DIR/live-state.txt"
echo "$DIR"
