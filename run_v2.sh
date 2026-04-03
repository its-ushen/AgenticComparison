#!/usr/bin/env bash
# Full v2 benchmark — all 4 architectures, Haiku 4.5, 70 payloads
# Estimated time: ~50 minutes | Estimated cost: ~$2.50

set -euo pipefail

export LLM_PROVIDER=anthropic
export MODEL_NAME=claude-haiku-4-5
export JUDGE_MODEL=claude-haiku-4-5

AGENTS=("react" "pte" "dual_llm" "schema_dual_llm")
DELAY=1
PAUSE=30

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/v2_${TIMESTAMP}.log"

source "$SCRIPT_DIR/.venv/bin/activate"

exec > >(tee -a "$LOG") 2>&1

echo "============================================"
echo " v2 benchmark"
echo " Model:  $MODEL_NAME"
echo " Judge:  $JUDGE_MODEL"
echo " Agents: ${AGENTS[*]}"
echo " Payloads: 70 (56 original + 14 extended)"
echo " Log:    $LOG"
echo "============================================"
echo ""

for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    echo "[$((i+1))/${#AGENTS[@]}] $agent"
    echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    python3 -m src.main --eval --agent="$agent" --delay="$DELAY" --failures -v

    echo ""
    echo "  Finished: $(date '+%Y-%m-%d %H:%M:%S')"

    if [ "$i" -lt $((${#AGENTS[@]} - 1)) ]; then
        echo "  Pausing ${PAUSE}s..."
        sleep "$PAUSE"
    fi
done

echo ""
echo "============================================"
echo " Done"
echo "============================================"
echo ""
python3 -m src.main --runs
