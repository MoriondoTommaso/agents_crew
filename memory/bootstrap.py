"""Cross-project bootstrap — scan any directory and seed the knowledge graph.

Persists filesystem structure into Graphiti memory as episodes, grouped by
directory, so future ``memory_recall`` calls can retrieve project context.

Usage (from inside the memory container or a sibling container):

    python /app/bootstrap.py \\
        --dir /workspace \\
        --group-id my-project

The equivalent one-liner from any project directory (requires the memory-mcp
Docker image and a running Neo4j container):

    docker run --rm \\
        --network container:memory \\
        -v "$(pwd):/workspace:ro" \\
        -e NEO4J_PASSWORD=... \\
        memory-mcp:latest \\
        python /app/bootstrap.py --dir /workspace --group-id "$(basename "$(pwd)")"
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[bootstrap] %(message)s")
logger = logging.getLogger("bootstrap")

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", ".env", "env",
    "node_modules", ".npm", ".yarn", "bower_components",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", ".output",
    "target", "bin", "obj",
    ".idea", ".vscode", ".DS_Store",
    "coverage", ".coverage",
}

SKIP_FILES = {
    ".env", ".env.example", ".env.local", ".env.production",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".DS_Store", "Thumbs.db",
}

CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".rb", ".php", ".java", ".kt", ".swift",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".scala", ".ex", ".exs",
    ".svelte", ".vue", ".astro",
}
DOC_EXTS = {".md", ".rst", ".txt", ".mdx"}
CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
WEB_EXTS = {".html", ".css", ".scss", ".sass", ".less"}
DEVOPS_EXTS = {".dockerfile", ".tf", ".sql"}
ALL_EXTS = CODE_EXTS | DOC_EXTS | CONFIG_EXTS | WEB_EXTS | DEVOPS_EXTS

MAX_CONTENT_CHARS = 800


def _count_tokens(text: str) -> int:
    return len(text) // 4


def _file_summary(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if not text.strip():
        return None
    ext = path.suffix.lower()
    lines = []
    if ext in CODE_EXTS:
        if ext == ".py":
            try:
                import ast
                tree = ast.parse(text)
                classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
                ifaces = [n.names[0].asname or n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
                ifaces += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
                if classes:
                    lines.append(f"Classes: {', '.join(classes)}")
                if funcs:
                    lines.append(f"Functions ({len(funcs)}): {', '.join(funcs[:15])}")
                if ifaces:
                    lines.append(f"Dependencies: {', '.join(sorted(set(ifaces))[:10])}")
            except SyntaxError:
                pass
        if not lines:
            lines.append(f"Language: {ext.lstrip('.')}")
        token_count = _count_tokens(text)
        preview = text[:MAX_CONTENT_CHARS].strip()
        lines.append(f"Size: {len(text)} chars, ~{token_count} tokens")
        lines.append(f"Preview:\n{preview}")
    elif ext in DOC_EXTS:
        lines.append(f"Content ({len(text)} chars):\n{text[:MAX_CONTENT_CHARS].strip()}")
    elif ext in CONFIG_EXTS:
        lines.append(f"Config ({len(text)} chars):\n{text[:MAX_CONTENT_CHARS].strip()}")
    else:
        lines.append(f"Size: {len(text)} chars")
        lines.append(text[:MAX_CONTENT_CHARS].strip())
    return "\n".join(lines)


def _scan_directory(root: Path) -> list[Path]:
    files = []
    for entry in root.rglob("*"):
        rel = entry.relative_to(root)
        parts = set(rel.parts[:-1])
        if parts & SKIP_DIRS:
            continue
        if not entry.is_file():
            continue
        if entry.name in SKIP_FILES:
            continue
        ext = entry.suffix.lower()
        if ext not in ALL_EXTS:
            continue
        files.append(entry)
    files.sort(key=lambda p: (len(p.parents), p.name))
    return files


from graphiti_core.nodes import EpisodeType
from service import get_graphiti


async def main():
    parser = argparse.ArgumentParser(description="Seed knowledge graph from a project directory.")
    parser.add_argument("--dir", default="/workspace", help="Project directory to scan (default: /workspace)")
    parser.add_argument("--group-id", default=None, help="Memory namespace (default: GRAPHITI_GROUP_ID env or dir name)")
    parser.add_argument("--delay", type=float, default=None, help="Seconds between episodes for rate limiting")
    parser.add_argument("--dry-run", action="store_true", help="Only list files, don't ingest")
    args = parser.parse_args()

    project_root = Path(args.dir).resolve()
    group_id = args.group_id or os.getenv("GRAPHITI_GROUP_ID") or project_root.name
    delay = args.delay if args.delay is not None else float(os.getenv("BOOTSTRAP_EPISODE_DELAY", "0"))
    dry_run = args.dry_run

    logger.info("Project: %s", project_root)
    logger.info("Group:   %s", group_id)
    logger.info("Dry run: %s", dry_run)

    files = _scan_directory(project_root)
    logger.info("Found %d files to ingest", len(files))

    if dry_run:
        for f in files:
            rel = f.relative_to(project_root)
            print(f"  {rel}")
        return

    logger.info("Connecting to Graphiti ...")
    g = await get_graphiti()
    logger.info("Connected. Group ID: %s", group_id)

    total = len(files)
    for i, path in enumerate(files, 1):
        rel = path.relative_to(project_root)
        summary = _file_summary(path)
        if summary is None:
            logger.info("  [%d/%d] %s — skipped (empty/unreadable)", i, total, rel)
            continue
        content = f"File: {rel}\n{summary}"
        name = f"file:{rel}"
        logger.info("  [%d/%d] %s", i, total, rel)
        try:
            await g.add_episode(
                name=name,
                episode_body=content,
                source=EpisodeType.text,
                source_description="bootstrap",
                reference_time=datetime.now(UTC),
                group_id=group_id,
            )
        except Exception as e:
            logger.warning("  ⚠ %s — %s", rel, e)
        if delay > 0 and i < total:
            logger.info("  ⏳ waiting %.1fs …", delay)
            await asyncio.sleep(delay)

    logger.info("Done — %d files ingested into group '%s'", total, group_id)


if __name__ == "__main__":
    asyncio.run(main())
