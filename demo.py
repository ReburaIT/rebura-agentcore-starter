#!/usr/bin/env python3
"""
AgentCore Demo — CLI (Strands + AgentCore edition)

Five interactive demo modes:
  1  chat         Multi-turn conversation
  2  analyst      Python REPL data analysis
  3  researcher   Live web research via HTTP
  4  multi-agent  Supervisor + specialist sub-agents
  5  memory       Persistent memory via AgentCore Memory
"""

import os
import sys
import textwrap

import boto3

try:  # pick up .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import http_request, python_repl
from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider

# ── Configuration ──────────────────────────────────────────────────────────────

REGION    = os.environ.get("AGENTCORE_REGION",    "eu-central-1")
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

MODEL_NOVA   = "eu.amazon.nova-pro-v1:0"
DEFAULT_MODEL = MODEL_NOVA

# ── Datasets ───────────────────────────────────────────────────────────────────

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
        "name": "Store a preference",
        "prompt": "Remember that I prefer Python for data work and TypeScript for frontend. Also note that I work in the eu-central-1 region.",
    },
    {
        "name": "Recall preferences",
        "prompt": "What do you remember about my technology preferences and AWS region?",
    },
    {
        "name": "Store workshop context",
        "prompt": "Remember that we are running an AWS AgentCore and Strands workshop. The audience are experienced AWS developers who are new to agentic AI.",
    },
]

# ── System prompts ─────────────────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are a knowledgeable, friendly assistant. Give thorough answers with concrete examples."
)

_ANALYST_SYSTEM = (
    "You are a data analyst. ALWAYS use python_repl for every calculation — never compute mentally. "
    "Show the code, run it, present the output, and interpret what the results mean. "
    "Variables do not reliably persist between python_repl calls, so make each call a single "
    "self-contained script that defines its own data and prints its results; prefer one call for the whole task."
)

_RESEARCHER_SYSTEM = (
    "You are a focused research assistant with live web access via http_request.\n"
    "For every research question, make at least 2–3 http_request GETs to different sources. "
    "Always cite the URLs you visited. Never rely on training data for facts that can change."
)

_MULTIAGENT_SYSTEM = (
    "You are an orchestrator with two specialist sub-agents:\n"
    "• research_specialist — for current web information, news, pricing, documentation.\n"
    "• data_analyst — for calculations, statistics, data processing.\n"
    "Break complex tasks down, delegate to the right specialist, and combine their outputs."
)

_MEMORY_SYSTEM = (
    "You are a helpful assistant with persistent memory via agent_core_memory.\n"
    "When asked to remember something, use agent_core_memory with action='record'.\n"
    "When asked what you remember, use agent_core_memory with action='retrieve'.\n"
    "Always confirm what you stored or retrieved."
)

# ── Colours ────────────────────────────────────────────────────────────────────

BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _hr(char: str = "─", width: int = 72) -> None:
    print(DIM + char * width + RESET)


def _header(title: str) -> None:
    _hr("═")
    print(f" {BOLD}{CYAN}{title}{RESET}")
    _hr("═")


def _section(title: str) -> None:
    print(f"\n{BOLD}{YELLOW}▸ {title}{RESET}")


# ── Strands helpers ────────────────────────────────────────────────────────────

def _make_model(model_id: str = DEFAULT_MODEL) -> BedrockModel:
    return BedrockModel(
        model_id=model_id,
        region_name=REGION,
        streaming=True,
    )


def _printing_callback(**kwargs):
    data      = kwargs.get("data", "")
    complete  = kwargs.get("complete", False)
    tool_use  = (
        kwargs.get("event", {})
              .get("contentBlockStart", {})
              .get("start", {})
              .get("toolUse")
    )
    if data:
        print(data, end="" if not complete else "\n", flush=True)
    if tool_use:
        print(f"\n{DIM}[tool: {tool_use.get('name', '?')}]{RESET}", flush=True)


def _make_sub_agent_tools(model_id: str) -> list:
    @tool
    def research_specialist(query: str) -> str:
        """Research current information from the web using live HTTP requests."""
        agent = Agent(
            model=_make_model(model_id),
            tools=[http_request],
            system_prompt="You are a web research specialist. Use http_request to fetch live information. Cite all URLs.",
        )
        return str(agent(query))

    @tool
    def data_analyst(task: str) -> str:
        """Analyse data or perform calculations using Python code execution."""
        agent = Agent(
            model=_make_model(model_id),
            tools=[python_repl],
            system_prompt="You are a data analyst. Use python_repl for every calculation. Show code and output.",
        )
        return str(agent(task))

    return [research_specialist, data_analyst]


# ── Mode implementations ───────────────────────────────────────────────────────

