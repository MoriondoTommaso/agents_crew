#!/usr/bin/env python3
"""
test_pipeline.py — End-to-end smoke test for the Hybrid Coding Agency.

Tests (in order):
  1. Health check           — server is up and crew is ready
  2. Model list             — /v1/models returns expected models
  3. Plan-only endpoint     — /api/plan routes to api_llm
  4. Code-only endpoint     — /api/code routes to local_llm
  5. Full pipeline          — /api/run  (plan → code, hybrid SMLRouter)
  6. OAI-compat full        — /v1/chat/completions with model=coding-agency
  7. OAI-compat plan        — /v1/chat/completions with model=coding-plan
  8. OAI-compat code        — /v1/chat/completions with model=coding-code
  9. Streaming              — /v1/chat/completions with stream=true

Usage:
  # Against local dev server (default):
  uv run python test_pipeline.py

  # Against custom host/port:
  BASE_URL=http://localhost:8000 uv run python test_pipeline.py

  # Quick mode: only health + full pipeline:
  QUICK=1 uv run python test_pipeline.py

  # Against the Docker stack:
  BASE_URL=http://localhost:8000 uv run python test_pipeline.py

Requires: requests  (already in pyproject.toml dev deps)
"""
import os
import sys
import time
import json
import textwrap
from typing import Any

try:
    import requests
except ImportError:
    print("[error] 'requests' not found. Run: uv add --dev requests")
    sys.exit(1)

# ── config ────────────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
QUICK    = os.getenv("QUICK", "0") == "1"
TIMEOUT  = int(os.getenv("REQUEST_TIMEOUT", "120"))  # per-request timeout (s)

# Simple task that exercises both planning and coding but is fast to execute
TEST_TASK = (
    "Write a Python function called `fibonacci(n: int) -> list[int]` that returns "
    "the first n Fibonacci numbers. Include type hints and a one-line docstring."
)

# ── colours ───────────────────────────────────────────────────────────────────────
GRN  = "\033[32m"; RED  = "\033[31m"; YEL  = "\033[33m"
CYN  = "\033[36m"; GRY  = "\033[90m"; BLD  = "\033[1m";  RST  = "\033[0m"

# ── test runner ──────────────────────────────────────────────────────────────────

results: list[dict] = []

def run_test(name: str, fn) -> bool:
    """Execute a test function, capture result, print inline."""
    print(f"  {GRY}running{RST}  {name} ...", end="", flush=True)
    start = time.time()
    try:
        fn()
        elapsed = time.time() - start
        print(f"\r  {GRN}✔ PASS{RST}    {name} {GRY}({elapsed:.1f}s){RST}")
        results.append({"name": name, "status": "PASS", "elapsed": elapsed})
        return True
    except AssertionError as e:
        elapsed = time.time() - start
        print(f"\r  {RED}✘ FAIL{RST}    {name} {GRY}({elapsed:.1f}s){RST}")
        print(f"     {RED}AssertionError:{RST} {e}")
        results.append({"name": name, "status": "FAIL", "elapsed": elapsed, "error": str(e)})
        return False
    except Exception as e:
        elapsed = time.time() - start
        print(f"\r  {RED}✘ ERROR{RST}   {name} {GRY}({elapsed:.1f}s){RST}")
        print(f"     {RED}{type(e).__name__}:{RST} {e}")
        results.append({"name": name, "status": "ERROR", "elapsed": elapsed, "error": str(e)})
        return False


def print_result_box(content: str, label: str = "output", max_lines: int = 20) -> None:
    lines = content.strip().splitlines()
    preview = lines[:max_lines]
    truncated = len(lines) > max_lines
    print(f"     {GRY}┌─ {label} {'(truncated)' if truncated else ''}─{RST}")
    for line in preview:
        wrapped = textwrap.wrap(line, width=90) or [""]
        for wl in wrapped:
            print(f"     {GRY}│{RST}  {wl}")
    if truncated:
        print(f"     {GRY}│{RST}  {YEL}... ({len(lines) - max_lines} more lines){RST}")
    print(f"     {GRY}└───────────────────────────────────────────────────────────────{RST}")


# ── individual tests ──────────────────────────────────────────────────────────────────

