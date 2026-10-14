variable "aws_region" {
  description = "AWS region for the AgentCore Memory store and IAM policy. Must match AGENTCORE_REGION in .env."
  type        = string
  default     = "eu-central-1"
}

variable "name_prefix" {
  description = "Prefix for resource names. Letters, digits and underscores only, starting with a letter (AgentCore Memory names cannot contain hyphens)."
  type        = string
  default     = "agentcore_workshop"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,39}$", var.name_prefix))
    error_message = "name_prefix must start with a letter and contain only letters, digits and underscores (max 40 characters)."
  }
}

variable "memory_event_expiry_days" {
  description = "How many days AgentCore Memory keeps events (7-365)."
  type        = number
  default     = 30

  validation {
    condition     = var.memory_event_expiry_days >= 7 && var.memory_event_expiry_days <= 365
    error_message = "memory_event_expiry_days must be between 7 and 365."
  }
}
