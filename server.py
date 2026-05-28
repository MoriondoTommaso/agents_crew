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
# App lifecycle
# ---------------------------------------------------------------------------

crew_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global crew_instance
    from crew import CodingAgencyCrew
    print("[Server] Warming up CodingAgencyCrew and SMLRouter...")
    crew_instance = CodingAgencyCrew()
    print("[Server] Ready.")
    yield
    crew_instance = None


app = FastAPI(
    title="Hybrid Coding Agency",
    description="SML router: plan→api_llm, code→local_llm. OpenAI-compatible.",
    version="0.4.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CodeRequest(BaseModel):
    user_request:     str  = Field(..., description="What to build")
    language:         str  = Field(default="Python", description="Target language")
    topic:            str  = Field(default="software", description="Domain/context")
    technical_design: str  = Field(default="", description="Pre-computed design doc (optional). If empty, /api/code will auto-generate one.")

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
    """Run a blocking function in a thread with a hard timeout → HTTP 504 on expiry."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args),
            timeout=LLM_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Pipeline timed out after {LLM_TIMEOUT_SEC}s. Increase LLM_TIMEOUT_SEC or simplify the request."
        )


# ---------------------------------------------------------------------------
# Native API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "crew_ready": crew_instance is not None}


@app.post("/api/run", response_model=CodeResponse)
async def run_pipeline(req: CodeRequest):
    """Full plan → code pipeline (single pass)."""
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    request_id = uuid.uuid4().hex[:8]
    start = time.time()
    inputs = {
        "user_request": req.user_request,
        "language":     req.language,
        "topic":        req.topic,
    }
    result = await _run_with_timeout(crew_instance.run, inputs)
    return CodeResponse(
        request_id  = request_id,
        result      = result,
        elapsed_sec = round(time.time() - start, 2),
    )


@app.post("/api/plan")
async def plan_only(req: CodeRequest):
    """Planning task only (senior_architect → api_llm)."""
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}
    result = await _run_with_timeout(
        lambda: str(Crew(
            agents=[crew_instance.senior_architect()],
            tasks=[crew_instance.planning_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.post("/api/code")
async def code_only(req: CodeRequest):
    """
    Coding task only (senior_developer → local_llm).

    If `technical_design` is not provided (empty string), the endpoint
    auto-generates a minimal plan via the api_llm before coding.
    This ensures the coder always receives the context it needs.
    """
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    # Auto-plan if no design was provided
    technical_design = req.technical_design
    if not technical_design.strip():
        plan_inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}
        technical_design = await _run_with_timeout(
            lambda: str(Crew(
                agents=[crew_instance.senior_architect()],
                tasks=[crew_instance.planning_task()],
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
            agents=[crew_instance.senior_developer()],
            tasks=[crew_instance.coding_task()],
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
    """
    OpenAI-compatible endpoint. Model routing:
      coding-agency  → full plan + code (default)
      coding-plan    → planning only
      coding-code    → coding only (local model, auto-plans if no design provided)
    """
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    user_request = _extract_user_request(req.messages)
    language     = _parse_language_from_text(user_request)
    native_req   = CodeRequest(user_request=user_request, language=language)

    if req.model == "coding-plan":
        resp    = await plan_only(native_req)
        content = resp["result"]
    elif req.model == "coding-code":
        resp    = await code_only(native_req)
        content = resp["result"]
    else:  # coding-agency or any unknown model
        code_resp = await run_pipeline(native_req)
        content   = code_resp.result

    if req.stream:
        return StreamingResponse(
            _stream_oai_response(content, req.model),
            media_type="text/event-stream",
        )
    return JSONResponse(_oai_response(content, req.model))
