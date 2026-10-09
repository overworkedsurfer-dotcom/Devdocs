# devdocs-mcp: run `make` to list the targets.

IMAGE     ?= devdocs-mcp
CONTAINER ?= devdocs-mcp
VOLUME    ?= devdocs-mcp-data
PORT      ?= 8000
# Docs to download, e.g. make install-docs DOCS="python javascript react"
DOCS      ?=
# Docs the Docker server installs in the background on start.
PRELOAD   ?=
# Docs to bake into the image at build time.
BAKE      ?=
# A query, for the catalog and search targets.
Q         ?=

UV := uv run --quiet devdocs-mcp

.DEFAULT_GOAL := help

##@ Local (uv)

setup: ## Install dependencies
	uv sync

serve: ## Run the MCP server over stdio (for clients that launch it)
	$(UV) serve --transport stdio

serve-http: ## Run the MCP server at http://localhost:8000/mcp (change with PORT=)
	$(UV) serve --transport http --port $(PORT)

install-docs: ## Download docs for offline use: make install-docs DOCS="python react"
	$(if $(DOCS),,$(error Name the docs: make install-docs DOCS="python javascript"))
	$(UV) install $(DOCS)

list: ## List installed docs
	$(UV) list

catalog: ## List docs DevDocs offers: make catalog Q=python
	$(UV) catalog $(Q)

search: ## Search installed docs from the shell: make search Q=getcwd
	$(UV) search "$(Q)"

update: ## Update installed docs that DevDocs has rebuilt
	$(UV) update

inspector: ## Try the tools in the MCP Inspector (needs Node.js)
	npx @modelcontextprotocol/inspector uv run --quiet devdocs-mcp

##@ Docker

up: docker-build docker-run ## Build the image and start the server at http://localhost:8000/mcp

down: docker-stop ## Stop the server

docker-build: ## Build the image (bake docs in with BAKE="python javascript")
	docker build -t $(IMAGE) --build-arg DOCS="$(BAKE)" .

docker-run: ## Start the HTTP server in the background (add PRELOAD="python react")
	-@docker rm -f $(CONTAINER) >/dev/null 2>&1
	docker run -d --name $(CONTAINER) --restart unless-stopped \
		-p 127.0.0.1:$(PORT):8000 -v $(VOLUME):/data \
		-e DEVDOCS_PRELOAD="$(PRELOAD)" $(IMAGE)
	@echo "MCP server: http://localhost:$(PORT)/mcp"

docker-stop: ## Stop and remove the server container
	docker rm -f $(CONTAINER)

docker-logs: ## Follow the server logs
	docker logs -f $(CONTAINER)

docker-stdio: ## Run over stdio, as an MCP client config would
	docker run -i --rm -v $(VOLUME):/data $(IMAGE) serve --transport stdio

docker-install-docs: ## Download docs into the Docker volume: make docker-install-docs DOCS="python"
	$(if $(DOCS),,$(error Name the docs: make docker-install-docs DOCS="python javascript"))
	docker run --rm -v $(VOLUME):/data $(IMAGE) install $(DOCS)

docker-list: ## List docs installed in the Docker volume
	docker run --rm -v $(VOLUME):/data $(IMAGE) list

##@ Development

test: ## Run the tests
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check src tests
	uv run ruff format --check src tests

format: ## Format the code and apply lint fixes
	uv run ruff format src tests
	uv run ruff check --fix src tests

clean: ## Delete the virtualenv and caches (installed docs are kept)
	rm -rf .venv .pytest_cache .ruff_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target> [VAR=value]\n"} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: setup serve serve-http install-docs list catalog search update inspector \
	up down docker-build docker-run docker-stop docker-logs docker-stdio \
	docker-install-docs docker-list test lint format clean help
