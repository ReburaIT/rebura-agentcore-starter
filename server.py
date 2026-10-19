"""
AgentCore Demo — FastAPI web server (Strands + AgentCore edition)

Serves the demo UI at GET / and exposes:
  GET  /api/status      — runtime status
  GET  /api/meta        — scenario metadata
  GET  /api/models      — available inference profiles
  POST /api/invoke      — streaming agent invoke (text/event-stream)
  POST /api/cancel      — abort an in-progress invoke
  GET  /api/context     — last invocation details (for context drawer)
  POST /api/credentials — hot-swap AWS credentials
  GET  /api/credentials — current credential status

Run:
    python server.py
"""

import asyncio
import configparser
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import boto3
from dotenv import load_dotenv

load_dotenv()

# python_repl asks for a y/n confirmation in the terminal by default, which cannot work
# inside a web server thread (it fails with "Application is already running").
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

# Suppress INFO-level chatter from Strands and its tools so the server
# terminal stays clean.  Tool output still reaches the browser via SSE.
for _noisy in ("strands", "strands_tools", "botocore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import http_request

try:
    from strands_tools import python_repl
except ModuleNotFoundError:  # Windows: strands_tools.python_repl needs fcntl/pty
    from repl_fallback import python_repl

# ── Configuration ──────────────────────────────────────────────────────────────

REGION       = os.environ.get("AGENTCORE_REGION",       "eu-central-1")
MEMORY_ID    = os.environ.get("AGENTCORE_MEMORY_ID",    "")
MODEL_NOVA   = "eu.amazon.nova-pro-v1:0"
MODEL_SONNET = "eu.anthropic.claude-sonnet-4-6"
DEFAULT_MODEL = MODEL_NOVA

# ── AWS clients ────────────────────────────────────────────────────────────────

_boto_session: boto3.Session | None = None   # None = use environment default

def _make_bedrock_cp(session: boto3.Session | None = None):
    s = session or boto3.Session()
    return s.client("bedrock", region_name=REGION)

bedrock_cp = _make_bedrock_cp()
_active_profile: str | None = None

def _reinit_clients(
    aws_access_key_id: str,
    aws_secret_access_key: str,
    aws_session_token: str | None,
) -> None:
    global bedrock_cp, _boto_session
    _boto_session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
        region_name=REGION,
    )
    bedrock_cp = _make_bedrock_cp(_boto_session)
    # Clear stored agents so they are rebuilt with the new credentials.
    _agent_sessions.clear()
    _session_callbacks.clear()


def _agentcore_client():
    """Return a bedrock-agentcore boto3 client using current credentials."""
    s = _boto_session or boto3.Session()
    return s.client("bedrock-agentcore", region_name=REGION)


_MEMORY_NAMESPACE = "workshop"


@tool
def agentcore_memory(action: str, content: str = "", query: str = "") -> dict:
    """Persistent memory backed by Amazon Bedrock AgentCore Memory.

    action='record'   — store a fact (content required)
    action='retrieve' — semantic search for memories (query required)
    action='list'     — list all stored memories
    """
    if not MEMORY_ID:
        return {"status": "error", "message": "AGENTCORE_MEMORY_ID is not configured"}

    client = _agentcore_client()

    try:
        if action == "record":
            r = client.batch_create_memory_records(
                memoryId=MEMORY_ID,
                records=[{
                    "requestIdentifier": str(uuid.uuid4()),
                    "namespaces": [_MEMORY_NAMESPACE],
                    "content": {"text": content},
                    "timestamp": datetime.now(timezone.utc),
                }],
            )
            ok = r.get("successfulRecords", [])
            bad = r.get("failedRecords", [])
            if ok:
                return {"status": "success", "stored": ok[0].get("memoryRecordId", "ok"), "failed": len(bad)}
            return {"status": "error", "failed": bad}

        if action == "retrieve":
            r = client.retrieve_memory_records(
                memoryId=MEMORY_ID,
                namespace=_MEMORY_NAMESPACE,
                searchCriteria={"searchQuery": query},
                maxResults=10,
            )
            records = r.get("memoryRecordSummaries", [])
            return {
                "status": "success",
                "count": len(records),
                "memories": [rec["content"]["text"] for rec in records],
            }

        if action == "list":
            r = client.list_memory_records(
                memoryId=MEMORY_ID,
                namespace=_MEMORY_NAMESPACE,
            )
            records = r.get("memoryRecordSummaries", [])
            return {
                "status": "success",
                "count": len(records),
                "memories": [rec["content"]["text"] for rec in records],
            }

        return {"status": "error", "message": f"Unknown action '{action}'. Use: record, retrieve, list"}

    except Exception as exc:
        return {"status": "error", "message": str(exc)}

