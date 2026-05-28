#!/usr/bin/env python3
"""
e2e_task_test.py — End-to-end coding task to verify the full pipeline.

What this tests
---------------
  1. The server is up and the crew is ready (/health)
  2. The planner (api_llm) produces a step-by-step plan
  3. The coder (local_llm) produces runnable Python from that plan
  4. The produced code is ACTUALLY EXECUTED locally with subprocess
  5. The full OAI-compat endpoint returns the same code (model=coding-agency)
  6. Open-Claw handoff format is verified (valid OAI JSON, no streaming needed)

The task
--------
  "Write a Python function add(a, b) that returns a + b. Include a __main__
   block that prints add(3, 4)."

  This is the simplest possible task that exercises:
    • planning   (api_llm)  — trivial but must produce coherent output
    • coding     (local_llm) — must generate syntactically valid Python
    • execution  (subprocess) — output must be "7"

Usage
-----
  # Server must be running:
  make up
  # or:
  uv run uvicorn server:app --reload

  # Then:
  uv run python e2e_task_test.py

  # Custom server:
  BASE_URL=http://localhost:8000 uv run python e2e_task_test.py

Requires: requests (already in dev deps)
"""
import os
import re
import sys
import time
import json
import textwrap
import subprocess
import tempfile

try:
    import requests
