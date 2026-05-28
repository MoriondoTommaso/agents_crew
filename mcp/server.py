"""
MCP Server — filesystem + GitHub tools for Open-Claw.

Exposes an MCP-over-HTTP endpoint at POST /mcp
returning a JSON-RPC 2.0 response.

Tools available:
  Filesystem:
    - read_file        read a file from /workspace
    - write_file       write / create a file in /workspace
    - list_directory   list files in a /workspace subdirectory
    - delete_file      delete a file from /workspace

  GitHub:
    - github_get_file       read a file from a GitHub repo
    - github_create_or_update_file  create or update a file in a repo
    - github_list_prs       list open pull requests
    - github_create_pr      open a new pull request
    - github_create_branch  create a new branch

Environment variables (all optional except GITHUB_TOKEN for GitHub tools):
  WORKSPACE_ROOT   absolute path mounted as workspace  (default: /workspace)
  GITHUB_TOKEN     personal access token with repo scope
  GITHUB_OWNER     default repo owner (avoids repeating in every call)
  GITHUB_REPO      default repo name
"""

from __future__ import annotations

import os
import base64
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from github import Github, GithubException

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace")).resolve()
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
DEFAULT_OWNER  = os.getenv("GITHUB_OWNER", "")
DEFAULT_REPO   = os.getenv("GITHUB_REPO",  "")