# ── Strands helpers ────────────────────────────────────────────────────────────

def _make_model(model_id: str) -> BedrockModel:
    if _boto_session:
        # region is already embedded in the session — don't pass both
        return BedrockModel(
            model_id=model_id,
            boto_session=_boto_session,
            streaming=True,
        )
    return BedrockModel(
        model_id=model_id,
        region_name=REGION,
        streaming=True,
    )


class _DelegatingCallback:
    """Mutable wrapper around a per-request callback.

    Agents are cached per session so conversation history is preserved.
    Each new SSE request injects a fresh delegate so tokens flow to the
    current HTTP response without recreating the agent.
    """
    def __init__(self):
        self._delegate = None

    def __call__(self, **kwargs):
        if self._delegate:
            self._delegate(**kwargs)


# (session_key → Agent) — preserves conversation history across turns.
_agent_sessions: dict[str, Agent] = {}
_session_callbacks: dict[str, _DelegatingCallback] = {}


def _get_agent(session_id: str, mode: str, model_id: str) -> tuple[Agent, _DelegatingCallback]:
    key = f"{session_id}:{mode}:{model_id}"
    if key not in _agent_sessions:
        cb = _DelegatingCallback()
        _session_callbacks[key] = cb
        _agent_sessions[key] = _build_agent(mode, model_id, cb, session_id)
    return _agent_sessions[key], _session_callbacks[key]


def _build_agent(mode: str, model_id: str, cb: _DelegatingCallback, session_id: str) -> Agent:
    model = _make_model(model_id)

    if mode == "chat":
        return Agent(model=model, system_prompt=_CHAT_SYSTEM, callback_handler=cb)

    if mode == "analyst":
        return Agent(
            model=model,
            tools=[python_repl],
            system_prompt=_ANALYST_SYSTEM,
            callback_handler=cb,
        )

    if mode == "researcher":
        return Agent(
            model=model,
            tools=[http_request],
            system_prompt=_RESEARCHER_SYSTEM,
            callback_handler=cb,
        )

    if mode == "multi-agent":
        sub_tools = _make_sub_agent_tools(model_id)
        return Agent(
            model=model,
            tools=sub_tools,
            system_prompt=_MULTIAGENT_SYSTEM,
            callback_handler=cb,
        )

    if mode == "memory":
        if not MEMORY_ID:
            raise ValueError(
                "AGENTCORE_MEMORY_ID is not set. "
                "Create a memory store and set the env var:\n"
                "  export AGENTCORE_MEMORY_ID=<memory-id>"
            )
        return Agent(
            model=model,
            tools=[agentcore_memory],
            system_prompt=_MEMORY_SYSTEM,
            callback_handler=cb,
        )

    raise ValueError(f"Unknown mode: {mode!r}")


def _make_sub_agent_tools(model_id: str) -> list:
    """Return specialist sub-agent tools for the multi-agent supervisor."""

    @tool
    def research_specialist(query: str) -> str:
        """Research current information from the web using live HTTP requests.
        Use this for any question that requires up-to-date facts, news, or pricing."""
        agent = Agent(
            model=_make_model(model_id),
            tools=[http_request],
            system_prompt=(
                "You are a web research specialist. Use http_request to fetch live information. "
                "Always cite the URLs you visited. Be concise and factual."
            ),
        )
        return str(agent(query))

    @tool
    def data_analyst(task: str) -> str:
        """Analyse data or perform calculations using Python code execution.
        Use this for maths, statistics, data processing, or generating summaries from numbers."""
        agent = Agent(
            model=_make_model(model_id),
            tools=[python_repl],
            system_prompt=(
                "You are a data analyst. Use python_repl for every calculation — never compute mentally. "
                "Show the code and the output, then explain what the results mean."
            ),
        )
        return str(agent(task))

    return [research_specialist, data_analyst]


# ── Datasets / scenarios ───────────────────────────────────────────────────────

