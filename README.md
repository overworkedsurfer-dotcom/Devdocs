# docshelf

Your own knowledge folder, searchable by your AI assistant over [MCP](https://modelcontextprotocol.io).

- **Your files:** notes and documents you keep in a folder (Markdown, text, HTML, PDF).
- **Pages you read:** a Chrome extension saves pages to the folder as you browse, only on the sites you switch it on for.
- **Whole sites:** a crawler saves a docs site in one go.

Your assistant searches and reads that folder, and nothing else. Everything is plain Markdown files on your disk, so you can open, edit, grep or git them like any other file.

```
Chrome extension ── pages you browse, on sites you choose ──┐
Crawler ─────────── whole sites ────────────────────────────┤──▶  ~/knowledge/web/<site>/<page>.md
You ─────────────── notes, docs, PDFs ──────────────────────┘     ~/knowledge/**/*.md .txt .html .pdf
                                                                          │
AI assistant ◀── MCP: search · read · list_files · crawl_site ── docshelf ┘
```

## Quick start

**1. Start the server.** It serves `~/knowledge`; set `KNOWLEDGE=/path/to/folder` to use another folder.

```sh
make up              # Docker: http://localhost:8000/mcp
# or
make setup           # uv
make serve-http
```

**2. Install the extension.**
1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick this repo's `extension/` folder.
3. Click the docshelf icon. Under **Server**, enter the address (`http://localhost:8000`, or your server's LAN IP; see below) and the token from `make docker-token` (Docker) or `make token` (uv). Click **Save**; it should say **Connected**.

**3. Turn on auto-send.** On a site you want to keep, click the icon and switch on **Auto-send pages from this site**. From then on, every page you open there is saved. The icon briefly shows ✓ when a page is saved.

**4. Connect your AI client** (next section).

## Connect an AI client

**Claude Code:**

```sh
claude mcp add --transport http docshelf http://localhost:8000/mcp
```

**Claude Desktop** (`claude_desktop_config.json`). Desktop launches the server itself, so it doesn't need `make up`:

```json
{
  "mcpServers": {
    "docshelf": {
      "command": "uv",
      "args": ["run", "--quiet", "--directory", "/path/to/this/repo", "docshelf"],
      "env": { "DOCSHELF_DIR": "/Users/you/knowledge" }
    }
  }
}
```

If Claude Desktop can't find `uv`, use its full path (`which uv`).

**OpenCode:** add this to `~/.config/opencode/opencode.json`, or to `opencode.json` in a project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "docshelf": { "type": "remote", "url": "http://localhost:8000/mcp", "oauth": false, "enabled": true }
  }
}
```

To have OpenCode start docshelf itself instead, run `make opencode-config` and paste what it prints. The [OpenCode guide](docs/opencode.md) covers both setups, checking the connection, and limiting what agents can do.

**Cursor, VS Code and other HTTP clients:** use the URL `http://localhost:8000/mcp`.

## Using it across your network

To send pages from a browser on another machine, the server has to listen on your LAN:

```sh
make up BIND=0.0.0.0           # Docker
make serve-http HOST=0.0.0.0   # uv
```

Then put `http://<server's LAN IP>:8000` in the extension.

The extension's API (`/api`) always requires the token. The MCP endpoint (`/mcp`) has no login, so anyone on your network can search your folder through it. Only do this on a network you trust.

## What the AI can do

| Tool | |
| --- | --- |
| `search` | Find sections by their words; headings and titles count most. Results are paths like `notes/deploy.md#rolling-back`. |
| `read` | Read a file as Markdown, or just one section with `#anchor`. A saved page can also be read by its original URL. Long files come in chunks. |
| `list_files` | Browse the folders. |
| `crawl_site` | Save a website into the folder, in the background. |
| `crawl_status` | Check on crawls. |

The assistant only sees your folder. Nothing is looked up online, except when it crawls a site you asked for.

## The knowledge folder

