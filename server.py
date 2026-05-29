"""
FastAPI server for the Hybrid Coding Agency.

Exposes two types of endpoints:
  1. /v1/chat/completions  — OpenAI-compatible (for harnesses: Open-Claw, Aider, Continue.dev)
  2. /api/*                — Native endpoints

Pipeline: plan (api_llm) → code (local_llm)  [single pass]
Iteration/healing is delegated to the harness via real code execution tools.

Start with:
    uv run uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import time
import uuid
import json
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from crewai import Crew, Process

load_dotenv()

LLM_TIMEOUT_SEC = int(os.getenv("LLM_TIMEOUT_SEC", "600"))


# ---------------------------------------------------------------------------
# App lifecycle — lazy crew init so /health works immediately
# ---------------------------------------------------------------------------

_crew_instance = None
_crew_lock = asyncio.Lock()
_crew_error: str | None = None


async def get_crew():
    """Lazy singleton: initialise CodingAgencyCrew on first call."""
    global _crew_instance, _crew_error
    if _crew_instance is not None:
        return _crew_instance
    async with _crew_lock:
        if _crew_instance is not None:   # double-checked
            return _crew_instance
        try:
            from crew import CodingAgencyCrew
            print("[Server] Initialising CodingAgencyCrew ...")
            _crew_instance = await asyncio.to_thread(CodingAgencyCrew)
            _crew_error = None
            print("[Server] CodingAgencyCrew ready.")
        except Exception as exc:
            _crew_error = str(exc)
            print(f"[Server] CodingAgencyCrew init failed: {exc}")
            raise HTTPException(status_code=503, detail=f"Crew init failed: {exc}")
    return _crew_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up in background — /health is available immediately
    asyncio.create_task(_warm_up())
    yield
    global _crew_instance
    _crew_instance = None


async def _warm_up():
    """Pre-warm crew in background; log warnings if deps unreachable."""
    _check_host_deps()
    try:
        await get_crew()
    except Exception:
        pass  # error already logged in get_crew()


def _check_host_deps():
    """Warn (don't crash) if Ollama / FreeLLM are not reachable."""
    import urllib.request
    checks = [
        (os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"), "Ollama"),
        (os.getenv("FREELLM_BASE_URL", "http://host.docker.internal:3001/v1").rstrip("/v1"), "FreeLLM"),
    ]
    for url, name in checks:
        try:
            urllib.request.urlopen(url, timeout=3)
            print(f"[Server] ✓ {name} reachable at {url}")
        except Exception:
            print(f"[Server] ⚠  {name} NOT reachable at {url} — start it before sending requests")


app = FastAPI(
    title="Hybrid Coding Agency",
    description="SML router: plan→api_llm, code→local_llm. OpenAI-compatible.",
    version="0.5.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CodeRequest(BaseModel):
    user_request:     str  = Field(..., description="What to build")
    language:         str  = Field(default="Python", description="Target language")
    topic:            str  = Field(default="software", description="Domain/context")
    technical_design: str  = Field(default="", description="Pre-computed design doc (optional).")

class CodeResponse(BaseModel):
    request_id:  str
    result:      str
    elapsed_sec: float

class OAIMessage(BaseModel):
    role:    str
    content: str

class OAIChatRequest(BaseModel):
    model:       str              = "coding-agency"
    messages:    list[OAIMessage]
    stream:      bool             = False
    temperature: float            = 0.1
    max_tokens:  int | None       = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_user_request(messages: list[OAIMessage]) -> str:
    if not messages:
        raise HTTPException(status_code=422, detail="messages list must not be empty")
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return messages[-1].content


def _parse_language_from_text(text: str) -> str:
    text_lower = text.lower()
    for lang in ["python", "typescript", "javascript", "rust", "go", "java", "c++"]:
        if lang in text_lower:
            return lang.capitalize()
    return "Python"


def _oai_response(content: str, model: str = "coding-agency") -> dict:
    return {
        "id":      f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     -1,
            "completion_tokens": -1,
            "total_tokens":      -1,
        },
    }


async def _stream_oai_response(content: str, model: str = "coding-agency") -> AsyncGenerator[str, None]:
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    words = content.split(" ")
    for i, word in enumerate(words):
        delta = word + (" " if i < len(words) - 1 else "")
        chunk = {
            "id":      chunk_id,
            "object":  "chat.completion.chunk",
            "created": int(time.time()),
            "model":   model,
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0)

    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    yield "data: [DONE]\n\n"


async def _run_with_timeout(fn, *args):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args),
            timeout=LLM_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Pipeline timed out after {LLM_TIMEOUT_SEC}s."
        )


