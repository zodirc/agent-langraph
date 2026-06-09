# 本地默认：HTTPS + 热更新
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE_PROD = docker compose -f docker-compose.yml

.PHONY: init up down restart logs ps build prod prod-down help

help: ## 显示命令
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-12s %s\n", $$1, $$2}'

init: ## 生成/补全 .env（自动检测局域网 IP、密钥等）
	@bash scripts/setup.sh

up: ## 启动 HTTPS + 热更新 → https://localhost:8080/
	$(COMPOSE_DEV) up -d --build

down: ## 停止
	$(COMPOSE_DEV) down --remove-orphans

restart: ## 重启 agent（改 .env / config 后，会重新注入环境变量）
	$(COMPOSE_DEV) up -d --force-recreate agent

logs: ## agent 日志
	$(COMPOSE_DEV) logs -f agent

ps: ## 状态
	$(COMPOSE_DEV) ps

build: ## 重建镜像（改 requirements.txt 后）
	$(COMPOSE_DEV) build --no-cache agent
	$(COMPOSE_DEV) up -d agent

prod: ## 生产：HTTPS，无热更新
	$(COMPOSE_PROD) up -d --build

prod-down:
	$(COMPOSE_PROD) down --remove-orphans
