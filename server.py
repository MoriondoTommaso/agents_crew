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


# ---------------------------------------------------------------------------
# App lifecycle: warm up the crew on startup
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
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CodeRequest(BaseModel):
    """Native pipeline request."""
    user_request: str   = Field(..., description="What to build")
    language:     str   = Field(default="Python", description="Target language")
    topic:        str   = Field(default="software", description="Domain/context")
    healing:      bool  = Field(default=True,  description="Enable self-healing QA loop")

class CodeResponse(BaseModel):
    request_id:  str
    result:      str
    iterations:  int | None = None
    elapsed_sec: float


# OpenAI-compatible models
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
    Harnesses like Open-Claw send the full conversation history —
    we use only the last user turn as the active request.
    Raises HTTP 422 if messages is empty.
    """
    if not messages:
        raise HTTPException(status_code=422, detail="messages list must not be empty")
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    # No user message found — fall back to last message
    return messages[-1].content


def _parse_language_from_text(text: str) -> str:
    """Best-effort language detection from the request text."""
    text_lower = text.lower()
    for lang in ["python", "typescript", "javascript", "rust", "go", "java", "c++"]:
        if lang in text_lower:
            return lang.capitalize()
    return "Python"


def _oai_response(content: str, model: str = "coding-agency") -> dict:
    """Build an OpenAI-compatible chat completion response."""
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
    """Stream an OpenAI-compatible SSE response chunk by chunk."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    words = content.split(" ")
    for i, word in enumerate(words):
        delta   = word + (" " if i < len(words) - 1 else "")
        chunk   = {
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


# ---------------------------------------------------------------------------
# Native API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — confirms server + crew are ready."""
    return {"status": "ok", "crew_ready": crew_instance is not None}


@app.post("/api/run", response_model=CodeResponse)
async def run_pipeline(req: CodeRequest):
    """
    Full plan → code → review pipeline.
    Set healing=true to enable the self-healing QA loop (max 3 iterations).
    """
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

    try:
        loop = asyncio.get_event_loop()
        if req.healing:
            result = await loop.run_in_executor(
                None, crew_instance.run_with_healing, inputs
            )
        else:
            result = await loop.run_in_executor(
                None, lambda: str(crew_instance.crew().kickoff(inputs=inputs))
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return CodeResponse(
        request_id  = request_id,
        result      = result,
        elapsed_sec = round(time.time() - start, 2),
    )


@app.post("/api/plan")
async def plan_only(req: CodeRequest):
    """Run only the planning task (senior_architect)."""
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
    inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: str(Crew(
            agents=[crew_instance.senior_architect()],
            tasks=[crew_instance.planning_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.post("/api/code")
async def code_only(req: CodeRequest):
    """Run only the coding task (senior_developer, local model)."""
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
    inputs = {"user_request": req.user_request, "language": req.language,
              "topic": req.topic, "review_feedback": ""}

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: str(Crew(
            agents=[crew_instance.senior_developer()],
            tasks=[crew_instance.coding_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.post("/api/review")
async def review_only(req: CodeRequest):
    """Run only the review task (qa_engineer, frontier model)."""
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    from crewai import Crew, Process
    inputs = {"user_request": req.user_request, "language": req.language, "topic": req.topic}

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: str(Crew(
            agents=[crew_instance.qa_engineer()],
            tasks=[crew_instance.review_task()],
            process=Process.sequential, verbose=False,
        ).kickoff(inputs=inputs))
    )
    return {"result": result}


@app.get("/api/models")
async def list_models():
    """List the models in use — useful for harness introspection."""
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
    """OpenAI /v1/models stub — required by most harnesses."""
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
    """
    OpenAI-compatible chat completions endpoint.

    Model routing via 'model' field:
      - 'coding-agency'  → full plan+code+review pipeline (default)
      - 'coding-plan'    → planning only
      - 'coding-code'    → coding only (local model)
      - 'coding-review'  → review only

    Supports streaming (stream=true) for harnesses that expect SSE.
    """
    if crew_instance is None:
        raise HTTPException(status_code=503, detail="Crew not initialized")

    user_request = _extract_user_request(req.messages)  # raises 422 if empty
    language     = _parse_language_from_text(user_request)

    native_req = CodeRequest(
        user_request = user_request,
        language     = language,
        healing      = req.model != "coding-code",
    )

    if req.model == "coding-plan":
        resp = await plan_only(native_req)
        content = resp["result"]
    elif req.model == "coding-code":
        resp = await code_only(native_req)
        content = resp["result"]
    elif req.model == "coding-review":
        resp = await review_only(native_req)
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