# ---------------------------------------------------------------------------
# Native API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Always returns 200 immediately — even before crew is initialised."""
    return {
        "status": "ok",
        "crew_ready": _crew_instance is not None,
        "crew_error": _crew_error,
    }


@app.post("/api/run", response_model=CodeResponse)
async def run_pipeline(req: CodeRequest):
    crew = await get_crew()
    request_id = uuid.uuid4().hex[:8]
    start = time.time()
    inputs = {
        "user_request": req.user_request,
        "language":     req.language,
        "topic":        req.topic,
    }
    result = await _run_with_timeout(crew.run, inputs)
    return CodeResponse(
        request_id  = request_id,
        result      = result,
        elapsed_sec = round(time.time() - start, 2),
    )


@app.post("/api/plan")
async def plan_only(req: CodeRequest):
    crew = await get_crew()
    inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}
    result = await _run_with_timeout(
        lambda: str(Crew(
            agents=[crew.senior_architect()],
            tasks=[crew.planning_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.post("/api/code")
async def code_only(req: CodeRequest):
    crew = await get_crew()
    technical_design = req.technical_design
    if not technical_design.strip():
        plan_inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}
        technical_design = await _run_with_timeout(
            lambda: str(Crew(
                agents=[crew.senior_architect()],
                tasks=[crew.planning_task()],
                process=Process.sequential, verbose=False,
            ).kickoff(inputs=plan_inputs))
        )
    inputs = {
        "user_request":     req.user_request,
        "language":         req.language,
        "topic":            req.topic,
        "technical_design": technical_design,
    }
    result = await _run_with_timeout(
        lambda: str(Crew(
            agents=[crew.senior_developer()],
            tasks=[crew.coding_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.get("/api/models")
async def list_models():
    coder_model = os.getenv("OLLAMA_CODER_MODEL", "qwen2.5-coder:14b")
    return {
        "router":  "qwen2.5:1.5b (Ollama local)",
        "planner": "auto via FreeLLM (frontier)",
        "coder":   f"{coder_model} (Ollama local)",
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def oai_list_models():
    return {
        "object": "list",
        "data": [
            {"id": "coding-agency", "object": "model", "owned_by": "local"},
            {"id": "coding-plan",   "object": "model", "owned_by": "local"},
            {"id": "coding-code",   "object": "model", "owned_by": "local"},
        ]
    }


@app.post("/v1/chat/completions")
async def oai_chat_completions(
    req: OAIChatRequest,
    authorization: str | None = Header(default=None),
):
    user_request = _extract_user_request(req.messages)
    language     = _parse_language_from_text(user_request)
    native_req   = CodeRequest(user_request=user_request, language=language)

    if req.model == "coding-plan":
        resp    = await plan_only(native_req)
        content = resp["result"]
    elif req.model == "coding-code":
        resp    = await code_only(native_req)
        content = resp["result"]
    else:
        code_resp = await run_pipeline(native_req)
        content   = code_resp.result

    if req.stream:
        return StreamingResponse(
            _stream_oai_response(content, req.model),
            media_type="text/event-stream",
        )
    return JSONResponse(_oai_response(content, req.model))
