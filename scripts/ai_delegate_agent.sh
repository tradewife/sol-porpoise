#!/bin/bash
# ai_delegate_agent.sh — Bridge for Droid AI trade decisions.
#
# Called by ai-paper mode after it writes:
#   - accounts/ai/data/ai_prompt.txt
#   - accounts/ai/data/ai_request.json
#
# This script invokes `droid exec` to read the prompt and produce a
# prompt-bound JSON response, then writes it to the response file.
#
# Environment (set by run_scan.py):
#   IMPERIAL_AI_PROMPT_PATH    — path to the market data prompt
#   IMPERIAL_AI_REQUEST_PATH   — path to the request metadata JSON
#   IMPERIAL_AI_RESPONSE_PATH  — path where the JSON response must be written
#   IMPERIAL_AI_PROMPT_ID      — the prompt_id that must be echoed in the response

set -euo pipefail

PROMPT_PATH="${IMPERIAL_AI_PROMPT_PATH:?IMPERIAL_AI_PROMPT_PATH not set}"
REQUEST_PATH="${IMPERIAL_AI_REQUEST_PATH:?IMPERIAL_AI_REQUEST_PATH not set}"
RESPONSE_PATH="${IMPERIAL_AI_RESPONSE_PATH:?IMPERIAL_AI_RESPONSE_PATH not set}"
PROMPT_ID="${IMPERIAL_AI_PROMPT_ID:?IMPERIAL_AI_PROMPT_ID not set}"

# Fail fast if the prompt file doesn't exist.
if [ ! -f "$PROMPT_PATH" ]; then
    echo "AI bridge: prompt file not found at $PROMPT_PATH" >&2
    exit 1
fi

# Build a short instruction that tells droid to read the prompt and output ONLY JSON.
BRIDGE_INSTRUCTION=$(cat <<'BRIDGE_EOF'
You are the Imperial AI trading delegate. Read the market data prompt file, analyze the live data, and produce trade decisions.

INSTRUCTIONS:
1. Read the file at the path shown below.
2. Follow all instructions in that prompt exactly.
3. Output ONLY the JSON response object — no markdown fences, no commentary, no explanation.
4. The JSON must include the correct prompt_id field.
5. If no high-quality setups exist, return the JSON with an empty trades array and a no_trade_reason.

Prompt file path will be provided separately. Your output will be captured as-is.
BRIDGE_EOF
)

echo "AI bridge: calling droid exec for prompt_id=$PROMPT_ID" >&2

# Call droid exec in read-only mode (default autonomy).
# It reads the prompt file and outputs the JSON response to stdout.
# We use --auto low so droid can write the response file itself if needed,
# but we capture stdout as the primary output path.
RESPONSE_JSON=$(droid exec \
    --model "custom:GLM-5.1-[Z.AI-Coding-Plan]---Openai-0" \
    --reasoning-effort high \
    --auto low \
    --cwd "$(dirname "$0")/.." \
    -f "$PROMPT_PATH" \
    --append-system-prompt "$BRIDGE_INSTRUCTION" \
    2>&1 || true)

# Validate we got something
if [ -z "$RESPONSE_JSON" ]; then
    echo "AI bridge: droid exec returned empty output" >&2
    exit 1
fi

# Strip any leading/trailing whitespace and optional markdown fences
RESPONSE_JSON=$(echo "$RESPONSE_JSON" | sed '/^```/d' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

# Quick sanity check: must look like JSON
if ! echo "$RESPONSE_JSON" | python3 -c "import sys,json; json.loads(sys.stdin.read())" 2>/dev/null; then
    echo "AI bridge: response is not valid JSON, saving raw output for inspection" >&2
    echo "$RESPONSE_JSON" > "${RESPONSE_PATH}.raw"
    exit 1
fi

# Write the validated response
echo "$RESPONSE_JSON" > "$RESPONSE_PATH"
echo "AI bridge: response written to $RESPONSE_PATH ($(echo "$RESPONSE_JSON" | wc -c) bytes)" >&2

exit 0