_ANALYST_DATASETS = [
    {
        "name": "SaaS Revenue 2025",
        "prompt": (
            "Monthly revenue for a SaaS company in 2025 (£000s):\n"
            "Jan=142, Feb=158, Mar=201, Apr=189, May=234, Jun=267,\n"
            "Jul=243, Aug=219, Sep=278, Oct=312, Nov=289, Dec=356\n\n"
            "Use python_repl to:\n"
            "1. Total annual revenue and mean monthly revenue.\n"
            "2. Highest and lowest months.\n"
            "3. Month-over-month growth rates (%).\n"
            "4. Best-performing quarter (Q1–Q4) by total revenue.\n"
            "5. A simple ASCII bar chart of monthly revenue."
        ),
    },
    {
        "name": "Website Traffic Funnel",
        "prompt": (
            "Weekly website funnel data for Q4 2025:\n"
            "Week  Visitors  Signups  Trials  Paid\n"
            "W1    12400     310      87      14\n"
            "W2    13800     345      102     19\n"
            "W3    15200     398      118     22\n"
            "W4    11900     278      74      11\n"
            "W5    16700     445      134     28\n"
            "W6    18200     512      158     31\n\n"
            "Use python_repl to:\n"
            "1. Calculate conversion rates at each funnel stage per week.\n"
            "2. Find the week with the best visitor-to-paid conversion.\n"
            "3. Compute the average drop-off % between each funnel stage.\n"
            "4. Project Q1 paid conversions if traffic grows 15% week-on-week."
        ),
    },
    {
        "name": "Server Latency Log",
        "prompt": (
            "API response latency (ms) sampled hourly over 24h:\n"
            "Hour: 0  1  2  3  4  5  6  7  8  9  10 11 12 13 14 15 16 17 18 19 20 21 22 23\n"
            "p50:  42 38 35 33 31 29 35 52 87 143 198 201 189 176 182 195 212 208 187 156 121 98 72 55\n"
            "p99:  98 87 82 79 74 71 84 142 287 412 589 601 578 521 534 568 624 611 553 469 358 294 201 148\n\n"
            "Use python_repl to:\n"
            "1. Identify peak latency hours and the morning spike size.\n"
            "2. Calculate the p99/p50 ratio per hour to find degradation patterns.\n"
            "3. Find hours where p99 exceeds 3× the overnight baseline.\n"
            "4. Suggest a traffic-shaping window based on the data."
        ),
    },
]

_MULTIAGENT_SCENARIOS = [
    {
        "name": "Research + Analyse",
        "prompt": (
            "Research the current on-demand pricing for Amazon Bedrock Nova Pro in eu-central-1, "
            "then calculate the cost of running 1,000 conversations per day, "
            "each averaging 500 input tokens and 800 output tokens."
        ),
    },
    {
        "name": "News + Sentiment",
        "prompt": (
            "Find the three most recent major announcements from AWS about AI agents, "
            "then analyse and score the sentiment of each announcement "
            "(positive / neutral / negative) with a brief reason."
        ),
    },
    {
        "name": "Compare + Calculate",
        "prompt": (
            "Look up the key differences between Strands Agents and LangGraph, "
            "then produce a scored comparison table (1–5) across five dimensions: "
            "ease of use, AWS integration, multi-agent support, observability, and community."
        ),
    },
]

_MEMORY_SCENARIOS = [
    {
        "name": "Store preferences",
        "prompt": "Remember that I prefer Python for data engineering and TypeScript for frontend. I work in eu-central-1 and prefer Nova Pro for cost-efficient tasks.",
    },
    {
        "name": "Recall preferences",
        "prompt": "What do you remember about my technology preferences and AWS region?",
    },
    {
        "name": "Store workshop context",
        "prompt": "Remember that we are running an AWS AgentCore and Strands workshop. The audience are experienced AWS developers who are new to agentic AI frameworks.",
    },
    {
        "name": "Recall all",
        "prompt": "List everything you currently have stored in memory about me.",
    },
]

# ── System prompts ─────────────────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are a knowledgeable, friendly assistant. "
    "Give thorough, detailed answers — use examples and concrete details."
)

_ANALYST_SYSTEM = (
    "You are a data analyst. ALWAYS use python_repl for every calculation — "
    "never compute mentally. Show the code, run it, present the actual output, "
    "and write a brief interpretation of what each result means in plain English. "
    "Variables do not reliably persist between python_repl calls, so make each call a single "
    "self-contained script that defines its own data and prints its results; prefer one call for the whole task."
)

