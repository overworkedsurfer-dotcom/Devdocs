# docshelf: run `make` to list the targets.

# Your knowledge folder: everything docshelf serves comes from here.
KNOWLEDGE ?= $(or $(DOCSHELF_DIR),$(HOME)/knowledge)
PORT      ?= 8000
# Address to listen on. 0.0.0.0 lets other machines (and their browsers) in.
HOST      ?= 127.0.0.1
BIND      ?= 127.0.0.1
IMAGE     ?= docshelf
CONTAINER ?= docshelf
VOLUME    ?= docshelf-index
# Fixed API token for the extension; generated and kept if unset.
TOKEN     ?=
# For search and crawl targets.
Q         ?=
URL       ?=

UV := DOCSHELF_DIR="$(KNOWLEDGE)" uv run --quiet docshelf
DOCKER_USER := $(shell id -u):$(shell id -g)

.DEFAULT_GOAL := help

##@ Run locally (uv)

setup: ## Install dependencies
	uv sync

serve: ## Run the MCP server over stdio (for clients that launch it)
	$(UV) serve --transport stdio

serve-http: ## Run at http://localhost:8000/mcp with the extension API (HOST=0.0.0.0 for your LAN)
	$(UV) serve --transport http --host $(HOST) --port $(PORT)

token: ## Print the token to paste into the extension
	@$(UV) token

search: ## Search the folder: make search Q="rolling back"
	$(UV) search "$(Q)"

ls: ## List the top of the folder
	$(UV) ls

crawl: ## Save a website into the folder: make crawl URL=https://docs.example.com/guide/
	$(if $(URL),,$(error Give a start page: make crawl URL=https://docs.example.com/guide/))
	$(UV) crawl "$(URL)"

reindex: ## Rebuild the search index from the folder
	$(UV) reindex

opencode-config: ## Print an OpenCode config that starts docshelf from this repo (see docs/opencode.md)
	@uv run --quiet python -c 'import json, sys; print(json.dumps({"$$schema": "https://opencode.ai/config.json", "mcp": {"docshelf": {"type": "local", "command": [sys.argv[1], "run", "--quiet", "--directory", sys.argv[2], "docshelf"], "environment": {"DOCSHELF_DIR": sys.argv[3]}, "enabled": True, "timeout": 20000}}}, indent=2))' \
		"$(or $(shell command -v uv),uv)" "$(CURDIR)" "$(abspath $(KNOWLEDGE))"

inspector: ## Try the tools in the MCP Inspector (needs Node.js)
	DOCSHELF_DIR="$(KNOWLEDGE)" npx @modelcontextprotocol/inspector uv run --quiet docshelf

##@ Run in Docker

up: docker-build docker-run ## Build and start at http://localhost:8000/mcp (BIND=0.0.0.0 for your LAN)

down: docker-stop ## Stop the server

docker-build: ## Build the image
	docker build -t $(IMAGE) .

docker-run: ## Start the server in the background, serving KNOWLEDGE (default ~/knowledge)
	@mkdir -p "$(KNOWLEDGE)"
	-@docker rm -f $(CONTAINER) >/dev/null 2>&1
	docker run -d --name $(CONTAINER) --restart unless-stopped --user $(DOCKER_USER) \
		-p $(BIND):$(PORT):8000 -v "$(KNOWLEDGE)":/knowledge -v $(VOLUME):/data \
		$(if $(TOKEN),-e DOCSHELF_TOKEN="$(TOKEN)") $(IMAGE)
	@echo "docshelf: http://localhost:$(PORT)/mcp, serving $(KNOWLEDGE)"
	@echo "Extension token: make docker-token"

docker-stop: ## Stop and remove the server container
	docker rm -f $(CONTAINER)

docker-logs: ## Follow the server logs
	docker logs -f $(CONTAINER)

docker-token: ## Print the running server's token for the extension
	@docker exec $(CONTAINER) docshelf token

docker-crawl: ## Crawl from the running server: make docker-crawl URL=https://...
	$(if $(URL),,$(error Give a start page: make docker-crawl URL=https://docs.example.com/guide/))
	docker exec $(CONTAINER) docshelf crawl "$(URL)"

docker-stdio: ## Run over stdio in Docker, as an MCP client config would
	docker run -i --rm --user $(DOCKER_USER) -v "$(KNOWLEDGE)":/knowledge -v $(VOLUME):/data \
		$(IMAGE) serve --transport stdio

##@ Development

test: ## Run the tests
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check src tests
	uv run ruff format --check src tests

format: ## Format the code and apply lint fixes
	uv run ruff format src tests
	uv run ruff check --fix src tests

clean: ## Delete the virtualenv and caches (your folder and index are untouched)
	rm -rf .venv .pytest_cache .ruff_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target> [VAR=value]\n"} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: setup serve serve-http token search ls crawl reindex opencode-config inspector up down \
	docker-build docker-run docker-stop docker-logs docker-token docker-crawl docker-stdio \
	test lint format clean help
