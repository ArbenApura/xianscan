#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "================================================================"
echo "  XIANSCAN -- ALL-IN-ONE AUTOMATED LAUNCHER"
echo "================================================================"
echo ""

# 1. CHECK PYTHON
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 was not found. Please install Python 3.10+."
    exit 1
fi

# 2. SETUP ML VIRTUAL ENVIRONMENT
if [ ! -d "ml/.venv" ]; then
    echo "[*] Creating Python virtual environment in ml/.venv..."
    python3 -m venv ml/.venv
    ml/.venv/bin/pip install --upgrade pip
    ml/.venv/bin/pip install -r ml/requirements.txt
fi

# 3. DOWNLOAD ML WEIGHTS IF MISSING
if [ ! -f "ml/models/comictextdetector.pt.onnx" ]; then
    echo "[*] Downloading required ML model weights..."
    ml/.venv/bin/python ml/scripts/download_models.py
fi

# 4. SETUP WEB ENVIRONMENT & BUILD
if [ ! -d "web/node_modules" ]; then
    echo "[*] Installing web application dependencies..."
    cd web && npm install && cd ..
fi

if [ ! -f "web/build/index.js" ]; then
    echo "[*] Production build not found. Building web application..."
    cd web && npm run build && cd ..
fi

echo ""
echo "================================================================"
echo "  [+] Starting ML Sidecar on http://127.0.0.1:8123"
echo "  [+] Starting Web App on    http://localhost:8124"
echo "================================================================"
echo ""

# TRAP CTRL+C TO SHUTDOWN BOTH
cleanup() {
    echo ""
    echo "[*] Shutting down services..."
    kill $(jobs -p) 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

(cd ml && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8123) &
(cd web && npm run preview) &

wait