_RESEARCHER_SYSTEM = (
    "You are a focused research assistant with live web access via http_request.\n\n"
    "For every research question:\n"
    "1. Make an http_request GET to a relevant URL.\n"
    "2. Read the response, extract the key facts.\n"
    "3. Make at least 2–3 requests to different sources before answering.\n"
    "4. Always cite the exact URLs you visited.\n\n"
    "Never rely on your training-data memory for facts that can change (prices, versions, dates). "
    "If a URL returns an error, skip it and try an alternative source."
)

_MULTIAGENT_SYSTEM = (
    "You are an orchestrator with two specialist sub-agents at your disposal:\n\n"
    "• research_specialist — for current web information, news, pricing, documentation.\n"
    "• data_analyst — for calculations, statistics, data processing, comparisons.\n\n"
    "Break complex tasks down and delegate to the right specialist. "
    "Combine their outputs into a single, well-structured answer. "
    "Always explain which specialist you used and why."
)

_MEMORY_SYSTEM = (
    "You are a helpful assistant with persistent memory backed by Amazon Bedrock AgentCore Memory.\n\n"
    "Use the agentcore_memory tool for all memory operations:\n"
    "  action='record'   — store a fact or preference (content= the text to store)\n"
    "  action='retrieve' — semantic search for relevant memories (query= what to look for)\n"
    "  action='list'     — list ALL stored memories\n\n"
    "When asked to remember something, use action='record' and confirm what was stored.\n"
    "When asked what you know or remember, use action='retrieve' with a descriptive query, "
    "then if results are sparse also try action='list' to show everything.\n"
    "Memory persists across sessions — information stored now will be available in future conversations."
)

# ── Streaming infrastructure ───────────────────────────────────────────────────

_THINK_RE = re.compile(r"<thinking>.*?</thinking>\s*", re.DOTALL)

_last_context: dict = {}
_cancel_events: dict[str, threading.Event] = {}


def _register_cancel(session_id: str) -> threading.Event:
    ev = threading.Event()
    _cancel_events[session_id] = ev
    return ev


def _unregister_cancel(session_id: str) -> None:
    _cancel_events.pop(session_id, None)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _make_strands_callback(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, cancel_event: threading.Event):
    """Return a Strands callback_handler that forwards events to the SSE queue."""

    def callback(**kwargs):
        if cancel_event.is_set():
            return

        data     = kwargs.get("data", "")
        event    = kwargs.get("event", {})

        if data:
            clean = _THINK_RE.sub("", data)
            if clean:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "token", "text": clean})

        tool_use = (
            event.get("contentBlockStart", {})
                 .get("start", {})
                 .get("toolUse")
        )
        if tool_use:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "type": "tool_start",
                    "name": tool_use.get("name", "tool"),
                    "tool_use_id": tool_use.get("toolUseId", ""),
                },
            )

    return callback


def _stream_strands_threaded(
    agent: Agent,
    cb: _DelegatingCallback,
    prompt: str,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    cancel_event: threading.Event,
    context_meta: dict,
) -> None:
    cb._delegate = _make_strands_callback(loop, queue, cancel_event)
    try:
        result = agent(prompt)
        global _last_context
        _last_context = {
            **context_meta,
            "response_preview": str(result)[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "stop", "reason": "end_turn", "pending_calls": []}
        )
    except Exception as exc:
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(exc)})
    finally:
        cb._delegate = None
        loop.call_soon_threadsafe(queue.put_nowait, None)


async def _run_stream(
    agent: Agent,
    cb: _DelegatingCallback,
    prompt: str,
    session_id: str,
    context_meta: dict,
) -> AsyncGenerator[str, None]:
    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel_event = _register_cancel(session_id)

    thread = threading.Thread(
        target=_stream_strands_threaded,
        args=(agent, cb, prompt, loop, queue, cancel_event, context_meta),
        daemon=True,
    )
    thread.start()

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            if cancel_event.is_set() and event.get("type") not in ("stop", "error"):
                continue
            yield _sse(event)
    finally:
        _unregister_cancel(session_id)

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="AgentCore Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ───────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(Path(__file__).parent / "index.html")

# ── /api/status ────────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    return {
        "status": "READY",
        "default_model": DEFAULT_MODEL,
        "region": REGION,
        "memory_configured": bool(MEMORY_ID),
        "memory_id": MEMORY_ID or None,
    }

# ── /api/meta ──────────────────────────────────────────────────────────────────

