#!/bin/bash
set -euo pipefail

# Ensure Homebrew paths for both Apple Silicon (/opt/homebrew) and Intel Mac (/usr/local)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:${PATH}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/backend/main.py" ]; then
  PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../backend/main.py" ]; then
  PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
else
  echo "[Error] Cannot locate Drawing Studio backend/ directory near: $SCRIPT_DIR"
  exit 1
fi

cd "$PROJECT_DIR"

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "============================================================"
  echo "  Drawing Studio requires Python 3.10+ (3.12 recommended)"
  echo "  Please install Python and ffmpeg via Homebrew first:"
  echo "    brew install python@3.12 ffmpeg"
  echo "============================================================"
  printf "Press Enter to exit..."
  read -r _
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[Hint] ffmpeg not found in PATH. Video concat & frame extraction require ffmpeg."
  echo "       You can install it anytime with: brew install ffmpeg"
fi

if [ ! -d "$PROJECT_DIR/web/dist" ] && [ ! -f "$PROJECT_DIR/web/dist/index.html" ]; then
  echo "[Error] Missing web/dist/index.html. Please use the prebuilt/macOS release bundle or run 'cd web && npm ci && npm run build'."
  printf "Press Enter to exit..."
  read -r _
  exit 1
fi

VENV_DIR="$PROJECT_DIR/.venv"
DEPS_STAMP="$VENV_DIR/.deps-1.0.43"

if [ ! -d "$VENV_DIR" ]; then
  echo "[Setup] Creating Python virtual environment with $PYTHON_BIN ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [ ! -f "$DEPS_STAMP" ]; then
  echo "[Setup] Installing Python dependencies (first launch may take ~30s)..."
  "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
  "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
  touch "$DEPS_STAMP"
fi

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/config/private"
chmod 700 "$PROJECT_DIR/config/private"

PORT="${PORT:-8790}"
HOST="${HOST:-127.0.0.1}"

export DRAWING_DATA="$PROJECT_DIR/data"
export DRAWING_COLAB_CREDENTIALS="$PROJECT_DIR/config/private/colab.json"
export DRAWING_DRIVE_CREDENTIALS="$PROJECT_DIR/config/private/drive.json"
export PYTHONUNBUFFERED=1

echo "============================================================"
echo "  Drawing Studio v1.0.43 (macOS Native Launcher)"
echo "  Architecture : $(uname -m)"
echo "  Listen URL   : http://${HOST}:${PORT}"
echo "  Data Dir     : $DRAWING_DATA"
if [ -f "$DRAWING_DATA/admin-password" ]; then
  echo "  Admin Pass   : see $DRAWING_DATA/admin-password"
else
  echo "  Admin Pass   : generated on first boot -> $DRAWING_DATA/admin-password"
fi
echo "============================================================"

# Open browser automatically once /api/health responds
(
  i=0
  while [ "$i" -lt 40 ]; do
    if command -v curl >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      echo ""
      echo "[Ready] Drawing Studio is running at http://127.0.0.1:${PORT}"
      if [ -f "$DRAWING_DATA/admin-password" ]; then
        echo "[Login] Password file: $DRAWING_DATA/admin-password"
      fi
      if command -v open >/dev/null 2>&1; then
        open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
      fi
      break
    fi
    i=$((i + 1))
    sleep 0.5
  done
) &

exec "$VENV_DIR/bin/uvicorn" backend.main:app --host "$HOST" --port "$PORT"
