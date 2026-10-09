# devdocs-mcp

A local [DevDocs](https://devdocs.io) clone, served as an [MCP](https://modelcontextprotocol.io) server. Your AI assistant gets fast, offline search and reading across the API docs for hundreds of languages, frameworks and libraries: Python, JavaScript, React, Rust, Go, PostgreSQL and many more.

It uses the same data and search ranking as devdocs.io. Docs are downloaded once into a local SQLite database and served from there.

## Quick start

Pick one:

**uv** (needs [uv](https://docs.astral.sh/uv/)):

```sh
make setup                                  # uv sync
make install-docs DOCS="python javascript"  # optional: docs are also fetched on first use
make serve-http                             # http://localhost:8000/mcp
```

**Docker:**

```sh
make up PRELOAD="python javascript"         # build + run at http://localhost:8000/mcp
```

**Without make:**

```sh
uv run devdocs-mcp                                          # stdio
uv run devdocs-mcp serve --transport http --port 8000       # http://localhost:8000/mcp

docker build -t devdocs-mcp .
docker run -d --name devdocs-mcp -p 127.0.0.1:8000:8000 -v devdocs-mcp-data:/data devdocs-mcp
```

**Without cloning:**

```sh
uvx --from git+https://github.com/overworkedsurfer-dotcom/Devdocs devdocs-mcp
```

Run `make` to list every target.

## Connect a client

### Claude Code

```sh
# The server over HTTP (make serve-http or make up)
claude mcp add --transport http devdocs http://localhost:8000/mcp

# Or let Claude Code launch it over stdio
claude mcp add devdocs -- uv run --quiet --directory /path/to/Devdocs devdocs-mcp
```

### Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "devdocs": {
      "command": "uv",
      "args": ["run", "--quiet", "--directory", "/path/to/Devdocs", "devdocs-mcp"]
    }
  }
}
```

If Claude Desktop can't find `uv`, use its full path (`which uv`). To run it in Docker instead:

```json
{
  "mcpServers": {
    "devdocs": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "devdocs-mcp-data:/data",
               "devdocs-mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

### Cursor, VS Code and other HTTP clients

Point the client at `http://localhost:8000/mcp`:

```jsonc
// Cursor: ~/.cursor/mcp.json
{ "mcpServers": { "devdocs": { "url": "http://localhost:8000/mcp" } } }

// VS Code: .vscode/mcp.json
{ "servers": { "devdocs": { "type": "http", "url": "http://localhost:8000/mcp" } } }
```

## Tools

| Tool | What it does |
| --- | --- |
| `search_docs` | Search entries (functions, classes, methods, guides) by name, ranked like devdocs.io: exact matches first, then fuzzy ones (`ospj` finds `os.path.join()`). |
| `read_page` | Read a page as Markdown. A `#fragment` path (`library/os#os.getcwd`) returns just that entry's section. Long pages come back in chunks. |
| `search_content` | Full-text search of page contents, for when you don't know the API name. Covers docs installed offline, plus pages already read. |
| `list_entries` | Browse a doc's table of contents, like the devdocs.io sidebar. |
| `list_docs` | List what DevDocs offers and what's installed. |
| `install_docs` | Download docs for offline use, or update ones DevDocs has rebuilt. |
| `remove_docs` | Delete installed docs. |

Docs can be named by slug (`python~3.12`), by name (`python`, which picks the installed version or else the newest), by alias (`js`, `py`, `ts`), or by name and version (`python 3.11`).

A doc that isn't installed yet is fetched the first time a tool names it. That fetch gets only its index, which is small: pages are downloaded as they're read, then cached. `install_docs` (or `make install-docs`) downloads every page instead, so the doc works fully offline and `search_content` covers all of it.

## Command line

The same features work from a shell, which is handy for scripts and for checking what the model will see:

```sh
devdocs-mcp catalog python             # list available docs and versions
devdocs-mcp install python~3.12 react  # download for offline use (--index-only for just the index)
devdocs-mcp list                       # list installed docs
devdocs-mcp search getcwd -d python    # name search
devdocs-mcp fulltext "context manager" # full-text search
devdocs-mcp read python library/os#os.getcwd
devdocs-mcp entries python os.path     # browse a section
devdocs-mcp update                     # update docs DevDocs has rebuilt
devdocs-mcp remove react
```

Prefix these with `uv run` inside the repo, or run them in Docker with `docker run --rm -v devdocs-mcp-data:/data devdocs-mcp <command>`.

## Configuration

These environment variables apply everywhere. The Docker image sets the ones marked.

| Variable | Default | |
| --- | --- | --- |
| `DEVDOCS_DATA_DIR` | `~/.local/share/devdocs-mcp` (Docker: `/data`) | Where the database lives |
| `DEVDOCS_TRANSPORT` | `stdio` (Docker: `http`) | `stdio`, `http` or `sse` |
| `DEVDOCS_HOST` | `127.0.0.1` (Docker: `0.0.0.0`) | HTTP bind address |
| `DEVDOCS_PORT` | `8000` | HTTP port |
| `DEVDOCS_PRELOAD` | | Docs to install in the background at startup, e.g. `python,javascript` |
| `DEVDOCS_AUTO_INSTALL` | `true` | Fetch a doc's index the first time a tool names it |
| `DEVDOCS_CATALOG_TTL_HOURS` | `24` | How long to cache the DevDocs catalog |
| `DEVDOCS_MANIFEST_URL` | `https://devdocs.io/docs.json` | Catalog URL |
| `DEVDOCS_DOCUMENTS_URL` | `https://documents.devdocs.io` | Where doc indexes and pages are downloaded from |

### Docker notes

- Docs persist in the `devdocs-mcp-data` volume, so container rebuilds keep them.
- `make up` publishes the port on `127.0.0.1` only. The server has no authentication, so don't expose it beyond machines you trust.
- To ship docs inside the image, bake them in: `make docker-build BAKE="python javascript"`. A new volume starts out with the baked docs.

## How it works

- **Data:** the same files the devdocs.io web app downloads:
  - `docs.json`, the catalog
  - `<slug>/index.json`, each doc's entries and sections
  - `<slug>/db.json`, every page
  - `<slug>/<path>.html`, a single page
- **Storage:** one SQLite file (WAL mode) holds the catalog, entry indexes and pages, plus an FTS5 index for full-text search.
- **Search:** a port of DevDocs' own `searcher.js`, so results rank the way they do on devdocs.io.
- **Rendering:** page HTML becomes Markdown.
  - Links are rewritten to doc-relative paths that `read_page` accepts.
  - Code blocks keep their language.
  - A `#fragment` narrows the page to the heading or `<dt>` definition that documents it.

## Development

```sh
make test     # pytest, against a fake DevDocs (no network needed)
make lint     # ruff
make format
make inspector  # poke at the tools in the MCP Inspector (needs Node.js)
```

## Credits

Documentation content comes from [DevDocs](https://github.com/freeCodeCamp/devdocs) and, through it, from each project's own docs, under their respective licenses. `src/devdocs_mcp/search.py` is a port of DevDocs' search algorithm and stays under the Mozilla Public License 2.0, like the original.