@app.get("/api/meta")
def get_meta():
    return {
        "datasets":          [{"index": i, "name": d["name"]} for i, d in enumerate(_ANALYST_DATASETS)],
        "multiagent_scenarios": [{"index": i, "name": s["name"]} for i, s in enumerate(_MULTIAGENT_SCENARIOS)],
        "memory_scenarios":  [{"index": i, "name": s["name"]} for i, s in enumerate(_MEMORY_SCENARIOS)],
        "chat_examples": [
            "Explain how transformer attention works, then I'll ask follow-up questions",
            "What are the key differences between RAG and fine-tuning for LLMs?",
            "I'm planning a trip to Lisbon. Start with the top 3 must-see sights and why each is worth it",
            "What is the difference between a Kubernetes Deployment and a StatefulSet?",
        ],
        "researcher_examples": [
            "What are the key differences between Amazon Bedrock AgentCore and LangGraph?",
            "What is the current on-demand pricing for Amazon Bedrock Nova Pro in eu-central-1?",
            "What were the most significant AI agent announcements from AWS in 2025?",
            "What Python libraries come pre-installed in the AgentCore Code Interpreter sandbox?",
        ],
    }

# ── /api/models ────────────────────────────────────────────────────────────────

@app.get("/api/models")
def list_models():
    try:
        profiles: list = []
        kwargs: dict = {"typeEquals": "SYSTEM_DEFINED", "maxResults": 100}
        while True:
            resp = bedrock_cp.list_inference_profiles(**kwargs)
            profiles.extend(resp.get("inferenceProfileSummaries", []))
            if "nextToken" not in resp:
                break
            kwargs["nextToken"] = resp["nextToken"]
        models = [
            {"id": p["inferenceProfileId"], "name": p["inferenceProfileName"]}
            for p in profiles if p.get("status") == "ACTIVE"
        ]
        models.sort(key=lambda m: m["name"].lower())
        return {"models": models, "default": DEFAULT_MODEL}
    except Exception as exc:
        return {
            "models": [
                {"id": MODEL_NOVA,   "name": "Amazon Nova Pro (eu cross-region)"},
                {"id": MODEL_SONNET, "name": "Claude Sonnet 4.6 (eu cross-region)"},
            ],
            "default": DEFAULT_MODEL,
            "error": str(exc),
        }

# ── Request models ─────────────────────────────────────────────────────────────

class InvokeRequest(BaseModel):
    session_id: str
    mode: str
    prompt: str = ""
    model: str | None = None
    dataset: int | None = None
    scenario: int | None = None

class CredentialsRequest(BaseModel):
    credentials_text: str

# ── /api/invoke ────────────────────────────────────────────────────────────────

@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    mode     = req.mode
    model_id = req.model or DEFAULT_MODEL

    context_meta = {
        "mode":       mode,
        "session_id": req.session_id,
        "model_id":   model_id,
        "framework":  "Strands Agents SDK",
    }

    try:
        # ── chat ──
        if mode == "chat":
            prompt = req.prompt or "Hello"
            context_meta["system_prompt"] = _CHAT_SYSTEM
            context_meta["tools"] = []

        # ── analyst ──
        elif mode == "analyst":
            idx = req.dataset if req.dataset is not None else 0
            idx = max(0, min(idx, len(_ANALYST_DATASETS) - 1))
            prompt = req.prompt if req.prompt else _ANALYST_DATASETS[idx]["prompt"]
            context_meta["system_prompt"] = _ANALYST_SYSTEM
            context_meta["tools"] = ["python_repl"]

        # ── researcher ──
        elif mode == "researcher":
            prompt = req.prompt or "Tell me about Amazon Bedrock AgentCore."
            context_meta["system_prompt"] = _RESEARCHER_SYSTEM
            context_meta["tools"] = ["http_request"]

        # ── multi-agent ──
        elif mode == "multi-agent":
            idx = req.scenario if req.scenario is not None else 0
            idx = max(0, min(idx, len(_MULTIAGENT_SCENARIOS) - 1))
            prompt = req.prompt if req.prompt else _MULTIAGENT_SCENARIOS[idx]["prompt"]
            context_meta["system_prompt"] = _MULTIAGENT_SYSTEM
            context_meta["tools"] = ["research_specialist (sub-agent)", "data_analyst (sub-agent)"]

        # ── memory ──
        elif mode == "memory":
            idx = req.scenario if req.scenario is not None else 0
            idx = max(0, min(idx, len(_MEMORY_SCENARIOS) - 1))
            prompt = req.prompt if req.prompt else _MEMORY_SCENARIOS[idx]["prompt"]
            context_meta["system_prompt"] = _MEMORY_SYSTEM
            context_meta["tools"] = ["agentcore_memory"]
            context_meta["memory_id"] = MEMORY_ID or "(not configured)"

        else:
            async def err_gen():
                yield _sse({"type": "error", "message": f"Unknown mode: {mode}"})
            return StreamingResponse(err_gen(), media_type="text/event-stream")

        agent, cb = _get_agent(req.session_id, mode, model_id)
        context_meta["prompt"] = prompt

    except ValueError as exc:
        err_msg = str(exc)
        async def err_gen():
            yield _sse({"type": "error", "message": err_msg})
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    return StreamingResponse(
        _run_stream(agent, cb, prompt, req.session_id, context_meta),
        media_type="text/event-stream",
    )

