#!/bin/bash
# ai_delegate_agent.sh — Hook for Droid/Hermes AI trade decisions.
#
# This script is called by ai-paper mode after it writes:
#   - accounts/ai/data/ai_prompt.txt
#   - accounts/ai/data/ai_request.json
#
# It should write:
#   - accounts/ai/data/ai_response.json
#
# Expected environment:
#   IMPERIAL_AI_PROMPT_PATH
#   IMPERIAL_AI_REQUEST_PATH
#   IMPERIAL_AI_RESPONSE_PATH
#   IMPERIAL_AI_PROMPT_ID
#
# The default implementation is intentionally safe: it does not call any
# external API and does not create trades. Wire this script to Hermes or Droid
# when their invocation/API contract is available.

set -euo pipefail

echo "AI delegate bridge not configured; prompt ready at ${IMPERIAL_AI_PROMPT_PATH:-unknown}" >&2
echo "Expected response path: ${IMPERIAL_AI_RESPONSE_PATH:-unknown}" >&2
echo "Required prompt_id: ${IMPERIAL_AI_PROMPT_ID:-unknown}" >&2

exit 0