app = FastAPI(title="MCP Server", version="1.0.0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_path(rel: str) -> Path:
    """Resolve a relative path inside WORKSPACE_ROOT, reject traversal."""
    resolved = (WORKSPACE_ROOT / rel).resolve()
    if not str(resolved).startswith(str(WORKSPACE_ROOT)):
        raise ValueError(f"Path traversal attempt: {rel}")
    return resolved


def _gh_repo(owner: str | None = None, repo: str | None = None):
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set")
    g = Github(GITHUB_TOKEN)
    o = owner or DEFAULT_OWNER
    r = repo  or DEFAULT_REPO
    if not o or not r:
        raise ValueError("GitHub owner/repo not provided and GITHUB_OWNER/GITHUB_REPO not set")
    return g.get_repo(f"{o}/{r}")


def _ok(data: Any) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": data}


def _err(code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": msg}}


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------
def _dispatch(tool: str, args: dict) -> dict:
    # ── Filesystem ──────────────────────────────────────────────────────────
    if tool == "read_file":
        p = _safe_path(args["path"])
        if not p.exists():
            return _err(-32001, f"File not found: {args['path']}")
        return _ok({"content": p.read_text(encoding="utf-8"), "path": str(p)})

    if tool == "write_file":
        p = _safe_path(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return _ok({"written": str(p), "bytes": len(args["content"].encode())})

    if tool == "list_directory":
        p = _safe_path(args.get("path", "."))
        if not p.is_dir():
            return _err(-32001, f"Not a directory: {args.get('path', '.')}")
        entries = [
            {"name": e.name, "type": "dir" if e.is_dir() else "file", "size": e.stat().st_size if e.is_file() else None}
            for e in sorted(p.iterdir())
        ]
        return _ok({"path": str(p), "entries": entries})

    if tool == "delete_file":
        p = _safe_path(args["path"])
        if not p.exists():
            return _err(-32001, f"File not found: {args['path']}")
        p.unlink()
        return _ok({"deleted": str(p)})

    # ── GitHub ───────────────────────────────────────────────────────────────
    if tool == "github_get_file":
        repo   = _gh_repo(args.get("owner"), args.get("repo"))
        ref    = args.get("ref", repo.default_branch)
        f      = repo.get_contents(args["path"], ref=ref)
        content = base64.b64decode(f.content).decode("utf-8")  # type: ignore[union-attr]
        return _ok({"path": args["path"], "sha": f.sha, "content": content})  # type: ignore[union-attr]

    if tool == "github_create_or_update_file":
        repo    = _gh_repo(args.get("owner"), args.get("repo"))
        path    = args["path"]
        content = args["content"]
        message = args.get("message", f"Update {path}")
        branch  = args.get("branch", repo.default_branch)
        try:
            existing = repo.get_contents(path, ref=branch)
            result = repo.update_file(path, message, content, existing.sha, branch=branch)  # type: ignore[union-attr]
            action = "updated"
        except GithubException:
            result = repo.create_file(path, message, content, branch=branch)
            action = "created"
        return _ok({"action": action, "path": path, "sha": result["content"].sha})

    if tool == "github_list_prs":
        repo  = _gh_repo(args.get("owner"), args.get("repo"))
        state = args.get("state", "open")
        prs   = repo.get_pulls(state=state)
        return _ok([{"number": pr.number, "title": pr.title, "head": pr.head.ref, "base": pr.base.ref, "url": pr.html_url} for pr in prs])

    if tool == "github_create_pr":
        repo  = _gh_repo(args.get("owner"), args.get("repo"))
        pr    = repo.create_pull(
            title=args["title"],
            body=args.get("body", ""),
            head=args["head"],
            base=args.get("base", repo.default_branch),
        )
        return _ok({"number": pr.number, "url": pr.html_url})

    if tool == "github_create_branch":
        repo   = _gh_repo(args.get("owner"), args.get("repo"))
        source = args.get("from_branch", repo.default_branch)
        sha    = repo.get_branch(source).commit.sha
        repo.create_git_ref(ref=f"refs/heads/{args['branch']}", sha=sha)
        return _ok({"branch": args["branch"], "from": source, "sha": sha})

    return _err(-32601, f"Unknown tool: {tool}")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "workspace": str(WORKSPACE_ROOT), "github": bool(GITHUB_TOKEN)}


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        tool = body["params"]["name"]
        args = body["params"].get("arguments", {})
        result = _dispatch(tool, args)
        return JSONResponse(content=result)
    except KeyError as e:
        return JSONResponse(content=_err(-32600, f"Bad request: missing {e}"), status_code=400)
    except (ValueError, RuntimeError) as e:
        return JSONResponse(content=_err(-32001, str(e)), status_code=400)
    except Exception as e:
        return JSONResponse(content=_err(-32603, str(e)), status_code=500)


@app.get("/tools")
def list_tools() -> dict:
    """Return the MCP tool manifest consumed by Open-Claw."""
    return {
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file from the workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path inside /workspace"}},
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Write or create a file in the workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "list_directory",
                "description": "List files and subdirectories inside a workspace path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path, default '.'."}},
                },
            },
            {
                "name": "delete_file",
                "description": "Delete a file from the workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "github_get_file",
                "description": "Read a file from a GitHub repository.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path":  {"type": "string"},
                        "ref":   {"type": "string", "description": "Branch/tag/SHA (default: default branch)"},
                        "owner": {"type": "string"},
                        "repo":  {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "github_create_or_update_file",
                "description": "Create or update a file in a GitHub repository and commit it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path":    {"type": "string"},
                        "content": {"type": "string"},
                        "message": {"type": "string"},
                        "branch":  {"type": "string"},
                        "owner":   {"type": "string"},
                        "repo":    {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "github_list_prs",
                "description": "List pull requests in a GitHub repository.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "enum": ["open", "closed", "all"]},
                        "owner": {"type": "string"},
                        "repo":  {"type": "string"},
                    },
                },
            },
            {
                "name": "github_create_pr",
                "description": "Open a pull request on GitHub.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "head":  {"type": "string", "description": "Source branch"},
                        "base":  {"type": "string", "description": "Target branch (default: default branch)"},
                        "body":  {"type": "string"},
                        "owner": {"type": "string"},
                        "repo":  {"type": "string"},
                    },
                    "required": ["title", "head"],
                },
            },
            {
                "name": "github_create_branch",
                "description": "Create a new branch in a GitHub repository.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "branch":      {"type": "string"},
                        "from_branch": {"type": "string", "description": "Source branch (default: default branch)"},
                        "owner":       {"type": "string"},
                        "repo":        {"type": "string"},
                    },
                    "required": ["branch"],
                },
            },
        ]
    }
