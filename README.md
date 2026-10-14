# AgentCore + Strands Starter

A working example and starting point for building agents with the [Strands Agents SDK](https://github.com/strands-agents/sdk-python) on [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/).

It contains two things:

| | What it is | Use it to |
|---|---|---|
| **The demo** (`server.py`, `demo.py`) | A browser UI and a CLI with five working agents: chat, data analyst, web researcher, multi-agent, and memory | See what's possible and check your setup works |
| **The starter agent** (`starter/agent.py`) | About 100 lines: one custom tool, optional AgentCore Memory, optional MCP tools, and an AgentCore Runtime entrypoint | Copy it and build your own agent |

Work through the steps below in order. Each step says how to tell it worked.

---

## Before you start

You need:

- **Python 3.10 or newer** (`python3 --version`)
- **An AWS account with Bedrock model access.** If you're using this in a workshop or hackathon, the organisers may give you a sandbox account, so follow their instructions for credentials, region and permissions. If you're using your own AWS account, **you pay for everything you use** (see [Cost](#cost)).
- **AWS credentials** for that account: an access key, secret key and session token, or an SSO profile.
- **Node.js 20 or newer**, only for step 8 (deploying with the AgentCore CLI).

---

## Step by step

### 1. Get the code

```bash
git clone https://github.com/ReburaIT/rebura-agentcore-starter.git
cd rebura-agentcore-starter
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

You should see `Successfully installed ...` with no errors. Run `source .venv/bin/activate` again whenever you open a new terminal.

### 3. Add your AWS credentials

Pick **one** of these:

**A. Environment variables** (what a workshop sandbox account usually gives you):
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
```

**B. SSO or a named profile:**
```bash
aws sso login --profile <profile>
export AWS_PROFILE=<profile>
```

> The web UI also has a **Credentials** button in the sidebar. You can paste fresh credentials there when they expire, without restarting.

### 4. Create your settings file

```bash
cp .env.example .env
```

Open `.env`. The defaults work for most people. Change `AGENTCORE_REGION` if your workshop organisers tell you to use another region, or to match the region you want to use in your own account.

### 5. Check everything works

```bash
python scripts/check_setup.py
```

You should see `OK` against every line and `All checks passed`. If a line says `FAIL`, the hint underneath says how to fix it. See Troubleshooting below.

### 6. Run the demo

**Web UI:**
```bash
python server.py
```
Open <http://localhost:8000>. Pick a mode in the sidebar, choose an example prompt and press send.

**Or the CLI:**
```bash
python demo.py
```

Try the modes in this order:

| Mode | What to try | What you learn |
|---|---|---|
| **Chat** | Ask a question, then a follow-up that refers to it | An agent keeps conversation history |
| **Analyst** | Run one of the bundled datasets | An agent that writes and runs Python (`python_repl`) instead of guessing numbers |
| **Researcher** | Ask about current AWS pricing | An agent that uses a tool (`http_request`) and cites sources |
| **Multi-agent** | Run "Research + Analyse" | A supervisor agent delegating to specialist agents that are exposed as tools |
| **Memory** | Needs step 7 | Facts persist across sessions in AgentCore Memory |

Use the **{ } Context** button (top right) to see the system prompt, the tools and the raw events behind each response. It's the quickest way to understand what the agent is doing.

> **Security note:** the demo can run Python code and make web requests on your behalf, and the server only listens on `localhost` for that reason. Don't expose it on a shared network.

### 7. Add AgentCore Memory (optional, needed for Memory mode)

```bash
python scripts/create_memory.py --write-env
```

This creates a memory store, waits for it to become `ACTIVE`, and adds `AGENTCORE_MEMORY_ID=...` to your `.env`. Restart `python server.py`, open Memory mode and:

1. Click **Store preferences**, then start a **New Session** and click **Recall preferences**. The agent remembers across sessions.
2. Use `POST /api/seed-memory` to pre-load some example facts before a demo.

Your role needs permission for `bedrock-agentcore-control:CreateMemory`. In a workshop sandbox, if that's denied, ask the organisers for a memory ID.

**Prefer Terraform?** `terraform/` creates the same memory store plus an IAM policy with the permissions the demo needs (model invocation and this one memory store):

```bash
cd terraform
terraform init
terraform apply          # review the plan, then type yes
terraform output env_line  # add this line to ../.env
```

Use this or the script, not both. Run `terraform destroy` to remove everything afterwards. If your role can't create IAM policies (common in workshop sandboxes), remove the `aws_iam_policy` block from `terraform/main.tf` and keep the memory store.

### 8. Build your own agent

The starter agent is the part to copy:

```bash
python starter/agent.py "What is the refund policy?"
```

Open `starter/agent.py` and change these, in order:

1. **`SYSTEM_PROMPT`**: say what your agent is for.
2. **`lookup_policy`**: replace this stub tool with your own. A tool is a Python function with a docstring; the model reads the docstring to decide when to call it. Make it call a real API, database or file.
3. **Memory**: set `AGENTCORE_MEMORY_ID` in `.env` and the agent gets an AgentCore Memory tool automatically.
4. **External tools via MCP**: set `MCP_SERVER_URL` (for example your AgentCore Gateway's MCP URL) and, if it needs one, `MCP_BEARER_TOKEN`. The agent loads all of that server's tools.
5. **Model**: set `MODEL_ID` in `.env`. Use an `eu.` inference profile ID, such as `eu.amazon.nova-pro-v1:0` or `eu.anthropic.claude-sonnet-4-6`.

Test it as a local AgentCore Runtime server, exactly as it'll run when deployed:

```bash
python starter/agent.py --serve
```

In a second terminal:
```bash
curl -s localhost:8080/invocations -H 'content-type: application/json' \
  -d '{"prompt": "What is the refund policy?", "session_id": "demo-session-0001", "user_id": "me"}'
```
You get back `{"result": "..."}`.

### 9. Deploy to AgentCore Runtime

The hackathon requirement is to deploy on AgentCore Runtime. The AgentCore CLI does this:

```bash
npm install -g @aws/agentcore
agentcore create          # choose a Strands project when asked
cd <the-project-it-created>
agentcore dev             # run it locally with a browser inspector
agentcore deploy          # deploy to AgentCore Runtime
agentcore invoke --prompt "Hello"
```

Then bring your agent over: copy your tools and system prompt from `starter/agent.py` into the generated project and run `agentcore deploy` again.

- **The first `agentcore deploy` takes a few minutes**, because it bootstraps your account with CDK.
- Your role needs permission to deploy (CloudFormation, IAM and AgentCore). Workshop sandbox accounts are usually set up for this; in your own account, check your permissions first.
- If a step fails, the CLI's error message is the best guide; the [AgentCore CLI quickstart](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-get-started-cli.html) covers each command.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ValidationException` ... "on-demand throughput isn't supported" | You used a bare model ID. Use an inference profile ID with the `eu.` prefix (see the table below). |
| `AccessDeniedException` when invoking a model | Bedrock model access isn't enabled for that model in your account or region. In a workshop, ask the organisers; in your own account, enable it in the Bedrock console. |
| `ExpiredTokenException` / `security token ... expired` | Your temporary credentials expired. Paste new ones in the UI's Credentials panel, or export them again and restart. |
| `Could not connect to the endpoint URL` | `AGENTCORE_REGION` is wrong or the service isn't available there. Check `.env`. |
| `Address already in use` on port 8000 | Something else is using it. Run `PORT=8010 python server.py`. |
| Analyst mode says "Application is already running" | The server sets `BYPASS_TOOL_CONSENT` itself now. If you see this in the CLI, run `export BYPASS_TOOL_CONSENT=true`. |
| Memory mode says `AGENTCORE_MEMORY_ID is not set` | Do step 7, then restart the server. |
| `ModuleNotFoundError` | Your virtual environment isn't active. Run `source .venv/bin/activate`, then `pip install -r requirements.txt`. |
| `agentcore: command not found` | Run `npm install -g @aws/agentcore` (needs Node.js 20+). |

Run `python scripts/check_setup.py` any time to re-check your setup.

---

## Model IDs

Direct model IDs often fail in the EU regions. Use the cross-region `eu.` inference profile ID:

| Model | ID |
|---|---|
| Amazon Nova Pro | `eu.amazon.nova-pro-v1:0` |
| Claude Sonnet 4.6 | `eu.anthropic.claude-sonnet-4-6` |

The web UI also lists every inference profile available to your account in its model dropdown.

---

## How the demo works

```
Browser ──► server.py (FastAPI + Strands agents, streams over SSE)     demo.py (same agents, terminal)
                │
                ├─ BedrockModel          (Amazon Bedrock, eu cross-region inference profile)
                ├─ python_repl           (analyst mode, runs locally on your machine)
                ├─ http_request          (researcher mode)
                ├─ sub-agents as @tools  (multi-agent mode)
                └─ agentcore_memory tool (memory mode ──► AgentCore Memory via boto3)
```

The server keeps one agent per session, mode and model so conversation history carries across turns, and swaps a fresh streaming callback in on each request.

Memory mode in the web UI uses the `agentcore_memory` tool in `server.py`, which writes records directly with `batch_create_memory_records`. The CLI (`demo.py`) uses `AgentCoreMemoryToolProvider` from `strands_tools`. The two use different namespaces, so memories saved in one aren't visible in the other.

## Files

| File | Purpose |
|---|---|
| `starter/agent.py` | **Start here.** Minimal agent you can copy: custom tool, optional memory and MCP, Runtime entrypoint |
| `scripts/check_setup.py` | Checks Python, packages, AWS credentials, model access and memory |
| `scripts/create_memory.py` | Creates an AgentCore Memory store and prints its ID |
| `terraform/` | Same memory store plus the IAM policy the demo needs, as Terraform |
| `server.py` | Demo web backend: Strands agents, SSE streaming, credential swap |
| `index.html` | Demo web UI |
| `demo.py` | Demo CLI |
| `cheatsheet.html` | Cheat sheet covering the AgentCore services and the Strands SDK |
| `requirements.txt` | All Python dependencies |
| `.env.example` | Settings template |
| `LICENSE` | MIT licence |

## Cost

In a workshop that provides a sandbox account, the organisers normally cover the cost, but check with them. **In your own AWS account, you pay for everything this repo uses**: Bedrock model use (per token) and AgentCore usage (Memory, Runtime and so on). See the [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) pages.

## Licence

[MIT](LICENSE). Provided as-is for learning and as a starting point. It isn't production-ready and the demo server is intended to run on localhost only.
