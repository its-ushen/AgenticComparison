# Stripe Payment Agent — Security Evaluation

FYP research comparing four LLM agent architectures against prompt injection attacks in a Stripe payment context.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
```

## Running the Benchmark

**Tier A** — 3 architectures × 3 Claude models = 9 combinations, 56 payloads:

```bash
./run_eval.sh
```

**v2 extended tiers** — 4 architectures × 3 Claude models = 12 combinations, 14 extended payloads:

```bash
./run_v2.sh
```

Logs are saved to `logs/` and results to `data/evals.db`.

### Running a single agent manually

```bash
python -m src.main --eval --agent=react
python -m src.main --eval --agent=pte
python -m src.main --eval --agent=dual_llm

# Filter by operation
python -m src.main --eval --agent=react --op=refund

# Show failure details
python -m src.main --eval --agent=react --failures -v
```

### Viewing past results

```bash
python -m src.main --runs
python -m src.main --runs --run=ID
```

## Metrics

- **TCR (Task Completion Rate)** — did the agent complete the legitimate user request? (higher is better)
- **ASR (Attack Success Rate)** — was the agent manipulated by the injection? (lower is better)

## Architectures

| Architecture | File |
|---|---|
| ReAct (baseline) | `src/react_agent.py` |
| Plan-Then-Execute | `src/pte_agent.py` |
| Dual LLM | `src/dual_llm_agent.py` |
| Schema Dual LLM | `src/schema_dual_llm_agent.py` |

## Benchmark

**Tier A** — 56 payloads across four Stripe operations:

| Operation | Payloads | Attack Vectors |
|---|---|---|
| Refund | 12 | Destination redirect, amount manipulation, authorisation bypass |
| Subscription | 11 | Hidden charges, parameter tampering, forced cancellation |
| Invoice | 11 | Customer misdirection, item injection, data exfiltration |
| Payment link | 12 | Amount manipulation, deceptive content, recipient hijacking |

**Extended tiers (v2)** — 14 payloads targeting higher-capability architectures (Tiers B–E).

All payloads are in `attacks/payloads.json`.

## Project Structure

```
├── src/
│   ├── react_agent.py           # ReAct baseline
│   ├── pte_agent.py             # Plan-Then-Execute
│   ├── dual_llm_agent.py        # Dual LLM
│   ├── schema_dual_llm_agent.py # Schema Dual LLM
│   ├── eval.py                  # LLM judge (TCR/ASR)
│   ├── tools.py                 # Mock Stripe MCP tools
│   ├── db.py                    # SQLite results store
│   ├── models.py                # Data models
│   ├── config.py                # Config
│   └── main.py                  # CLI
├── attacks/
│   └── payloads.json            # 70 injection payloads (Tier A + extended)
├── data/
│   └── evals.db                 # Evaluation results
├── logs/                        # Run logs
├── tests/
├── run_eval.sh                  # Tier A benchmark runner
└── run_v2.sh                    # v2 extended tiers runner
```

## Models Tested

| Model | Tier |
|---|---|
| claude-sonnet-4-5 | Frontier |
| claude-haiku-4-5 | Mid |
| claude-3-haiku-20240307 | Budget |

Judge model: `claude-haiku-4-5` (consistent across all runs)