- **What's read:** `.md`, `.markdown`, `.mdx`, `.txt`, `.rst`, `.adoc`, `.html`, `.htm` and `.pdf`, in any subfolder.
- **What's skipped:** hidden folders (like `.git` or `.obsidian`) and `node_modules`.
- **Changes are picked up automatically.** Add, edit or delete files any way you like, and searches notice within a couple of seconds.
- **Where saved pages go:** browser and crawler pages land in `web/<site>/<path>.md`. Each one has a small header recording its title, source URL and the time it was saved:

  ```markdown
  ---
  title: "useState – React"
  source: https://react.dev/reference/react/useState
  saved: 2026-10-09T20:22:23Z
  via: browser
  ---
  # useState
  ...
  ```

  Saving a page again only rewrites the file if its content changed. If you edit a saved page by hand, the next save of that page overwrites your edits.
- **The search index:** it lives in `.docshelf/` inside the folder, ignored by git. It's only a cache: `make reindex` rebuilds it, and deleting it loses nothing. In Docker it's kept in a volume instead.

## The extension

- **Auto-send** works only on sites you switch on, and asks Chrome for access to each site when you do.
  - It waits for a page to finish loading before sending it.
  - On app-style sites that change pages without reloading, it sends each new page too.
  - Pages without readable text, like login screens, aren't saved.
  - The icon shows ✓ when a page is saved, – when it's skipped, and ! on an error. Hover over it for details.
- **Send this page** saves the current page once, on any site.
- **Crawl site on server** has the server fetch the rest of the site.
  - The server can't use your browser's logins or run JavaScript.
  - For sites behind a login, or apps that build pages with JavaScript, use auto-send while you browse instead.
- **Your data:** pages go only to the server address you entered, and the extension sends nothing anywhere else.

## Crawling

```sh
make crawl URL=https://docs.example.com/guide/
docshelf crawl https://wiki.internal/docs/ --max-pages 500 -H "Cookie: session=..."
```

- **Scope:** a crawl follows links that stay under the start page's folder. Change that with `--prefix`, `--include` or `--exclude`.
- **robots.txt** is respected unless you pass `--ignore-robots`.
- **Logins:** `-H` adds headers, such as a session cookie for internal sites. Headers are only ever sent to that site.

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `DOCSHELF_DIR` | `~/knowledge` (Docker: `/knowledge`) | The knowledge folder |
| `DOCSHELF_INDEX_DIR` | `<folder>/.docshelf` (Docker: `/data`) | Search index and token |
| `DOCSHELF_TOKEN` | generated, kept in the index dir | Token for the extension's API |
| `DOCSHELF_WEB_FOLDER` | `web` | Subfolder for saved pages |
| `DOCSHELF_TRANSPORT` | `stdio` (Docker: `http`) | `stdio`, `http` or `sse` |
| `DOCSHELF_HOST` | `127.0.0.1` (Docker: `0.0.0.0`) | HTTP bind address |
| `DOCSHELF_PORT` | `8000` | HTTP port |

The Makefile takes `KNOWLEDGE`, `PORT`, `HOST` (uv), `BIND` (Docker) and `TOKEN`. Run `make` to list every target.

## Command line

```sh
docshelf serve --transport http   # serve over HTTP (default: stdio)
docshelf token                    # the extension's token
docshelf search "rolling back"    # search, exactly as the assistant sees it
docshelf read notes/deploy.md#rolling-back
docshelf ls web
docshelf crawl https://docs.example.com/
docshelf reindex
```

Inside the repo, run these with `uv run docshelf ...`.

## HTTP API

This is what the extension uses. Every `/api` route needs `Authorization: Bearer <token>`.

| Route | |
| --- | --- |
| `GET /health` | No token needed |
| `GET /api/status` | Version and file count |
| `POST /api/pages` | `{"url", "html", "title"?}` saves a page |
| `POST /api/crawl` | `{"url", "max_pages"?, "prefix"?, "headers"?}` starts a crawl |
| `GET /api/crawl`, `GET /api/crawl/{id}` | Crawl progress |
| `DELETE /api/crawl/{id}` | Cancel a crawl |

## Development

```sh
make test   # pytest
make lint   # ruff
```
