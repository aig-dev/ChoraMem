.PHONY: help test race vet build proto-generate proto-check proto-lint integration verify-independence verify verify-sdks verify-worker verify-deployment verify-eval eval-live eval-matrix eval-memoryagentbench eval-personamem eval-personamem-effect eval-perma-seed eval-cupid-seed eval-anchor-v0 eval-seed-essential-delayed eval-seed-generalization verify-standalone release-gate-local release-gate

BINARY := bin/memoryd
BUF := go run github.com/bufbuild/buf/cmd/buf@v1.57.2
EVAL_PROFILE ?= $(CURDIR)/eval/profiles/v0.json
EVAL_VERCEL_RUNNER ?= $(CURDIR)/eval/typescript/bin/vercel-ai-runner.mjs
PERSONAMEM_ARGS ?= run
CUPID_ARGS ?= prepare
ANCHOR_ARGS ?= prepare
DELAYED_SEED_ARGS ?= prepare
GENERALIZATION_ARGS ?= prepare

help:
	@echo "Memory Core commands"
	@echo "  make test        - Run module tests"
	@echo "  make race        - Run race-enabled tests"
	@echo "  make vet         - Run Go static checks"
	@echo "  make build       - Build memoryd"
	@echo "  make proto-generate - Generate Go, gRPC and public Connect bindings"
	@echo "  make proto-check - Regenerate bindings and fail on drift"
	@echo "  make proto-lint  - Lint authoritative Protobuf contracts"
	@echo "  make integration - Verify PostgreSQL, MySQL 8 and the running gRPC service"
	@echo "  make verify      - Run the complete local verification"
	@echo "  make verify-sdks - 验证 Go/Python/TypeScript SDK、生成漂移与发布包"
	@echo "  make verify-worker - 验证参考 Worker、生成漂移与发布包"
	@echo "  make verify-deployment - 验证 Compose 网络、端口与必填配置"
	@echo "  make verify-eval - 无模型 key 验证固定 Harness、Omni Adapter 与六组 Eval"
	@echo "  make eval-live - 用显式 endpoint、token、model 和场景运行真实 Eval"
	@echo "  make eval-matrix - 用同一 profile 运行 OpenAI Agents + Vercel AI 四模式 Eval"
	@echo "  make eval-memoryagentbench - 独立运行固定 commit 的检索/Recollection benchmark"
	@echo "  make eval-personamem - 官方 PersonaMem-v2 文本选择题四模式评测"
	@echo "  make eval-personamem-effect - PersonaMem-v2 validation 六组因果诊断"
	@echo "  make eval-perma-seed - PERMA H1/H2 的 Seed 增量效应评测"
	@echo "  make eval-cupid-seed - CUPID 三组长期陪伴 Seed 增量效应评测"
	@echo "  make eval-anchor-v0 - ANCHOR v0 长期人格、关系适应与修复三臂评测"
	@echo "  make eval-seed-essential-delayed - Seed 必要性四臂延迟因果评测"
	@echo "  make eval-seed-generalization - 未见模式、长历史与负对照确认评测"
	@echo "  make verify-standalone - 在无父目录依赖的干净导出中运行发布门"
	@echo "  make release-gate - 在干净导出中运行完整发布门"

test:
	go test -count=1 ./...

race:
	go test -race -count=1 ./...

vet:
	go vet ./...

build:
	@mkdir -p bin
	go build -o $(BINARY) ./cmd/memoryd

proto-generate:
	$(BUF) generate --template buf.gen.yaml
	$(BUF) generate --template buf.connect.gen.yaml

proto-check:
	@tmp_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	$(BUF) generate --template buf.gen.yaml --output "$$tmp_dir"; \
	$(BUF) generate --template buf.connect.gen.yaml --output "$$tmp_dir"; \
	diff -ru gen "$$tmp_dir/gen"

proto-lint:
	$(BUF) lint

integration:
	./scripts/test-postgres.sh
	./scripts/test-mysql.sh

verify-independence:
	@if go list -deps ./... | grep -Eq '^github\.com/chorai/chorai-go(/|$$)'; then \
		echo "memory-core must not depend on Chorai internal modules" >&2; \
		exit 1; \
	fi

verify: test race vet proto-lint proto-check verify-independence build

verify-sdks:
	./scripts/verify-sdks.sh

verify-worker:
	./scripts/verify-worker.sh

verify-deployment:
	./scripts/verify-deployment.sh

verify-eval:
	./scripts/verify-eval.sh

eval-live:
	PYTHONPATH="sdk/python/src:eval/python/src" python3 -m memory_core_eval.cli

eval-matrix:
	cd eval/typescript && npm ci --ignore-scripts && npm run build
	MEMORY_EVAL_PROFILE="$(EVAL_PROFILE)" \
	MEMORY_EVAL_VERCEL_RUNNER="$(EVAL_VERCEL_RUNNER)" \
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.matrix_cli

eval-memoryagentbench:
	./scripts/run-memoryagentbench.sh

eval-personamem:
	PYTHONHASHSEED=0 PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.personamem_runner $(PERSONAMEM_ARGS)

eval-personamem-effect:
	PYTHONHASHSEED=0 PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.personamem_effect_cli $(PERSONAMEM_ARGS)

eval-perma-seed:
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.perma_seed_cli $(PERSONAMEM_ARGS)

eval-cupid-seed:
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.cupid_cli $(CUPID_ARGS)

eval-anchor-v0:
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.anchor_cli $(ANCHOR_ARGS)

eval-seed-essential-delayed:
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.delayed_seed_cli $(DELAYED_SEED_ARGS)

eval-seed-generalization:
	PYTHONPATH="sdk/python/src:eval/python/src" \
		python3 -m memory_core_eval.seed_generalization_cli $(GENERALIZATION_ARGS)

verify-standalone:
	./scripts/verify-standalone.sh

release-gate-local:
	$(MAKE) verify
	$(MAKE) integration
	$(MAKE) verify-sdks
	$(MAKE) verify-worker
	$(MAKE) verify-deployment
	$(MAKE) verify-eval

release-gate: verify-standalone