# ── /api/cancel ────────────────────────────────────────────────────────────────

@app.post("/api/cancel")
async def cancel_invoke(body: dict):
    session_id = body.get("session_id", "")
    ev = _cancel_events.get(session_id)
    if ev:
        ev.set()
        return {"cancelled": True, "session_id": session_id}
    return {"cancelled": False, "session_id": session_id}

# ── /api/context ───────────────────────────────────────────────────────────────

@app.get("/api/context")
def get_context():
    return _last_context

# ── /api/credentials ───────────────────────────────────────────────────────────

@app.post("/api/credentials")
def set_credentials(req: CredentialsRequest):
    global _active_profile
    text = req.credentials_text.strip()
    cfg  = configparser.ConfigParser()
    try:
        cfg.read_string(text)
    except configparser.Error as e:
        raise HTTPException(status_code=400, detail=f"INI parse error: {e}")

    if not cfg.sections():
        raise HTTPException(status_code=400, detail="No sections found in credentials text")

    section = cfg.sections()[0]
    key_id  = cfg.get(section, "aws_access_key_id",     fallback="").strip()
    secret  = cfg.get(section, "aws_secret_access_key", fallback="").strip()
    token   = cfg.get(section, "aws_session_token",     fallback="").strip() or None

    if not key_id or not secret:
        raise HTTPException(status_code=400, detail="aws_access_key_id or aws_secret_access_key missing")

    _reinit_clients(key_id, secret, token)
    _active_profile = section

    return {
        "profile":      section,
        "key_id_prefix": key_id[:8] + "…",
        "has_token":    token is not None,
    }

@app.get("/api/credentials")
def get_credentials():
    return {
        "active_profile": _active_profile,
        "using_env_creds": _active_profile is None,
    }

# ── /api/seed-memory ───────────────────────────────────────────────────────────

_SEED_RECORDS = [
    "User prefers Python for data engineering and machine learning. Uses TypeScript for frontend work and AWS CDK for infrastructure.",
    "User works primarily in the eu-central-1 AWS region. Prefers Amazon Nova Pro for cost-efficient tasks and Claude Sonnet for complex reasoning.",
    "User is an AWS consultant at Rebura, running a workshop on Amazon Bedrock AgentCore and Strands Agents SDK. The audience are experienced AWS developers who are new to agentic AI frameworks.",
    "User finds multi-agent orchestration patterns most interesting for production use cases. Prefers composable, tool-based agent architectures over monolithic designs.",
]

@app.post("/api/seed-memory")
def seed_memory():
    """Pre-populate AgentCore Memory with demo facts (use before demoing recall)."""
    if not MEMORY_ID:
        raise HTTPException(status_code=400, detail="AGENTCORE_MEMORY_ID is not configured")

    client = _agentcore_client()
    records = [
        {
            "requestIdentifier": str(uuid.uuid4()),
            "namespaces": [_MEMORY_NAMESPACE],
            "content": {"text": text},
            "timestamp": datetime.now(timezone.utc),
        }
        for text in _SEED_RECORDS
    ]

    try:
        r = client.batch_create_memory_records(memoryId=MEMORY_ID, records=records)
        ok  = r.get("successfulRecords", [])
        bad = r.get("failedRecords", [])
        return {
            "seeded": len(ok),
            "failed": len(bad),
            "ids": [rec.get("memoryRecordId") for rec in ok],
            "errors": [rec.get("errorMessage") for rec in bad],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    # Bind to localhost only: this server can run Python code and make HTTP requests
    # for anyone who can reach it, so never expose it on a shared network.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
