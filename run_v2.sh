#!/usr/bin/env bash
# Full v2 benchmark — all 4 architectures x 3 model tiers, 70 payloads
# Estimated time: ~3 hours | Estimated cost: ~$15

set -euo pipefail

FRONTIER="claude-sonnet-4-5"
WEAK="claude-haiku-4-5"
BUDGET="claude-3-haiku-20240307"
JUDGE="claude-haiku-4-5"

AGENTS=("react" "pte" "dual_llm" "schema_dual_llm")
DELAY=1
PAUSE=30

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/v2_${TIMESTAMP}.log"

source "$SCRIPT_DIR/.venv/bin/activate"

export LLM_PROVIDER=anthropic
export JUDGE_MODEL="$JUDGE"

exec > >(tee -a "$LOG") 2>&1

echo "============================================"
echo " v2 benchmark"
echo " Models: $BUDGET / $WEAK / $FRONTIER"
echo " Judge:  $JUDGE"
echo " Agents: ${AGENTS[*]}"
echo " Payloads: 70 (56 original + 14 extended)"
echo " Log:    $LOG"
echo "============================================"
echo ""

run_model() {
    local model="$1"
    local label="$2"
    echo "══════════════════════════════════════════"
    echo " $label"
    echo "══════════════════════════════════════════"
    echo ""
    export MODEL_NAME="$model"
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
    echo "$label complete."
    echo ""
    sleep "$PAUSE"
}

run_model "$BUDGET"   "Haiku 3 (budget)"
run_model "$WEAK"     "Haiku 4.5"
run_model "$FRONTIER" "Sonnet (frontier)"

echo "============================================"
echo " All 12 runs complete"
echo "============================================"
echo ""
python3 -m src.main --runs
