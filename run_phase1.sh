#!/usr/bin/env bash
# Phase 1: Bug fixes + spotlighting — Haiku 4.5 only, all 3 architectures
# ~30 minutes total

set -euo pipefail

export LLM_PROVIDER=anthropic
export MODEL_NAME=claude-haiku-4-5
export JUDGE_MODEL=claude-haiku-4-5

AGENTS=("dual_llm" "react" "pte" "schema_dual_llm")
DELAY=1
PAUSE=30

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/phase1_${TIMESTAMP}.log"

source "$SCRIPT_DIR/.venv/bin/activate"

exec > >(tee -a "$LOG") 2>&1

echo "============================================"
echo " Phase 1: Bug fixes + spotlighting"
echo " Model:  $MODEL_NAME"
echo " Judge:  $JUDGE_MODEL"
echo " Agents: ${AGENTS[*]}"
echo " Log:    $LOG"
echo "============================================"
echo ""

for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    echo "[$((i+1))/${#AGENTS[@]}] Running $agent..."
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
echo " Phase 1 complete"
echo "============================================"
echo ""
python3 -m src.main --runs
