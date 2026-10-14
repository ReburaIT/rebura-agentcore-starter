"""Starter agent for the AgentCore hackathon.

A small Strands agent, written so you can change it and then deploy it to
AgentCore Runtime unchanged. It already has:

  1. a custom tool                (replace `lookup_policy` with your own)
  2. optional AgentCore Memory    (set AGENTCORE_MEMORY_ID)
  3. optional external MCP tools  (set MCP_SERVER_URL, e.g. an AgentCore Gateway)

Run it locally in your terminal:

    python starter/agent.py "What is the refund policy?"

Run it as a local AgentCore Runtime server on http://localhost:8080:

    python starter/agent.py --serve
    curl -s localhost:8080/invocations -H 'content-type: application/json' \
         -d '{"prompt": "What is the refund policy?", "session_id": "demo-session-0001", "user_id": "me"}'
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

REGION = os.environ.get("AGENTCORE_REGION", "eu-central-1")
MODEL_ID = os.environ.get("MODEL_ID", "eu.amazon.nova-pro-v1:0")
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")
MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")

SYSTEM_PROMPT = (
    "You are a helpful assistant for <your company / your use case>. "
    "Use your tools to answer; if a tool cannot answer, say so rather than guessing."
)


# ── 1. Your tools ─────────────────────────────────────────────────────────────
# A tool is a Python function with a docstring: the model reads the docstring to
# decide when to call it. Replace this stub with something real (call an API, query
# a database, read a file...).

_POLICIES = {
    "refund": "Refunds are available within 30 days of purchase with proof of purchase.",
    "shipping": "Standard shipping takes 3-5 working days. Express takes 1-2.",
}


@tool
def lookup_policy(topic: str) -> str:
    """Look up a company policy by topic (for example 'refund' or 'shipping')."""
    return _POLICIES.get(topic.lower().strip(), f"No policy found for '{topic}'. Known topics: {', '.join(_POLICIES)}")


# ── 2 + 3. Optional extras ────────────────────────────────────────────────────

def _memory_tools(user_id: str, session_id: str) -> list:
    """AgentCore Memory as a tool the agent can call. Skipped if no memory is configured."""
    if not MEMORY_ID:
        return []
    from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider

    return AgentCoreMemoryToolProvider(
        memory_id=MEMORY_ID, actor_id=user_id, session_id=session_id,
        namespace=f"/users/{user_id}", region=REGION,
    ).tools


def _mcp_client():
    """Connect to a remote MCP server (such as an AgentCore Gateway). Skipped if not configured."""
    if not MCP_SERVER_URL:
        return None
    from strands.tools.mcp import MCPClient

    headers = {"Authorization": f"Bearer {MCP_BEARER_TOKEN}"} if MCP_BEARER_TOKEN else None
    return MCPClient(url=MCP_SERVER_URL, headers=headers)


def run_agent(prompt: str, user_id: str = "local-user", session_id: str = "local-session-0001") -> str:
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    tools = [lookup_policy, *_memory_tools(user_id, session_id)]
    mcp = _mcp_client()

    if mcp is None:
        return str(Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT, callback_handler=None)(prompt))
    with mcp:  # keeps the MCP connection open while the agent runs
        tools += mcp.list_tools_sync()
        return str(Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT, callback_handler=None)(prompt))


# ── AgentCore Runtime entrypoint ──────────────────────────────────────────────
# `agentcore deploy` (or `python starter/agent.py --serve`) calls this for every request.

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    return {
        "result": run_agent(
            prompt=payload.get("prompt", "Hello"),
            user_id=payload.get("user_id", "local-user"),
            session_id=payload.get("session_id", "local-session-0001"),
        )
    }


if __name__ == "__main__":
    if "--serve" in sys.argv:
        app.run()
    else:
        question = " ".join(a for a in sys.argv[1:]) or "What is the refund policy?"
        print(run_agent(question))
