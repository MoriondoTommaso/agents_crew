"""
FastAPI server for the Hybrid Coding Agency.

Exposes two types of endpoints:
  1. /v1/chat/completions  — OpenAI-compatible (for harness like Open-Claw, Aider, Continue.dev)
  2. /api/*                — Native endpoints with full pipeline control

Start with:
    uv run uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

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
    description="LLM orchestration server — OpenAI-compatible + native API",
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CodeRequest(BaseModel):
    user_request: str   = Field(..., description="What to build")
    language:     str   = Field(default="Python", description="Target language")
    topic:        str   = Field(default="software", description="Domain/context")
    healing:      bool  = Field(default=True,  description="Enable self-healing QA loop")

class CodeResponse(BaseModel):
    request_id:  str
    result:      str
    iterations:  int | None = None
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
    """
    Extract the last user message as the coding request.
    Raises HTTP 422 if messages is empty.
    """
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
        import json
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0)

    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    yield "data: [DONE]\n\n"


async def _run_with_timeout(fn, *args):
    """Run a blocking function in a thread pool with a hard timeout."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args),
            timeout=LLM_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Pipeline timed out after {LLM_TIMEOUT_SEC}s. Try a simpler request or increase LLM_TIMEOUT_SEC."
        )


# ---------------------------------------------------------------------------
# Native API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "crew_ready": crew_instance is not None}


@app.post("/api/run", response_model=CodeResponse)
async def run_pipeline(req: CodeRequest):
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    request_id = uuid.uuid4().hex[:8]
    start      = time.time()

    inputs = {
        "user_request":    req.user_request,
        "language":        req.language,
        "topic":           req.topic,
        "review_feedback": "",
    }

    if req.healing:
        result = await _run_with_timeout(crew_instance.run_with_healing, inputs)
    else:
        result = await _run_with_timeout(
            lambda: str(crew_instance.crew().kickoff(inputs=inputs))
        )

    return CodeResponse(
        request_id  = request_id,
        result      = result,
        elapsed_sec = round(time.time() - start, 2),
    )


@app.post("/api/plan")
async def plan_only(req: CodeRequest):
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
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
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
    inputs = {"user_request": req.user_request, "language": req.language,
              "topic": req.topic, "review_feedback": ""}

    result = await _run_with_timeout(
        lambda: str(Crew(
            agents=[crew_instance.senior_developer()],
            tasks=[crew_instance.coding_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.post("/api/review")
async def review_only(req: CodeRequest):
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
    inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}

    result = await _run_with_timeout(
        lambda: str(Crew(
            agents=[crew_instance.qa_engineer()],
            tasks=[crew_instance.review_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.get("/api/models")
async def list_models():
    return {
        "router":   "qwen2.5:1.5b (Ollama local)",
        "planner":  "auto via FreeLLM (frontier)",
        "coder":    "qwen2.5-coder:12b (Ollama local)",
        "reviewer": "auto via FreeLLM (frontier)",
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def oai_list_models():
    return {
        "object": "list",
        "data": [
            {"id": "coding-agency",   "object": "model", "owned_by": "local"},
            {"id": "coding-plan",     "object": "model", "owned_by": "local"},
            {"id": "coding-code",     "object": "model", "owned_by": "local"},
            {"id": "coding-review",   "object": "model", "owned_by": "local"},
        ]
    }


@app.post("/v1/chat/completions")
async def oai_chat_completions(
    req: OAIChatRequest,
    authorization: str | None = Header(default=None),
):
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    user_request = _extract_user_request(req.messages)
    language     = _parse_language_from_text(user_request)

    native_req = CodeRequest(
        user_request = user_request,
        language     = language,
        healing      = req.model != "coding-code",
    )

    if req.model == "coding-plan":
        resp    = await plan_only(native_req)
        content = resp["result"]
    elif req.model == "coding-code":
        resp    = await code_only(native_req)
        content = resp["result"]
    elif req.model == "coding-review":
        resp    = await review_only(native_req)
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
