# Stripe Payment Agent - Security Evaluation

FYP Research: Evaluating architectural patterns against prompt injection attacks in LLM-based payment agents.

## Quick Start

### 1. Install dependencies

```bash
cd stripe-payment-agent
pip install -e ".[dev]"
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Important**: Use Stripe TEST keys only (`sk_test_...`)

### 3. Run the agent

```bash
# Interactive mode
python -m src.main

# Single query mode
python -m src.main "List all customers"
```

## Project Structure

```
stripe-payment-agent/
├── src/
│   ├── config.py         # Configuration
│   ├── tools.py          # Stripe tool definitions
│   ├── prompts.py        # System prompts
│   ├── react_agent.py    # ReAct agent (baseline)
│   └── main.py           # CLI entry point
│
├── tests/
│   └── test_agent.py     # Test suite
│
├── .env.example          # API key template
└── pyproject.toml        # Dependencies
```

## Architectures

| Architecture | Status | File |
|--------------|--------|------|
| ReAct (baseline) | ✅ Implemented | `react_agent.py` |
| Plan-Then-Execute | 🔜 TODO | `pte_agent.py` |
| Dual LLM | 🔜 TODO | `dual_llm_agent.py` |
| Orchestrator | 🔜 TODO | `orchestrator_agent.py` |

## Example Commands

```
List all customers
Show me recent payments
Show payments for customer cus_xxx
Refund $50 from payment pi_xxx
Create an invoice for customer cus_xxx
List active subscriptions
Cancel subscription sub_xxx
```

## Running Tests

```bash
pytest tests/ -v
```

## Security Research Notes

This agent is intentionally vulnerable to prompt injection for research purposes.
The ReAct baseline has NO defenses against:
- Indirect prompt injection via API responses
- Goal hijacking through poisoned metadata
- Multi-turn conversation attacks

**DO NOT use in production. Sandbox only.**
# AgenticComparison-
