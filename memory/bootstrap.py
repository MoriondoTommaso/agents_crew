"""bootstrap.py — Scan the codebase and seed the Graphiti knowledge graph.

Run once after `make up` to populate the graph with existing code structure:

    docker compose exec memory python bootstrap.py

What it does:
  1. Walks /workspace looking for .py files (skips venv, __pycache__, .git)
  2. For each file: AST-parses classes, functions, imports
  3. Ingests one episode per file into Graphiti (with delay for rate limits)
  4. Ingests one episode per key decision from MEMORY.md if it exists
"""

import ast
import asyncio
import os
from pathlib import Path

import httpx

MEMORY_BASE     = os.getenv("MEMORY_SERVICE_URL", "http://localhost:8002")
WORKSPACE       = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
INGEST_TIMEOUT  = int(os.getenv("BOOTSTRAP_TIMEOUT", "300"))

# Delay between episodes (seconds). Free-tier LLM providers (OpenRouter, Groq)
# have rate limits of ~20 req/min. Graphiti uses ~6 LLM calls per episode.
# 35s gives comfortable headroom; set to 0 if using a paid plan.
EPISODE_DELAY   = int(os.getenv("BOOTSTRAP_EPISODE_DELAY", "35"))

SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def extract_symbols(path: Path) -> dict:
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
        lines.append(f"Functions: {', '.join(symbols['functions'][:20])}")
    if symbols.get("imports"):
        lines.append(f"Imports: {', '.join(set(symbols['imports'][:15]))}")
    return "\n".join(lines)


async def ingest_episode(client: httpx.AsyncClient, name: str, content: str, source: str = "bootstrap"):
    resp = await client.post(
        f"{MEMORY_BASE}/mcp/memory_add_episode",
        json={"name": name, "content": content, "source": source},
        timeout=INGEST_TIMEOUT,
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
    print(f"[bootstrap] Timeout per episode: {INGEST_TIMEOUT}s")
    if EPISODE_DELAY > 0:
        print(f"[bootstrap] Delay between episodes: {EPISODE_DELAY}s (set BOOTSTRAP_EPISODE_DELAY=0 for paid plans)")
    est = len(py_files) * (EPISODE_DELAY + 30) // 60
    print(f"[bootstrap] Estimated time: ~{max(1, est)} min")

    async with httpx.AsyncClient() as client:
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

        total = len(py_files)
        for i, path in enumerate(py_files, 1):
            rel = str(path.relative_to(WORKSPACE))
            symbols = extract_symbols(path)
            if not symbols:
                continue
            content = build_episode(rel, symbols)
            print(f"  [{i}/{total}] ingesting {rel} ...", end=" ", flush=True)
            await ingest_episode(client, f"file:{rel}", content, source="bootstrap")
            print("✓")
            if EPISODE_DELAY > 0 and i < total:
                print(f"  ⏳ waiting {EPISODE_DELAY}s for rate limit ...")
                await asyncio.sleep(EPISODE_DELAY)

        memory_md = WORKSPACE / "MEMORY.md"
        if memory_md.exists():
            if EPISODE_DELAY > 0:
                print(f"  ⏳ waiting {EPISODE_DELAY}s before MEMORY.md ...")
                await asyncio.sleep(EPISODE_DELAY)
            content = memory_md.read_text(encoding="utf-8")
            print(f"  [{total+1}/{total+1}] ingesting MEMORY.md ...", end=" ", flush=True)
            await ingest_episode(client, "MEMORY.md", content, source="bootstrap")
            print("✓")

    print("[bootstrap] Done.")


if __name__ == "__main__":
    asyncio.run(main())
