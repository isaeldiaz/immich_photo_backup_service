#!/usr/bin/env bash
# update-ml-models.sh — download or refresh ML models for immich_machine_learning
#
# Usage:
#   sudo ./update-ml-models.sh                        # download configured models, keep old ones
#   sudo ./update-ml-models.sh --clean                # remove all cached models first
#   sudo ./update-ml-models.sh --clip <model>         # override CLIP model name
#   sudo ./update-ml-models.sh --face <model>         # override facial recognition model name
#
# Models are read from the running immich_server system config by default.
# Pass --clip / --face to override (useful before starting a fresh stack).
#
# Examples:
#   sudo ./update-ml-models.sh
#   sudo ./update-ml-models.sh --clean
#   sudo ./update-ml-models.sh --clip ViT-L-14__openai --face buffalo_l

set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-compose.yml"
ML_CONTAINER="immich_machine_learning"
IMMICH_API="http://localhost:2283"

# -- defaults (overridden by flags or live config) --
CLIP_MODEL=""
FACE_MODEL=""
CLEAN=false

# -- parse args --
while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)   CLEAN=true; shift ;;
        --clip)    CLIP_MODEL="$2"; shift 2 ;;
        --face)    FACE_MODEL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -- resolve cache volume path from running container --
CACHE_DIR=$(docker inspect "$ML_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/cache"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)

if [[ -z "$CACHE_DIR" ]]; then
    echo "ERROR: Could not determine cache path — is $ML_CONTAINER running?"
    exit 1
fi

echo "Cache volume: $CACHE_DIR"

# -- fetch live model config from Immich if not overridden --
fetch_model_config() {
    local api_key
    api_key=$(grep -o '"api_key"[[:space:]]*:[[:space:]]*"[^"]*"' \
        "$(dirname "$COMPOSE_FILE")/config.json" | head -1 | grep -o '"[^"]*"$' | tr -d '"')

    if [[ -z "$api_key" ]]; then
        echo "WARN: Could not read API key from config.json — skipping live config fetch"
        return
    fi

    local response
    response=$(curl -sf -H "x-api-key: $api_key" "$IMMICH_API/api/system-config" 2>/dev/null || true)

    if [[ -z "$response" ]]; then
        echo "WARN: Could not reach Immich API — using defaults or provided model names"
        return
    fi

    [[ -z "$CLIP_MODEL" ]] && \
        CLIP_MODEL=$(echo "$response" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['machineLearning']['clip']['modelName'])" 2>/dev/null || true)

    [[ -z "$FACE_MODEL" ]] && \
        FACE_MODEL=$(echo "$response" | python3 -c \
            "import sys,json; print(json.load(sys.stdin)['machineLearning']['facialRecognition']['modelName'])" 2>/dev/null || true)
}

fetch_model_config

# -- fallbacks if API unreachable and no flags given --
CLIP_MODEL="${CLIP_MODEL:-ViT-B-32__openai}"
FACE_MODEL="${FACE_MODEL:-buffalo_l}"

echo "CLIP model:             $CLIP_MODEL"
echo "Facial recognition:     $FACE_MODEL"

# -- optionally wipe cache --
if [[ "$CLEAN" == true ]]; then
    echo ""
    echo "Cleaning model cache..."
    find "$CACHE_DIR" -mindepth 1 -delete
    echo "Cache cleared."
fi

# -- check huggingface_hub --
if ! python3 -c "from huggingface_hub import snapshot_download" 2>/dev/null; then
    echo "Installing huggingface_hub..."
    pip install -q huggingface_hub
fi

# -- resolve model cache subdirs from the running container --
# The ML image uses subdirectories per model type (e.g. /cache/clip/<model>).
# Query the container directly so this script stays correct across image upgrades.
resolve_cache_subdirs() {
    docker exec "$ML_CONTAINER" python3 - << PYEOF 2>/dev/null || true
from immich_ml.models.clip.visual import OpenClipVisualEncoder
from immich_ml.models.facial_recognition.detection import FaceDetector
import json, sys

clip = OpenClipVisualEncoder("$CLIP_MODEL")
face = FaceDetector("$FACE_MODEL")
print(json.dumps({"clip": str(clip.cache_dir), "face": str(face.cache_dir)}))
PYEOF
}

SUBDIR_JSON=$(resolve_cache_subdirs)

if [[ -n "$SUBDIR_JSON" ]]; then
    CLIP_CACHE=$(echo "$SUBDIR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['clip'])")
    FACE_CACHE=$(echo "$SUBDIR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['face'])")
    # Translate container paths to host paths via the volume mount
    CLIP_CACHE_HOST="${CACHE_DIR}${CLIP_CACHE#/cache}"
    FACE_CACHE_HOST="${CACHE_DIR}${FACE_CACHE#/cache}"
else
    # Fallback: flat layout (older image versions)
    CLIP_CACHE_HOST="$CACHE_DIR"
    FACE_CACHE_HOST="$CACHE_DIR"
fi

echo "CLIP cache path:        $CLIP_CACHE_HOST"
echo "Face cache path:        $FACE_CACHE_HOST"

# -- download --
echo ""
echo "Downloading models..."

python3 - << EOF
from huggingface_hub import snapshot_download
from pathlib import Path

downloads = [
    ("immich-app/$CLIP_MODEL", "$CLIP_CACHE_HOST"),
    ("immich-app/$FACE_MODEL", "$FACE_CACHE_HOST"),
]

for repo, local_dir in downloads:
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    print(f"  -> {repo} into {local_dir}")
    snapshot_download(
        repo,
        cache_dir=local_dir,
        local_dir=local_dir,
        ignore_patterns=["*.armnn", "*.rknn"],
    )
    print(f"     done.")
EOF

# -- restart ML container --
echo ""
echo "Restarting $ML_CONTAINER ..."
docker compose -f "$COMPOSE_FILE" restart immich-machine-learning

echo ""
echo "Waiting for ML service to become ready..."
for i in $(seq 1 15); do
    ML_IP=$(docker inspect "$ML_CONTAINER" \
        --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null | head -1)
    if [[ -n "$ML_IP" ]] && curl -sf "http://$ML_IP:3003/ping" >/dev/null 2>&1; then
        echo "ML service is ready."
        break
    fi
    sleep 2
done

echo ""
echo "Done. Run a smart search to verify."