def run_chat(model_id: str = DEFAULT_MODEL) -> None:
    _header("Chat — Multi-turn Conversation")
    print(
        textwrap.dedent(f"""
        {DIM}Model: {model_id}
        Framework: Strands Agent (no tools — pure conversation)
        Demonstrates: persistent conversation history across turns
        Type 'exit' or 'quit' to return to the menu.{RESET}
        """)
    )
    agent = Agent(
        model=_make_model(model_id),
        system_prompt=_CHAT_SYSTEM,
        callback_handler=_printing_callback,
    )
    while True:
        _hr()
        try:
            prompt = input(f"{BOLD}You: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.lower() in ("exit", "quit", ""):
            break
        print(f"\n{GREEN}Agent:{RESET} ", end="", flush=True)
        agent(prompt)
        print()


def run_analyst(model_id: str = DEFAULT_MODEL) -> None:
    _header("Analyst — Python REPL Data Analysis")
    print(
        textwrap.dedent(f"""
        {DIM}Model: {model_id}
        Tools: python_repl (executes real Python code)
        Demonstrates: code-driven analysis with actual execution{RESET}
        """)
    )
    agent = Agent(
        model=_make_model(model_id),
        tools=[python_repl],
        system_prompt=_ANALYST_SYSTEM,
        callback_handler=_printing_callback,
    )

    while True:
        _section("Choose a dataset or type your own prompt")
        for i, d in enumerate(_ANALYST_DATASETS, 1):
            print(f"  {BOLD}{i}{RESET}  {d['name']}")
        print(f"  {BOLD}c{RESET}  Custom prompt")
        print(f"  {BOLD}q{RESET}  Return to menu")
        choice = input(f"\n{BOLD}Choice: {RESET}").strip().lower()

        if choice == "q" or choice == "":
            break
        if choice == "c":
            prompt = input(f"{BOLD}Prompt: {RESET}").strip()
        else:
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(_ANALYST_DATASETS):
                    raise ValueError
                prompt = _ANALYST_DATASETS[idx]["prompt"]
            except ValueError:
                print(f"{RED}Invalid choice.{RESET}")
                continue

        print(f"\n{DIM}Running analysis…{RESET}\n")
        agent(prompt)
        print()

        followup = input(f"{BOLD}Follow-up (Enter to continue): {RESET}").strip()
        if followup:
            agent(followup)
            print()


def run_researcher(model_id: str = DEFAULT_MODEL) -> None:
    _header("Researcher — Live Web Research")
    print(
        textwrap.dedent(f"""
        {DIM}Model: {model_id}
        Tools: http_request (live HTTP GET/POST)
        Demonstrates: real-time web research with citation{RESET}
        """)
    )
    agent = Agent(
        model=_make_model(model_id),
        tools=[http_request],
        system_prompt=_RESEARCHER_SYSTEM,
        callback_handler=_printing_callback,
    )

    examples = [
        "What are the key differences between Amazon Bedrock AgentCore and LangGraph?",
        "What is the current on-demand pricing for Amazon Bedrock Nova Pro in eu-central-1?",
        "What were the most significant AI agent announcements from AWS in 2025?",
    ]

    while True:
        _section("Example queries")
        for i, ex in enumerate(examples, 1):
            wrapped = textwrap.fill(ex, width=65, subsequent_indent="     ")
            print(f"  {BOLD}{i}{RESET}  {wrapped}")
        print(f"  {BOLD}c{RESET}  Custom query")
        print(f"  {BOLD}q{RESET}  Return to menu")
        choice = input(f"\n{BOLD}Choice: {RESET}").strip().lower()

        if choice == "q" or choice == "":
            break
        if choice == "c":
            prompt = input(f"{BOLD}Query: {RESET}").strip()
        else:
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(examples):
                    raise ValueError
                prompt = examples[idx]
            except ValueError:
                print(f"{RED}Invalid choice.{RESET}")
                continue

        print(f"\n{DIM}Researching…{RESET}\n")
        agent(prompt)
        print()


def run_multi_agent(model_id: str = DEFAULT_MODEL) -> None:
    _header("Multi-Agent — Supervisor + Specialists")
    print(
        textwrap.dedent(f"""
        {DIM}Model: {model_id}
        Architecture: supervisor agent with two specialist sub-agents
          • research_specialist  — live web research
          • data_analyst         — Python code execution
        Demonstrates: task decomposition and specialist delegation{RESET}
        """)
    )
    sub_tools = _make_sub_agent_tools(model_id)
    agent = Agent(
        model=_make_model(model_id),
        tools=sub_tools,
        system_prompt=_MULTIAGENT_SYSTEM,
        callback_handler=_printing_callback,
    )

    while True:
        _section("Choose a scenario or type your own")
        for i, s in enumerate(_MULTIAGENT_SCENARIOS, 1):
            print(f"  {BOLD}{i}{RESET}  {s['name']}")
        print(f"  {BOLD}c{RESET}  Custom task")
        print(f"  {BOLD}q{RESET}  Return to menu")
        choice = input(f"\n{BOLD}Choice: {RESET}").strip().lower()

        if choice == "q" or choice == "":
            break
        if choice == "c":
            prompt = input(f"{BOLD}Task: {RESET}").strip()
        else:
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(_MULTIAGENT_SCENARIOS):
                    raise ValueError
                prompt = _MULTIAGENT_SCENARIOS[idx]["prompt"]
            except ValueError:
                print(f"{RED}Invalid choice.{RESET}")
                continue

        print(f"\n{DIM}Orchestrating…{RESET}\n")
        agent(prompt)
        print()


def run_memory(model_id: str = DEFAULT_MODEL) -> None:
    _header("Memory — Persistent AgentCore Memory")
    if not MEMORY_ID:
        print(
            f"{RED}AGENTCORE_MEMORY_ID is not set.{RESET}\n\n"
            "Create a memory store and export the ID:\n"
            "  export AGENTCORE_MEMORY_ID=<memory-id>\n"
        )
        input("Press Enter to return to menu…")
        return

    print(
        textwrap.dedent(f"""
        {DIM}Model: {model_id}
        Memory ID: {MEMORY_ID}
        Tools: agent_core_memory (AgentCore Memory API)
        Demonstrates: storing and retrieving facts across sessions{RESET}
        """)
    )
    provider = AgentCoreMemoryToolProvider(
        memory_id=MEMORY_ID,
        actor_id="demo-user",
        session_id="demo-session",
        namespace="default",
        region=REGION,
        boto_session=boto3.Session(region_name=REGION),
    )
    agent = Agent(
        model=_make_model(model_id),
        tools=provider.tools,
        system_prompt=_MEMORY_SYSTEM,
        callback_handler=_printing_callback,
    )

    while True:
        _section("Choose a scenario or type your own")
        for i, s in enumerate(_MEMORY_SCENARIOS, 1):
            print(f"  {BOLD}{i}{RESET}  {s['name']}")
        print(f"  {BOLD}c{RESET}  Custom message")
        print(f"  {BOLD}q{RESET}  Return to menu")
        choice = input(f"\n{BOLD}Choice: {RESET}").strip().lower()

        if choice == "q" or choice == "":
            break
        if choice == "c":
            prompt = input(f"{BOLD}Message: {RESET}").strip()
        else:
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(_MEMORY_SCENARIOS):
                    raise ValueError
                prompt = _MEMORY_SCENARIOS[idx]["prompt"]
            except ValueError:
                print(f"{RED}Invalid choice.{RESET}")
                continue

        print(f"\n{DIM}Processing…{RESET}\n")
        agent(prompt)
        print()


# ── Main menu ──────────────────────────────────────────────────────────────────

MODES = [
    ("chat",        "Multi-turn conversation",                  run_chat),
    ("analyst",     "Python REPL data analysis",                run_analyst),
    ("researcher",  "Live web research via HTTP",               run_researcher),
    ("multi-agent", "Supervisor + specialist sub-agents",       run_multi_agent),
    ("memory",      "Persistent memory via AgentCore Memory",   run_memory),
]


def _main() -> None:
    _header("Amazon Bedrock AgentCore — Strands Demo")
    print(
        f"{DIM}Region: {REGION}  |  Default model: {DEFAULT_MODEL}\n"
        f"Memory configured: {'yes (' + MEMORY_ID[:12] + '…)' if MEMORY_ID else 'no (set AGENTCORE_MEMORY_ID)'}{RESET}\n"
    )

    # Model selection
    print(f"{BOLD}Select model:{RESET}")
    model_choices = [
        (MODEL_NOVA,   "Amazon Nova Pro  (eu cross-region)"),
        ("c",          "Custom model ID"),
    ]
    for i, (mid, name) in enumerate(model_choices, 1):
        print(f"  {BOLD}{i}{RESET}  {name}")
    mchoice = input(f"\n{BOLD}Model [1]: {RESET}").strip() or "1"
    if mchoice == "1":
        model_id = MODEL_NOVA
    elif mchoice == "2":
        model_id = input("Custom model ID: ").strip() or DEFAULT_MODEL
    else:
        model_id = DEFAULT_MODEL

    print(f"{DIM}Using model: {model_id}{RESET}\n")

    while True:
        _hr()
        print(f"{BOLD}Select a demo mode:{RESET}")
        for i, (name, desc, _) in enumerate(MODES, 1):
            mem_note = f"  {DIM}(requires AGENTCORE_MEMORY_ID){RESET}" if name == "memory" and not MEMORY_ID else ""
            print(f"  {BOLD}{i}{RESET}  {CYAN}{name:<14}{RESET}  {desc}{mem_note}")
        print(f"  {BOLD}q{RESET}  Quit")

        choice = input(f"\n{BOLD}Mode: {RESET}").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            print("Goodbye.")
            break

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(MODES):
                raise ValueError
            _, _, fn = MODES[idx]
        except ValueError:
            print(f"{RED}Invalid choice.{RESET}")
            continue

        try:
            fn(model_id)
        except KeyboardInterrupt:
            print(f"\n{DIM}Interrupted.{RESET}")
        except Exception as exc:
            print(f"\n{RED}Error: {exc}{RESET}")
            input("Press Enter to continue…")


if __name__ == "__main__":
    _main()