def test_health():
    r = requests.get(f"{BASE_URL}/health", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert data.get("status") == "ok", f"status != ok: {data}"
    assert data.get("crew_ready") is True, f"crew_ready is not True: {data}"


def test_model_list():
    r = requests.get(f"{BASE_URL}/v1/models", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    ids = [m["id"] for m in r.json()["data"]]
    for expected in ("coding-agency", "coding-plan", "coding-code"):
        assert expected in ids, f"Model '{expected}' not found in {ids}"


def test_plan_only():
    r = requests.post(
        f"{BASE_URL}/api/plan",
        json={"user_request": TEST_TASK, "language": "Python"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    result = r.json()["result"]
    assert len(result) > 50, f"Plan output suspiciously short ({len(result)} chars)"
    print_result_box(result, label="plan output")


def test_code_only():
    r = requests.post(
        f"{BASE_URL}/api/code",
        json={"user_request": TEST_TASK, "language": "Python"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    result = r.json()["result"]
    assert len(result) > 50, f"Code output suspiciously short ({len(result)} chars)"
    assert "def" in result or "fibonacci" in result.lower(), \
        f"Expected Python function in output but got:\n{result[:300]}"
    print_result_box(result, label="code output")


def test_full_pipeline():
    r = requests.post(
        f"{BASE_URL}/api/run",
        json={"user_request": TEST_TASK, "language": "Python", "topic": "algorithms"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert "result"      in data, f"'result' key missing: {data.keys()}"
    assert "elapsed_sec" in data, f"'elapsed_sec' key missing: {data.keys()}"
    assert "request_id"  in data, f"'request_id' key missing: {data.keys()}"
    result = data["result"]
    assert len(result) > 50, f"Pipeline output suspiciously short ({len(result)} chars)"
    print(f"     {GRY}elapsed:{RST} {data['elapsed_sec']}s  "
          f"{GRY}request_id:{RST} {data['request_id']}")
    print_result_box(result, label="full pipeline output")


def test_oai_full():
    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model":    "coding-agency",
            "messages": [{"role": "user", "content": TEST_TASK}],
            "stream":   False,
        },
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data["object"] == "chat.completion", f"Unexpected object: {data.get('object')}"
    content = data["choices"][0]["message"]["content"]
    assert len(content) > 50, f"Response too short: {content[:200]}"
    print_result_box(content, label="OAI completion")


def test_oai_plan():
    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model":    "coding-plan",
            "messages": [{"role": "user", "content": TEST_TASK}],
        },
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    content = r.json()["choices"][0]["message"]["content"]
    assert len(content) > 20, "Plan via OAI endpoint returned empty content"
    print_result_box(content, label="OAI plan", max_lines=10)


def test_oai_code():
    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model":    "coding-code",
            "messages": [{"role": "user", "content": TEST_TASK}],
        },
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    content = r.json()["choices"][0]["message"]["content"]
    assert len(content) > 20, "Code via OAI endpoint returned empty content"
    print_result_box(content, label="OAI code", max_lines=10)


def test_streaming():
    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json={
            "model":    "coding-agency",
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "stream":   True,
        },
        stream=True,
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    chunks: list[str] = []
    for line in r.iter_lines():
        if not line:
            continue
        decoded = line.decode() if isinstance(line, bytes) else line
        if decoded.startswith("data: "):
            payload = decoded[6:]
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
                if delta:
                    chunks.append(delta)
            except json.JSONDecodeError:
                pass
    assembled = "".join(chunks)
    assert len(assembled) > 5, f"Stream assembled content too short: '{assembled}'"
    print(f"     {GRY}stream chunks:{RST} {len(chunks)}  "
          f"{GRY}assembled:{RST} '{assembled[:80]}...'")


# ── routing verification ────────────────────────────────────────────────────────────────
ROUTING_TASKS: list[tuple[str, str]] = [
    # (description, expected_route)
    ("Plan the architecture for a REST API",    "api"),
    ("Review this Python code for bugs",        "api"),
    ("Write a Python function to sort a list",  "local"),
    ("Implement a binary search in TypeScript", "local"),
    ("Build a FastAPI endpoint for /health",    "local"),
    ("Design the database schema for users",    "api"),
]

def test_routing_logic():
    """
    Unit test for SMLRouter._keyword_fallback() and _OVERRIDES.
    Doesn't require the server to be running.
    """
    import importlib.util, pathlib

    spec = importlib.util.spec_from_file_location("crew", pathlib.Path("crew.py"))
    mod  = importlib.util.load_from_spec(spec)  # type: ignore
    spec.loader.exec_module(mod)                 # type: ignore

    # Patch LLM constructor so it doesn't try to connect to anything
    class _FakeLLM:
        def __init__(self, **_): pass

    from unittest.mock import patch
    with patch("crew.LLM", _FakeLLM):
        from crew import SMLRouter, PipelineMode
        router = SMLRouter(
            api_llm   = _FakeLLM(),
            local_llm = _FakeLLM(),
            mode      = PipelineMode.HYBRID,
        )
        router._router_llm = None  # disable actual LLM call

        failed: list[str] = []
        for desc, expected in ROUTING_TASKS:
            got = router._keyword_fallback(desc)
            if got != expected:
                failed.append(f"  '{desc}' → expected={expected} got={got}")
            else:
                print(f"     {GRN}✔{RST}  '{desc[:55]}' → {got}")

        if failed:
            raise AssertionError("Routing mismatches:\n" + "\n".join(failed))


def test_pipeline_mode_env():
    """Verify PipelineMode.from_env() handles all valid values and bad input."""
    import importlib, importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("crew", pathlib.Path("crew.py"))
    mod  = importlib.util.load_from_spec(spec)  # type: ignore
    spec.loader.exec_module(mod)                 # type: ignore

    from unittest.mock import patch
    with patch("crew.LLM", lambda **_: None):
        from crew import PipelineMode

    for val, expected in [
        ("hybrid", PipelineMode.HYBRID),
        ("api",    PipelineMode.API),
        ("local",  PipelineMode.LOCAL),
        ("HYBRID", PipelineMode.HYBRID),  # case-insensitive
        ("bad",    PipelineMode.HYBRID),  # fallback
    ]:
        with patch.dict(os.environ, {"PIPELINE_MODE": val}):
            importlib.reload(mod)
            from crew import PipelineMode as PM
            result = PM.from_env()
            assert result == expected, f"from_env('{val}') = {result}, expected {expected}"
        print(f"     {GRN}✔{RST}  PIPELINE_MODE={val!r} → {result.value}")


# ── main ───────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print(f"{BLD}Hybrid Coding Agency — Pipeline Smoke Test{RST}")
    print(f"{GRY}Target: {BASE_URL}  |  timeout: {TIMEOUT}s/req  |  quick: {QUICK}{RST}")
    print()

    # Unit tests (no server needed)
    print(f"{BLD}{CYN}── Unit tests (no server required) ────────────────────────────────{RST}")
    run_test("routing keyword fallback", test_routing_logic)
    run_test("PIPELINE_MODE env parsing", test_pipeline_mode_env)
    print()

    if QUICK:
        # Quick mode: only health + full pipeline
        print(f"{BLD}{CYN}── Quick integration tests ────────────────────────────────────────{RST}")
        tests = [
            ("health check",    test_health),
            ("full pipeline",   test_full_pipeline),
        ]
    else:
        print(f"{BLD}{CYN}── Integration tests (server must be running) ─────────────────────{RST}")
        tests = [
            ("health check",      test_health),
            ("model list",        test_model_list),
            ("plan only",         test_plan_only),
            ("code only",         test_code_only),
            ("full pipeline",     test_full_pipeline),
            ("OAI full pipeline", test_oai_full),
            ("OAI plan model",    test_oai_plan),
            ("OAI code model",    test_oai_code),
            ("streaming",         test_streaming),
        ]

    server_reachable = True
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except requests.exceptions.ConnectionError:
        print(f"  {YEL}⚠ Server not reachable at {BASE_URL}{RST}")
        print(f"  {GRY}Start it with: make up  (Docker) or: uv run uvicorn server:app --reload{RST}")
        server_reachable = False

    if server_reachable:
        for name, fn in tests:
            run_test(name, fn)

    # ── summary ───────────────────────────────────────────────────────────────────
    print()
    total  = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] in ("FAIL", "ERROR"))
    total_time = sum(r["elapsed"] for r in results)

    status_line = f"{passed}/{total} passed"
    colour = GRN if failed == 0 else RED

    print(f"{BLD}{colour}╔══════════════════════════════════════════════╗{RST}")
    print(f"{BLD}{colour}║  {status_line:<44}║{RST}")
    print(f"{BLD}{colour}║  Total time: {total_time:.1f}s{' ' * (32 - len(f'{total_time:.1f}'))}║{RST}")
    print(f"{BLD}{colour}╚══════════════════════════════════════════════╝{RST}")

    if failed > 0:
        print()
        print(f"{RED}Failed tests:{RST}")
        for r in results:
            if r["status"] in ("FAIL", "ERROR"):
                print(f"  {RED}✘{RST}  {r['name']}: {r.get('error', '')[:120]}")

    sys.exit(0 if failed == 0 else 1)
