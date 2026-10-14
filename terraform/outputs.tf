output "memory_id" {
  description = "Put this in .env as AGENTCORE_MEMORY_ID"
  value       = aws_bedrockagentcore_memory.demo.id
}

output "env_line" {
  description = "Line to add to .env"
  value       = "AGENTCORE_MEMORY_ID=${aws_bedrockagentcore_memory.demo.id}"
}

output "demo_policy_arn" {
  description = "Attach this policy to the IAM user or role that runs the demo"
  value       = aws_iam_policy.demo_caller.arn
}

output "aws_region" {
  description = "Region the resources were created in"
  value       = var.aws_region
}
