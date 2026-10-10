# Using docshelf with OpenCode

[OpenCode](https://opencode.ai) can use docshelf as an MCP server, so its agents can search and read your knowledge folder. There are two ways to connect, depending on whether docshelf is already running.

| | Use when |
| --- | --- |
| **HTTP**: OpenCode connects to a running docshelf | You run docshelf with `make up` or `make serve-http`, e.g. for the browser extension. One server then covers both. |
| **Local**: OpenCode starts docshelf itself | You don't keep a server running. The browser extension needs the HTTP server, so it won't work in this mode. |

## Where the config goes

Both options go under `mcp` in an OpenCode config file:

- `~/.config/opencode/opencode.json` makes docshelf available in every project.
- `opencode.json` in a project's root makes it available in that project only.

If the file already exists, add the `docshelf` entry to its `mcp` object rather than replacing the file.

## Option 1: HTTP

Start docshelf (`make up` for Docker, or `make serve-http`), then add:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "docshelf": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "oauth": false,
      "enabled": true
    }
  }
}
```

- `"oauth": false` stops OpenCode from looking for a login flow; docshelf doesn't use one.
- If docshelf runs on another machine, use its address, e.g. `http://192.168.1.20:8000/mcp`. The server must listen on your network (`make up BIND=0.0.0.0`); see [Using it across your network](../README.md#using-it-across-your-network).

## Option 2: Local

OpenCode launches docshelf from this repo whenever it starts. From the repo, run:

```sh
make opencode-config
```

It prints a complete config with this repo's path, your knowledge folder, and the full path to `uv` filled in, e.g.:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "docshelf": {
      "type": "local",
      "command": ["/Users/you/.local/bin/uv", "run", "--quiet", "--directory", "/Users/you/code/docshelf", "docshelf"],
      "environment": { "DOCSHELF_DIR": "/Users/you/knowledge" },
      "enabled": true,
      "timeout": 20000
    }
  }
}
```

To use a different folder, run `make opencode-config KNOWLEDGE=/path/to/folder`.

`timeout` gives docshelf 20 seconds to start. OpenCode's default is 5, and the first `uv run` can take longer while it installs dependencies.

To run it in Docker instead (after `make docker-build`), use this `command`:

```json
"command": ["docker", "run", "-i", "--rm",
            "-v", "/Users/you/knowledge:/knowledge", "-v", "docshelf-index:/data",
            "docshelf", "serve", "--transport", "stdio"]
```

## Check it's connected

```sh
opencode mcp list
```

You should see `✓ docshelf connected`. If it doesn't connect:

- **Local mode:** run the `command` from your config in a terminal. It should start and wait silently; press Ctrl+C to quit. Errors appear there.
- **HTTP mode:** `curl http://localhost:8000/health` should return `{"status":"ok",...}`.
- **More detail:** `opencode mcp list --print-logs` shows why a connection failed.

## Using it

OpenCode adds docshelf's tools with a `docshelf_` prefix: `docshelf_search`, `docshelf_read`, `docshelf_list_files`, `docshelf_crawl_site` and `docshelf_crawl_status`. Agents use them when a question calls for them, or you can ask directly:

```txt
search docshelf for how we roll back deploys
use docshelf to read notes/deploy.md#rolling-back
crawl https://docs.example.com/guide/ into docshelf
```

To make agents check your notes routinely, add a line like this to the project's `AGENTS.md`:

```md
Before answering questions about our infrastructure or internal tools, search docshelf.
```

## Optional: limit what agents can do

To stop agents from crawling websites, and leave them only searching and reading, turn those tools off in the same config file:

```json
{
  "tools": {
    "docshelf_crawl*": false
  }
}
```
