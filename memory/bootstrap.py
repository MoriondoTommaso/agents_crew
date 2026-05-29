"""bootstrap.py — Scan the codebase and seed the Graphiti knowledge graph.

Run once after `make up` to populate the graph with existing code structure:

    docker compose exec memory python bootstrap.py

What it does:
  1. Walks /workspace looking for .py files (skips venv, __pycache__, .git)
  2. For each file: AST-parses classes, functions, imports
  3. Ingests one episode per file into Graphiti
  4. Ingests one episode per key decision from MEMORY.md if it exists
"""

import ast
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

MEMORY_BASE = os.getenv("MEMORY_SERVICE_URL", "http://localhost:8002")
WORKSPACE   = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def extract_symbols(path: Path) -> dict:
    """Return classes, functions, imports found in a Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return {}

    classes   = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    imports   = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module)

    return {"classes": classes, "functions": functions, "imports": imports}


def build_episode(rel_path: str, symbols: dict) -> str:
    lines = [f"File: {rel_path}"]
    if symbols.get("classes"):
        lines.append(f"Classes: {', '.join(symbols['classes'])}")
    if symbols.get("functions"):
        lines.append(f"Functions: {', '.join(symbols['functions'][:20])}")  # cap at 20
    if symbols.get("imports"):
        lines.append(f"Imports: {', '.join(set(symbols['imports'][:15]))}")
    return "\n".join(lines)


async def ingest_episode(client: httpx.AsyncClient, name: str, content: str, source: str = "bootstrap"):
    resp = await client.post(
        f"{MEMORY_BASE}/mcp/memory_add_episode",
        json={"name": name, "content": content, "source": source},
        timeout=30,
    )
    resp.raise_for_status()


async def main():
    print(f"[bootstrap] Scanning {WORKSPACE} ...")
    py_files = []
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                py_files.append(Path(root) / f)

    print(f"[bootstrap] Found {len(py_files)} Python files")

    async with httpx.AsyncClient() as client:
        # Wait for memory service
        for _ in range(10):
            try:
                r = await client.get(f"{MEMORY_BASE}/health", timeout=5)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            raise RuntimeError("Memory service not reachable")

        # Ingest Python files
        for path in py_files:
            rel = str(path.relative_to(WORKSPACE))
            symbols = extract_symbols(path)
            if not symbols:
                continue
            content = build_episode(rel, symbols)
            await ingest_episode(client, f"file:{rel}", content, source="bootstrap")
            print(f"  ✓ {rel}")

        # Ingest MEMORY.md if present
        memory_md = WORKSPACE / "MEMORY.md"
        if memory_md.exists():
            content = memory_md.read_text(encoding="utf-8")
            await ingest_episode(client, "MEMORY.md", content, source="bootstrap")
            print("  ✓ MEMORY.md ingested")

    print("[bootstrap] Done.")


if __name__ == "__main__":
    asyncio.run(main())
