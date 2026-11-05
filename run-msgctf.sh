#!/bin/sh
set -eu

cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "Create .env from .env.msgctf.example and set your participant credentials." >&2
  exit 1
fi

CTF_HOST_DATA_DIR="$(pwd -P)/data"
CODEX_AUTH_DIR="${CODEX_AUTH_DIR:-${CODEX_HOME:-$HOME/.codex}}"
export CTF_HOST_DATA_DIR CODEX_AUTH_DIR
mkdir -p "$CTF_HOST_DATA_DIR/challenges" "$CTF_HOST_DATA_DIR/tmp" "$CTF_HOST_DATA_DIR/logs" "$CTF_HOST_DATA_DIR/codex-home"
if [ ! -f "$CODEX_AUTH_DIR/auth.json" ]; then
  echo "Codex auth.json was not found. Run codex login on the host first." >&2
  exit 1
fi
chmod 700 "$CTF_HOST_DATA_DIR/codex-home"
if [ ! -f "$CTF_HOST_DATA_DIR/codex-home/auth.json" ]; then
  cp "$CODEX_AUTH_DIR/auth.json" "$CTF_HOST_DATA_DIR/codex-home/auth.json"
  chmod 600 "$CTF_HOST_DATA_DIR/codex-home/auth.json"
fi

case "${1:-}" in
  --list)
    docker compose -f compose.msgctf.yml run --build --rm agent \
      ctf-solve --platform msgctf --list
    ;;
  --no-submit)
    if ! docker image inspect ctf-sandbox >/dev/null 2>&1; then
      docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
    fi
    docker compose -f compose.msgctf.yml run --build --rm agent \
      ctf-solve --platform msgctf --challenges-dir "$CTF_HOST_DATA_DIR/challenges" \
      --max-challenges 1 --no-submit -v
    ;;
  "")
    if ! docker image inspect ctf-sandbox >/dev/null 2>&1; then
      docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
    fi
    docker compose -f compose.msgctf.yml up --build
    ;;
  *)
    echo "Usage: $0 [--list|--no-submit]" >&2
    exit 2
    ;;
esac