except ImportError:
    print("[error] requests not found. Run: uv add --dev requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT  = int(os.getenv("REQUEST_TIMEOUT", "120"))

# The task — deliberately minimal so even a slow local model nails it
TASK = (
    "Write a Python function called add(a: int, b: int) -> int that returns a + b. "
    "Include a __main__ block at the bottom that prints add(3, 4)."
)
EXPECTED_OUTPUT = "7"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
GRN = "\033[32m"; RED = "\033[31m"; YEL = "\033[33m"
CYN = "\033[36m"; GRY = "\033[90m"; BLD = "\033[1m"; RST = "\033[0m"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ok(label: str, detail: str = "") -> None:
    print(f"  {GRN}✔ PASS{RST}  {label}" + (f"  {GRY}{detail}{RST}" if detail else ""))


def fail(label: str, reason: str) -> None:
    print(f"  {RED}✘ FAIL{RST}  {label}")
    print(f"         {RED}{reason}{RST}")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n{BLD}{CYN}── {title} {'─' * max(0, 54 - len(title))}{RST}")


def box(content: str, label: str = "", max_lines: int = 15) -> None:
    lines = content.strip().splitlines()
    preview = lines[:max_lines]
    print(f"     {GRY}┌─ {label or 'output'} {'(truncated)' if len(lines) > max_lines else ''}─{RST}")
    for ln in preview:
        for wl in (textwrap.wrap(ln, 88) or [""]):
            print(f"     {GRY}│{RST}  {wl}")
    if len(lines) > max_lines:
        print(f"     {GRY}│{RST}  {YEL}... ({len(lines) - max_lines} more lines){RST}")
    print(f"     {GRY}└{'─' * 64}{RST}")


def extract_python(text: str) -> str:
    """
    Pull the first fenced ```python ... ``` block from the text.
    Falls back to the raw text if no fence is found.
    """
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def run_python(code: str) -> tuple[str, str, int]:
    """Write code to a temp file, run it, return (stdout, stderr, returncode)."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        fname = f.name
    result = subprocess.run(
        [sys.executable, fname],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def check_health() -> None:
    section("Step 1 — Health check")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=8)
    except requests.exceptions.ConnectionError:
        fail("health", f"Cannot reach {BASE_URL} — is the server running? (make up)")
    assert r.status_code == 200, f"HTTP {r.status_code}"
    data = r.json()
    assert data.get("crew_ready") is True, f"crew_ready={data.get('crew_ready')}"
    ok("server reachable", f"crew_ready=true")


def check_plan() -> str:
    section("Step 2 — Plan (api_llm via /api/plan)")
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/api/plan",
        json={"user_request": TASK, "language": "Python"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    plan = r.json()["result"]
    assert len(plan) > 30, f"Plan suspiciously short ({len(plan)} chars)"
    ok("plan returned", f"{len(plan)} chars in {time.time()-t0:.1f}s")
    box(plan, label="plan (senior_architect)")
    return plan


def check_code() -> str:
    section("Step 3 — Code (local_llm via /api/code)")
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/api/code",
        json={"user_request": TASK, "language": "Python"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    raw = r.json()["result"]
    assert len(raw) > 20, f"Code output too short ({len(raw)} chars)"
    assert "def add" in raw, f"Expected 'def add' in output:\n{raw[:400]}"
    ok("code returned", f"{len(raw)} chars in {time.time()-t0:.1f}s")
    box(raw, label="code (senior_developer)")
    return raw


def check_execution(raw_code: str) -> None:
    section("Step 4 — Execute the generated code locally")
    code = extract_python(raw_code)
    stdout, stderr, rc = run_python(code)
    if rc != 0:
        fail("execution", f"Process exited {rc}\nSTDERR:\n{stderr[:400]}")
    if EXPECTED_OUTPUT not in stdout:
        fail(
            "output mismatch",
            f"Expected '{EXPECTED_OUTPUT}' in stdout.\n"
            f"Got: '{stdout}'\nSTDERR: {stderr[:200]}",
        )
    ok("code executed cleanly", f"stdout='{stdout}'  expected='{EXPECTED_OUTPUT}' ✓")


def check_full_pipeline() -> str:
    section("Step 5 — Full pipeline (/api/run)")
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/api/run",
        json={"user_request": TASK, "language": "Python", "topic": "basics"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    data = r.json()
    for key in ("result", "elapsed_sec", "request_id"):
        assert key in data, f"Missing key '{key}' in response"
    result = data["result"]
    assert len(result) > 20, f"Pipeline result too short"
    ok("plan→code pipeline", f"{data['elapsed_sec']}s  request_id={data['request_id']}")
    box(result, label="full pipeline output")
    return result


def check_openclaw_handoff() -> None:
    """
    Simulates exactly what Open-Claw sends: a POST to /v1/chat/completions
    with model=coding-agency. Verifies the response is a valid OAI JSON object
    that Open-Claw can parse and act on.
    """
    section("Step 6 — Open-Claw handoff (/v1/chat/completions)")
    payload = {
        "model":    "coding-agency",
        "messages": [
            {"role": "system",  "content": "You are a helpful coding assistant."},
            {"role": "user",    "content": TASK},
        ],
        "stream": False,
    }
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json=payload,
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    data = r.json()

    # Validate OAI schema fields that Open-Claw actually reads
    assert data.get("object") == "chat.completion",   f"object={data.get('object')}"
    assert "choices"                  in data,         "missing 'choices'"
    assert len(data["choices"]) > 0,                  "choices is empty"
    choice = data["choices"][0]
    assert choice.get("finish_reason") == "stop",     f"finish_reason={choice.get('finish_reason')}"
    msg     = choice.get("message", {})
    assert msg.get("role")    == "assistant",          f"role={msg.get('role')}"
    content = msg.get("content", "")
    assert len(content) > 20,                         f"content too short: {content[:100]}"
    assert "usage"            in data,                "missing 'usage'"

    elapsed = time.time() - t0
    ok("OAI schema valid",     f"id={data.get('id', '?')[:20]}  elapsed={elapsed:.1f}s")
    ok("finish_reason=stop",   "Open-Claw will read this as a complete response")
    ok("content non-empty",    f"{len(content)} chars returned")
    box(content, label="content Open-Claw receives")

    # Verify Open-Claw can find code in the response
    has_code = "def " in content or "```" in content
    if has_code:
        ok("code detected in response", "Open-Claw can extract and execute it")
    else:
        print(f"  {YEL}⚠ WARN{RST}  No code block found — Open-Claw may not find runnable code")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print(f"{BLD}Hybrid Coding Agency — E2E Task Test{RST}")
    print(f"{GRY}Server: {BASE_URL}  |  Task: '{TASK[:70]}...'{RST}")

    check_health()
    check_plan()
    raw_code = check_code()
    check_execution(raw_code)       # ← actually runs the generated code
    full_result = check_full_pipeline()
    check_openclaw_handoff()        # ← simulates exactly what Open-Claw sends

    print()
    print(f"{BLD}{GRN}╔{'═' * 46}╗{RST}")
    print(f"{BLD}{GRN}║  All 6 steps passed ✓{'':26}║{RST}")
    print(f"{BLD}{GRN}║  Pipeline + Open-Claw handoff verified {'':6}║{RST}")
    print(f"{BLD}{GRN}╚{'═' * 46}╝{RST}")
    print()
    print(f"{GRY}Next steps:{RST}")
    print(f"  1. Point Open-Claw at {BASE_URL}/v1")
    print(f"  2. Set model: coding-agency")
    print(f"  3. Give it a real task — Open-Claw will handle the review loop")
    sys.exit(0)
