#!/usr/bin/env bash
# 初始化 .env：最少只需后续填写 ANTHROPIC_API_KEY
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-.env}"
EXAMPLE="${ROOT}/.env.example"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "missing .env.example" >&2
  exit 1
fi

# 主网卡 IP（供局域网 HTTPS）；失败则仅 localhost
LAN_IP=""
if command -v hostname >/dev/null 2>&1; then
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' || true)"
fi

if [[ -n "$LAN_IP" && "$LAN_IP" != "127.0.0.1" ]]; then
  PUBLIC_DOMAIN="localhost, ${LAN_IP}"
  DEFAULT_SNI="$LAN_IP"
else
  PUBLIC_DOMAIN="localhost"
  DEFAULT_SNI="localhost"
fi

SECRET="$(openssl rand -base64 32 2>/dev/null | tr -d '\n' || head -c 32 /dev/urandom | base64 | tr -d '\n')"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE" "$ENV_FILE"
  echo "created $ENV_FILE from .env.example"
else
  echo "using existing $ENV_FILE (will merge missing keys only)"
fi

set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  printf '\n%s=%s\n' "$key" "$val" >>"$ENV_FILE"
}

set_kv "COMPOSE_FILE" "docker-compose.yml:docker-compose.dev.yml"
set_kv "HOST_PORT" "8080"
set_kv "HTTP_PORT" "8081"
set_kv "AUTH_ENABLED" "false"
set_kv "ADMIN_PASSWORD" "admin"
set_kv "APP_SECRET_KEY" "$SECRET"
set_kv "PUBLIC_DOMAIN" "$PUBLIC_DOMAIN"
set_kv "DEFAULT_SNI" "$DEFAULT_SNI"
set_kv "MODEL_PROVIDER" "anthropic"
set_kv "MODEL_ENABLED" "true"

echo ""
echo "=== setup done ==="
echo "  1. Edit $ENV_FILE → 填写 API Key（可多个厂商）；设置 MODEL_PROVIDER 选择当前厂商"
echo "  2. make up"
echo ""
echo "  平台:     https://localhost:8080/"
echo "  对话:     https://localhost:8080/chat"
if [[ "$DEFAULT_SNI" != "localhost" ]]; then
  echo "  LAN:      https://${DEFAULT_SNI}:8080/"
  echo "  Health:   http://${DEFAULT_SNI}:8081/health"
fi
if grep -q '^AUTH_ENABLED=false' "$ENV_FILE" 2>/dev/null || ! grep -q '^AUTH_ENABLED=true' "$ENV_FILE" 2>/dev/null; then
  echo "  Auth:     off (set AUTH_ENABLED=true + ADMIN_PASSWORD to enable)"
else
  echo "  Auth:     /login admin <ADMIN_PASSWORD>"
fi
echo ""
