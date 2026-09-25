# Docker MCP Toolkit + Claude Code (Windows)

Gives a **locally running** Claude Code container tools — list/start/stop, logs,
exec — plus whatever else you enable in Docker Desktop's MCP catalog.

## What it does and doesn't do for BBW3D

**Does:** lets a Claude Code session *on your machine* inspect and drive the
cad-agent container directly — read its logs when a build fails, restart it,
exec into it — instead of you copying terminal output back and forth.

**Doesn't:** give a cloud session (claude.ai/code) access to your Docker. The
gateway talks over **stdio** to a client process on the same machine. A cloud
container cannot reach it, and no amount of gateway configuration changes that.

**Not required.** `scripts/setup.ps1` already does everything BBW3D needs from
Docker. This is a convenience, not a dependency.

## Setup

Docker Desktop must be running.

### The easy way

```powershell
cd $HOME\dev\BBW3D
docker mcp client connect claude-code
```

Expected:

```
=== Project-wide MCP Configurations (C:\Users\<you>\dev\BBW3D) ===
 ● claude-code: connected
   MCP_DOCKER: Docker MCP Catalog (gateway server) (stdio)
```

That writes `.mcp.json` in the project folder. Or do it from the GUI: Docker
Desktop → **MCP Toolkit** → **Clients** → Claude Code → **Connect**.

Then start Claude Code in that folder and run `/mcp`. On first launch it asks
about the new server; approving it for the project is the option you want.

### If it fails on Windows

Three known Windows problems, all documented upstream in
[docker/mcp-gateway#424](https://github.com/docker/mcp-gateway/issues/424):

1. **Microsoft Store builds are sandboxed** and can't spawn the gateway. Use the
   installer from claude.ai/download instead.
2. **`docker mcp` isn't found** — the plugin lives at
   `C:\Program Files\Docker\cli-plugins\docker-mcp.exe` but isn't always on PATH.
3. **`panic: unable to get 'ProgramData'`** — the client doesn't pass Windows
   environment variables to the child process, and the binary needs them.

The working configuration for all three, with your real username substituted:

```json
{
  "mcpServers": {
    "MCP_DOCKER": {
      "command": "C:\\Program Files\\Docker\\cli-plugins\\docker-mcp.exe",
      "args": ["gateway", "run"],
      "env": {
        "ProgramData": "C:\\ProgramData",
        "LOCALAPPDATA": "C:\\Users\\YOUR_USERNAME\\AppData\\Local",
        "APPDATA": "C:\\Users\\YOUR_USERNAME\\AppData\\Roaming",
        "USERPROFILE": "C:\\Users\\YOUR_USERNAME",
        "SystemRoot": "C:\\Windows"
      }
    }
  }
}
```

Restart the client after editing.

## Why `.mcp.json` is gitignored

Docker writes machine-specific absolute paths into it, and the Windows fallback
above embeds your username. Each machine generates its own.

## Running BBW3D work locally

The real reason to bother with any of this: a Claude Code session running **on
your machine** can do the parts a cloud session can't — run `setup.ps1`, read
container logs, drive the whole model loop against a live container.

```powershell
cd $HOME\dev\BBW3D
claude
```

The `3d-designer` agent and the `image-to-cad` skill are committed in this repo,
so a local session picks them up automatically.

## Sources

- [Add MCP Servers to Claude Code with MCP Toolkit](https://www.docker.com/blog/add-mcp-servers-to-claude-code-with-mcp-toolkit/)
- [docker/mcp-gateway](https://github.com/docker/mcp-gateway)
- [Windows connection issues #424](https://github.com/docker/mcp-gateway/issues/424)
