# Resources for the AgentCore + Strands demo.
#
#   - an AgentCore Memory store          (the same thing scripts/create_memory.py creates)
#   - an IAM policy with the permissions the demo needs, to attach to the user or role that runs it
#
# Use this OR scripts/create_memory.py, not both. If your sandbox role cannot create IAM policies,
# comment out the IAM section at the bottom and use the memory store alone.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# ── AgentCore Memory ────────────────────────────────────────────────────────

resource "aws_bedrockagentcore_memory" "demo" {
  name                  = var.name_prefix
  description           = "Memory store for the AgentCore + Strands workshop demo"
  event_expiry_duration = var.memory_event_expiry_days
}

# ── Permissions for whoever runs the demo ───────────────────────────────────

data "aws_iam_policy_document" "demo_caller" {
  statement {
    sid    = "InvokeBedrockModels"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }

  # Fills the model dropdown in the web UI.
  statement {
    sid       = "ListInferenceProfiles"
    effect    = "Allow"
    actions   = ["bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"]
    resources = ["*"]
  }

  statement {
    sid    = "UseDemoMemory"
    effect = "Allow"
    # Covers batch-create, retrieve, list, get and delete of records. Scoped to this one memory store.
    actions   = ["bedrock-agentcore:*MemoryRecord*"]
    resources = [aws_bedrockagentcore_memory.demo.arn]
  }

  # Lets scripts/check_setup.py read the memory's status.
  statement {
    sid       = "ReadDemoMemory"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:GetMemory"]
    resources = [aws_bedrockagentcore_memory.demo.arn]
  }
}

resource "aws_iam_policy" "demo_caller" {
  name        = "${var.name_prefix}-demo-policy"
  description = "Permissions to run the AgentCore + Strands demo (Bedrock models and one AgentCore Memory store)"
  policy      = data.aws_iam_policy_document.demo_caller.json
}
