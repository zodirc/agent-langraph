#!/bin/sh
# Seed embedding weights into the persistent /data volume on first run.
set -e
CACHE_DIR="${LOCAL_EMBEDDING_CACHE_DIR:-/data/models/sentence-transformers}"
BAKED_DIR="/app/models-baked/sentence-transformers"
mkdir -p "$CACHE_DIR"
if [ -d "$BAKED_DIR" ]; then
  if [ -z "$(ls -A "$CACHE_DIR" 2>/dev/null)" ]; then
    echo "[entrypoint] seeding embedding model into $CACHE_DIR"
    cp -a "$BAKED_DIR/." "$CACHE_DIR/"
  fi
fi
exec "$@"
