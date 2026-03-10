#!/usr/bin/env bash
# =============================================================================
# Full Evaluation: 3 architectures x 3 Claude models = 9 runs
#
# Models:
#   Frontier: claude-sonnet-4-5      (~$3/$15 per M tokens)
#   Weaker:   claude-haiku-4-5       (~$1/$5 per M tokens)
#   Budget:   claude-3-haiku-20240307 (~$0.25/$1.25 per M tokens, deprecated Apr 2026)
#   Judge:    claude-haiku-4-5       (consistent, cheap)
#
# Estimated cost: ~$28 total for all 9 runs (56 payloads each)
# Estimated time: ~2-3 hours total
#
# Usage:
#   ./run_eval.sh              # All 56 payloads x 9 combos
#   ./run_eval.sh refund       # Refund only (12 payloads, ~20 min)
# =============================================================================

set -euo pipefail

FRONTIER="claude-sonnet-4-5"
WEAK="claude-haiku-4-5"
BUDGET="claude-3-haiku-20240307"
JUDGE="claude-haiku-4-5"
AGENTS=("react" "pte" "dual_llm")
DELAY=5            # 5s between payloads — Anthropic rate limits are generous
PAUSE=60           # 1 min between runs

OP_FLAG=""
OP_LABEL="all"
if [ "${1:-}" != "" ]; then
    OP_FLAG="--op=$1"
    OP_LABEL="$1"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="$LOG_DIR/eval_${TIMESTAMP}_${OP_LABEL}.log"

source "$SCRIPT_DIR/.venv/bin/activate"

export LLM_PROVIDER=anthropic
export JUDGE_MODEL="$JUDGE"

# All output goes to both terminal and master log
exec > >(tee -a "$MASTER_LOG") 2>&1

echo "============================================"
echo " Frontier: $FRONTIER"
echo " Weaker:   $WEAK"
echo " Budget:   $BUDGET"
echo " Judge:    $JUDGE"
echo " Agents:   ${AGENTS[*]}"
echo " Op:       $OP_LABEL"
echo " Delay:    ${DELAY}s between payloads"
echo " Log:      $MASTER_LOG"
echo "============================================"
echo ""

# ── Phase 1: Budget model (Haiku 3) ──────────────────────────────────────────
echo "══════════════════════════════════════════"
echo " PHASE 1/3: $BUDGET (3 runs)"
echo "══════════════════════════════════════════"
echo ""

export MODEL_NAME="$BUDGET"

for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    echo "[Phase 1 - $((i+1))/3] Running $agent with $BUDGET..."
    echo "    Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    python3 -m src.main --eval --agent="$agent" --delay="$DELAY" \
        $OP_FLAG --failures -v

    echo ""
    echo "    Finished: $(date '+%Y-%m-%d %H:%M:%S')"

    if [ "$i" -lt 2 ]; then
        echo "    Pausing ${PAUSE}s..."
        sleep "$PAUSE"
    fi
done

echo ""
echo "Phase 1 complete."
echo ""
sleep "$PAUSE"

# ── Phase 2: Weaker model (Haiku 4.5) ────────────────────────────────────────
echo "══════════════════════════════════════════"
echo " PHASE 2/3: $WEAK (3 runs)"
echo "══════════════════════════════════════════"
echo ""

export MODEL_NAME="$WEAK"

for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    echo "[Phase 2 - $((i+1))/3] Running $agent with $WEAK..."
    echo "    Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    python3 -m src.main --eval --agent="$agent" --delay="$DELAY" \
        $OP_FLAG --failures -v

    echo ""
    echo "    Finished: $(date '+%Y-%m-%d %H:%M:%S')"

    if [ "$i" -lt 2 ]; then
        echo "    Pausing ${PAUSE}s..."
        sleep "$PAUSE"
    fi
done

echo ""
echo "Phase 2 complete."
echo ""
sleep "$PAUSE"

# ── Phase 3: Frontier model (Sonnet) ─────────────────────────────────────────
echo "══════════════════════════════════════════"
echo " PHASE 3/3: $FRONTIER (3 runs)"
echo "══════════════════════════════════════════"
echo ""

export MODEL_NAME="$FRONTIER"

for i in "${!AGENTS[@]}"; do
    agent="${AGENTS[$i]}"
    echo "[Phase 3 - $((i+1))/3] Running $agent with $FRONTIER..."
    echo "    Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    python3 -m src.main --eval --agent="$agent" --delay="$DELAY" \
        $OP_FLAG --failures -v

    echo ""
    echo "    Finished: $(date '+%Y-%m-%d %H:%M:%S')"

    if [ "$i" -lt 2 ]; then
        echo "    Pausing ${PAUSE}s..."
        sleep "$PAUSE"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo " ALL 9 RUNS COMPLETE"
echo "============================================"
echo ""
python3 -m src.main --runs
