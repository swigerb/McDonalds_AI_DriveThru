#!/bin/sh

# Usage:
#   ./scripts/start.sh              # CPU mode (default)
#   ./scripts/start.sh --gpu        # DirectML GPU (Windows)
#   ./scripts/start.sh --gpu-cuda   # NVIDIA CUDA GPU (Linux)

PRODUCTION_MODE=""
GPU_MODE=""

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --production)
            PRODUCTION_MODE="--production"
            ;;
        --gpu)
            GPU_MODE="directml"
            ;;
        --gpu-cuda)
            GPU_MODE="cuda"
            ;;
    esac
done

echo 'Creating Python virtual environment and installing dependencies...'
sh scripts/load_python_env.sh

# Fix onnxruntime for GPU — faster-whisper pulls in CPU variant which conflicts
if [ -n "$GPU_MODE" ]; then
    echo ""
    echo "GPU mode: swapping onnxruntime CPU → $GPU_MODE..."
    pip uninstall onnxruntime -y 2>/dev/null
    if [ "$GPU_MODE" = "directml" ]; then
        pip install --force-reinstall --no-deps onnxruntime-directml==1.24.4 --quiet
    elif [ "$GPU_MODE" = "cuda" ]; then
        pip install --force-reinstall --no-deps onnxruntime-genai-cuda --quiet
    fi
    echo "GPU mode: onnxruntime-$GPU_MODE ready"
    echo ""
fi

if [ "$PRODUCTION_MODE" != "--production" ]; then
    echo ""
    echo "Restoring frontend npm packages"
    echo ""
    cd app/frontend
    npm install
    if [ $? -ne 0 ]; then
        echo "Failed to restore frontend npm packages"
        exit $?
    fi

    echo ""
    echo "Building frontend"
    echo ""
    npm run build
    if [ $? -ne 0 ]; then
        echo "Failed to build frontend"
        exit $?
    fi
    cd ../../
fi

echo ""
echo "Starting backend"
echo ""

if [ "$PRODUCTION_MODE" = "--production" ]; then
    HOST="${HOST:-0.0.0.0}"
    PORT="${PORT:-8000}"
    LOG_LEVEL="${LOG_LEVEL:-info}"
    export RUNNING_IN_PRODUCTION=true
    cd app/backend
    python -m gunicorn app:create_app \
        -b "${HOST}:${PORT}" \
        --worker-class aiohttp.GunicornWebWorker \
        --workers 2 \
        --timeout 120 \
        --keep-alive 65 \
        --access-logfile - \
        --log-level "${LOG_LEVEL}"
else
    cd app/backend
    python app.py
fi

if [ $? -ne 0 ]; then
    echo "Failed to start backend"
    exit $?
fi