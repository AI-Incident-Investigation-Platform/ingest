.PHONY: help config check lint-fix test test-integration test-all run build secret deploy up down ps logs

UV := uv

CLUSTER := aiip
CONTEXT := k3d-$(CLUSTER)
KUBECTL := kubectl --context $(CONTEXT) -n apps
IMAGE := aiip-ingest:$(shell date +%Y%m%d%H%M%S)
ON_HOST := CACHE__HOST=localhost PROJECTS__URL=http://localhost:8003 \
           AUTH__URL=http://localhost:8001 TRACING__OTLP_ENDPOINT=http://localhost:4318 \
           RABBITMQ__URL=$$(sed -n 's|^url = "\(amqp://[^@]*@\)[^:/]*\(.*\)"|\1localhost\2|p' config.toml)

help:
	@echo "config, check, lint-fix, test, test-integration, test-all, run, build, up, down, ps, logs"

config:
	@test ! -e config.toml || { echo "config.toml уже есть"; exit 1; }
	cp config.toml.example config.toml
	@echo "заполните config.toml данными из make -C ../infra service name=ingest и служебным секретом auth"

check:
	$(UV) run ruff format --check src tests
	$(UV) run ruff check src tests
	$(UV) run mypy src tests

lint-fix:
	$(UV) run ruff format src tests
	$(UV) run ruff check --fix src tests

test:
	$(UV) run pytest tests/unit

test-integration:
	$(UV) run pytest tests/integration

test-all:
	$(UV) run pytest tests

run:
	@test -f config.toml || { echo "нужен config.toml: make config"; exit 1; }
	@nc -z localhost 6379 2>/dev/null || { echo "нужен make -C ../infra forward"; exit 1; }
	$(ON_HOST) $(UV) run python -m src

build:
	docker build -q -t $(IMAGE) . >/dev/null
	k3d image import $(IMAGE) -c $(CLUSTER)

secret:
	@test -f config.toml || { echo "нужен config.toml: make config"; exit 1; }
	@$(KUBECTL) create secret generic ingest --from-file=config.toml --dry-run=client -o yaml | $(KUBECTL) apply -f - >/dev/null

deploy:
	kubectl kustomize k8s | sed 's|aiip-ingest:latest|$(IMAGE)|' | kubectl --context $(CONTEXT) apply -f -
	@$(KUBECTL) rollout status deploy/ingest --timeout=150s || { $(KUBECTL) rollout undo deploy/ingest; exit 1; }

up: build secret deploy

down:
	$(KUBECTL) delete -k k8s --ignore-not-found

ps:
	$(KUBECTL) get pods -l app=ingest -o wide

logs:
	$(KUBECTL) logs -l app=ingest --prefix --tail=100 -f
