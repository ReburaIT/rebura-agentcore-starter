#!/usr/bin/env python3
"""Check that your machine and AWS account are ready for the workshop.

    python scripts/check_setup.py

Each check prints OK / FAIL with a hint on how to fix it. Nothing is created or changed.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

REGION = os.environ.get("AGENTCORE_REGION", "eu-central-1")
MODEL = os.environ.get("CHECK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

failures = 0


def ok(msg: str) -> None:
    print(f"  \033[92mOK\033[0m    {msg}")


def fail(msg: str, hint: str) -> None:
    global failures
    failures += 1
    print(f"  \033[91mFAIL\033[0m  {msg}\n        -> {hint}")


def skip(msg: str) -> None:
    print(f"  \033[93mSKIP\033[0m  {msg}")


print(f"Region: {REGION}   Model: {MODEL}\n")

# 1. Python version
if sys.version_info >= (3, 10):
    ok(f"Python {sys.version.split()[0]}")
else:
    fail(f"Python {sys.version.split()[0]} is too old", "Install Python 3.10 or newer")

# 2. Packages
for module, package in [
    ("boto3", "boto3"),
    ("strands", "strands-agents"),
    ("strands_tools", "strands-agents-tools"),
    ("bedrock_agentcore", "bedrock-agentcore"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
]:
    try:
        __import__(module)
        ok(f"import {module}")
    except ImportError:
        fail(f"cannot import {module}", f"pip install -r requirements.txt  (needs {package})")

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    print("\nboto3 is missing, so the AWS checks cannot run.")
    sys.exit(1)

# 3. AWS credentials
session = boto3.Session(region_name=REGION)
try:
    ident = session.client("sts").get_caller_identity()
    ok(f"AWS credentials work (account {ident['Account']})")
except (BotoCoreError, ClientError) as exc:
    fail(f"no usable AWS credentials ({type(exc).__name__})",
         "Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, or run `aws configure` / `aws sso login`")
    print("\nFix the credentials first; the remaining checks need them.")
    sys.exit(1)

# 4. Bedrock model access
try:
    resp = session.client("bedrock-runtime").converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    ok(f"Bedrock model {MODEL} responded")
except ClientError as exc:
    code = exc.response["Error"]["Code"]
    hints = {
        "AccessDeniedException": "Your role cannot invoke this model. Ask the hosts to enable Bedrock model access.",
        "ValidationException": "The model ID is not valid in this region. Use an `eu.` inference profile ID, or change AGENTCORE_REGION.",
        "ResourceNotFoundException": "Model not found in this region. Check AGENTCORE_REGION.",
        "ThrottlingException": "Throttled. Wait a moment and re-run.",
    }
    fail(f"Bedrock call failed ({code})", hints.get(code, str(exc)))
except BotoCoreError as exc:
    fail(f"Bedrock call failed ({type(exc).__name__})", str(exc))

# 5. AgentCore Memory (optional)
if not MEMORY_ID:
    skip("AGENTCORE_MEMORY_ID not set (only needed for Memory mode; create one with scripts/create_memory.py)")
else:
    try:
        mem = session.client("bedrock-agentcore-control").get_memory(memoryId=MEMORY_ID)["memory"]
        if mem.get("status") == "ACTIVE":
            ok(f"AgentCore Memory {MEMORY_ID} is ACTIVE")
        else:
            fail(f"AgentCore Memory is {mem.get('status')}", "Wait for it to become ACTIVE and re-run")
    except (BotoCoreError, ClientError) as exc:
        fail("cannot read AgentCore Memory", f"Check AGENTCORE_MEMORY_ID and AGENTCORE_REGION ({exc})")

print()
if failures:
    print(f"\033[91m{failures} check(s) failed.\033[0m See the hints above, or the Troubleshooting section of the README.")
    sys.exit(1)
print("\033[92mAll checks passed. You are ready to go.\033[0m")
