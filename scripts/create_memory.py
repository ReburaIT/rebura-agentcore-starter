#!/usr/bin/env python3
"""Create an AgentCore Memory store and print the ID to use.

    python scripts/create_memory.py                 # prints the memory ID
    python scripts/create_memory.py --write-env     # also adds AGENTCORE_MEMORY_ID to .env

Needs permission to call bedrock-agentcore-control:CreateMemory. Memory stores are
billed by use (see the AgentCore pricing page), and this one holds no strategies, so
it only stores the records that the demo writes explicitly.
"""

import argparse
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--name", default="agentcore_workshop",
                    help="Memory name: letters, digits and underscores, starting with a letter")
parser.add_argument("--expiry-days", type=int, default=30, help="Days to keep events (7-365)")
parser.add_argument("--write-env", action="store_true", help="Append AGENTCORE_MEMORY_ID to .env")
args = parser.parse_args()

region = os.environ.get("AGENTCORE_REGION", "eu-central-1")
client = boto3.Session(region_name=region).client("bedrock-agentcore-control")

try:
    memory = client.create_memory(name=args.name, eventExpiryDuration=args.expiry_days)["memory"]
except ClientError as exc:
    code = exc.response["Error"]["Code"]
    print(f"Could not create the memory ({code}): {exc.response['Error']['Message']}")
    if code in ("ConflictException", "ValidationException"):
        print("If a memory with this name already exists, find its ID with:\n"
              f"  aws bedrock-agentcore-control list-memories --region {region}\n"
              "or re-run with --name <something-else>.")
    raise SystemExit(1)

memory_id = memory["id"]
print(f"Created {memory_id} in {region}; waiting for it to become ACTIVE...")

for _ in range(60):
    status = client.get_memory(memoryId=memory_id)["memory"]["status"]
    if status == "ACTIVE":
        break
    if status == "FAILED":
        raise SystemExit("Memory creation FAILED. Check the AWS console for the reason.")
    time.sleep(5)
else:
    raise SystemExit(f"Still {status} after 5 minutes. Re-run scripts/check_setup.py later.")

print(f"\nMemory is ACTIVE.\n\n  AGENTCORE_MEMORY_ID={memory_id}\n")

if args.write_env:
    env = Path(__file__).resolve().parent.parent / ".env"
    existing = env.read_text() if env.exists() else ""
    if "AGENTCORE_MEMORY_ID=" in existing:
        print(f"{env} already sets AGENTCORE_MEMORY_ID; edit it by hand to use the new ID.")
    else:
        with env.open("a") as f:
            f.write(("" if existing.endswith("\n") or not existing else "\n") + f"AGENTCORE_MEMORY_ID={memory_id}\n")
        print(f"Added it to {env}")
else:
    print("Add that line to your .env file (or re-run with --write-env).")
