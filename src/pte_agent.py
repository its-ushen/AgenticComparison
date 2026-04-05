"""Plan-then-Execute agent — planner, pure-Python executor, synthesizer."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.config import config
from src.tools import execute_tool, get_mock_tools, set_mock_tools, reset_mock_tools, MockStripeTools, MockDataStore
from src.prompts import PTE_PLANNER_PROMPT, PTE_SYNTHESIZER_PROMPT
from src.base import AgentResult

console = Console()


@dataclass
class PlanStep:
    id: int
    tool: str
    args: dict
    store_as: str
    depends_on: list[int] = field(default_factory=list)
    description: str = ""


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]


def _resolve_path(expr: str, store: dict) -> Any:
    """
    Resolve a $variable[N].field path expression against the store.

    Examples:
        "$customer[0].id"     → store["$customer"][0]["id"]
        "$payments[0].amount" → store["$payments"][0]["amount"]
        "$result"             → store["$result"]
    """
    tokens = re.findall(r'\$\w+|\[\d+\]|\.\w+', expr)
    if not tokens:
        return expr

    current = None
    for token in tokens:
        if token.startswith("$"):
            if token not in store:
                raise KeyError(
                    f"Variable {token} not in store. "
                    f"Available: {list(store.keys())}"
                )
            current = store[token]
        elif token.startswith("["):
            idx = int(token[1:-1])
            current = current[idx]
        elif token.startswith("."):
            field_name = token[1:]
            if not isinstance(current, dict):
                raise ValueError(
                    f"Cannot access .{field_name} on {type(current).__name__}"
                )
            current = current[field_name]

    return current


def _resolve_args(args: dict, store: dict) -> dict:
    resolved = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("$"):
            resolved[key] = _resolve_path(value, store)
        else:
            resolved[key] = value
    return resolved


def _parse_plan(raw: str) -> Plan:
    text = raw.strip()

    # Strip ```json ... ``` if the model wraps output
    if text.startswith("```"):
        inner = []
        in_block = False
        for line in text.split("\n"):
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner)

    data = json.loads(text)

    steps = [
        PlanStep(
            id=s["id"],
            tool=s["tool"],
            args=s.get("args", {}),
            store_as=s.get("store_as", f"$step{s['id']}"),
            depends_on=s.get("depends_on", []),
            description=s.get("description", ""),
        )
        for s in data["steps"]
    ]

    return Plan(goal=data.get("goal", ""), steps=steps)


class PlanThenExecuteAgent:

    def __init__(self, verbose: bool = True, mock_tools: MockStripeTools | None = None):
        self.provider = config.llm_provider
        self.model = config.model
        self.verbose = verbose
        self.tool_call_history: list[dict[str, Any]] = []
        self._custom_mock_tools = mock_tools is not None
        self._input_tokens: int = 0
        self._output_tokens: int = 0

        if self.provider == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        else:
            import openai
            self.client = openai.OpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
            )

        if mock_tools is not None:
            set_mock_tools(mock_tools)
        else:
            reset_mock_tools()

    def reset(self):
        self.tool_call_history = []
        self._input_tokens = 0
        self._output_tokens = 0
        if not self._custom_mock_tools:
            reset_mock_tools()

    def get_mock_call_log(self) -> list[dict]:
        return get_mock_tools().get_call_log()

    def _log(self, title: str, content: str, style: str = "blue"):
        if self.verbose:
            console.print(Panel(content, title=title, border_style=style))

    def _call_planner(self, user_input: str) -> str:
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=0,
                system=PTE_PLANNER_PROMPT,
                messages=[{"role": "user", "content": user_input}],
            )
            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                temperature=0,
                messages=[
                    {"role": "system", "content": PTE_PLANNER_PROMPT},
                    {"role": "user", "content": user_input},
                ],
            )
            self._input_tokens += response.usage.prompt_tokens
            self._output_tokens += response.usage.completion_tokens
            return response.choices[0].message.content

    def _execute_plan(self, plan: Plan) -> list[dict]:
        store: dict[str, Any] = {}
        execution_log = []

        for step in plan.steps:
            self._log(
                f"📋 Step {step.id}: {step.description or step.tool}",
                f"tool: {step.tool}\nargs: {json.dumps(step.args, indent=2)}",
                style="cyan",
            )

            # Substitute $variable references from previous step results
            try:
                resolved_args = _resolve_args(step.args, store)
            except (KeyError, ValueError, IndexError) as e:
                error_msg = f"Variable resolution failed at step {step.id}: {e}"
                self._log(f"❌ Step {step.id} Error", error_msg, style="red")
                execution_log.append({
                    "step": step.id,
                    "tool": step.tool,
                    "args": step.args,
                    "error": error_msg,
                })
                # Abort remaining steps — plan is invalid without this result
                break

            self._log(
                f"🔧 Tool Call: {step.tool}",
                json.dumps(resolved_args, indent=2),
                style="yellow",
            )

            result = execute_tool(step.tool, resolved_args)

            # Store for subsequent steps
            store[step.store_as] = result

            # Record for eval framework (same shape as ReAct's tool_call_history)
            self.tool_call_history.append({
                "tool": step.tool,
                "input": resolved_args,
                "output": result,
            })

            execution_log.append({
                "step": step.id,
                "tool": step.tool,
                "args": resolved_args,
                "result": result,
            })

            self._log(
                f"✅ Result: {step.tool}",
                json.dumps(result, indent=2),
                style="green",
            )

        return execution_log

    def _call_synthesizer(self, user_input: str, execution_log: list[dict]) -> str:
        log_text = json.dumps(execution_log, indent=2)
        user_content = f"User request: {user_input}\n\nExecution log:\n{log_text}"

        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                system=PTE_SYNTHESIZER_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                messages=[
                    {"role": "system", "content": PTE_SYNTHESIZER_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            self._input_tokens += response.usage.prompt_tokens
            self._output_tokens += response.usage.completion_tokens
            return response.choices[0].message.content

    def run(self, user_input: str) -> AgentResult:
        self.reset()
        self._log("👤 User Input", user_input, style="cyan")
        t_total = time.time()

        # --- Phase 1: Plan ---
        t0 = time.time()
        try:
            raw_plan = self._call_planner(user_input)
        except Exception as e:
            error_msg = f"Planner failed: {type(e).__name__}: {e}"
            elapsed = (time.time() - t_total) * 1000
            return AgentResult(
                success=False, final_response="", turns=1,
                tool_calls=[], error=error_msg,
                latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
            )
        planner_ms = (time.time() - t0) * 1000

        self._log("🗺️  Raw Plan", raw_plan, style="magenta")

        try:
            plan = _parse_plan(raw_plan)
        except (json.JSONDecodeError, KeyError) as e:
            error_msg = f"Plan parse failed: {e} | Raw: {raw_plan[:300]}"
            elapsed = (time.time() - t_total) * 1000
            return AgentResult(
                success=False, final_response="", turns=1,
                tool_calls=[], error=error_msg,
                latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
            )

        self._log(
            f"✅ Plan ({len(plan.steps)} steps)",
            f"Goal: {plan.goal}\n" + "\n".join(
                f"  {s.id}. [{s.tool}] {s.description}" for s in plan.steps
            ),
            style="blue",
        )

        # --- Phase 2: Execute (pure Python — no LLM) ---
        t0 = time.time()
        execution_log = self._execute_plan(plan)
        execution_ms = (time.time() - t0) * 1000

        # --- Phase 3: Synthesize ---
        t0 = time.time()
        try:
            final_response = self._call_synthesizer(user_input, execution_log)
        except Exception as e:
            error_msg = f"Synthesizer failed: {type(e).__name__}: {e}"
            elapsed = (time.time() - t_total) * 1000
            return AgentResult(
                success=False, final_response="", turns=3,
                tool_calls=self.tool_call_history, error=error_msg,
                latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
            )
        synthesizer_ms = (time.time() - t0) * 1000

        self._log("🤖 Final Response", final_response, style="green")

        total_ms = (time.time() - t_total) * 1000
        return AgentResult(
            success=True,
            final_response=final_response,
            turns=3,
            tool_calls=self.tool_call_history,
            latency_ms=total_ms,
            latency_breakdown={
                "planner_ms": planner_ms,
                "execution_ms": execution_ms,
                "synthesizer_ms": synthesizer_ms,
                "total_ms": total_ms,
            },
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


def run_pte_agent(user_input: str, verbose: bool = True) -> AgentResult:
    agent = PlanThenExecuteAgent(verbose=verbose)
    return agent.run(user_input)


def run_pte_with_injection(
    user_input: str,
    injection_payload: dict,
    injection_target: str = "all_payments",
    verbose: bool = True,
) -> tuple[AgentResult, list[dict]]:
    data_store = MockDataStore()

    if injection_target == "all_payments":
        data_store.inject_into_all_payments(injection_payload)
    elif injection_target == "all_payments_fields":
        data_store.inject_fields_into_all_payments(injection_payload)
    elif injection_target.startswith("payment:"):
        payment_id = injection_target.split(":")[1]
        data_store.inject_into_payment(payment_id, injection_payload)
    elif injection_target == "all_customers":
        for c in data_store.customers:
            data_store.inject_into_customer(c["id"], injection_payload)
    elif injection_target == "all_customers_fields":
        data_store.inject_fields_into_all_customers(injection_payload)
    elif injection_target.startswith("customer:"):
        customer_id = injection_target.split(":")[1]
        data_store.inject_into_customer(customer_id, injection_payload)
    elif injection_target.startswith("subscription:"):
        sub_id = injection_target.split(":")[1]
        data_store.inject_into_subscription(sub_id, injection_payload)
    elif injection_target == "all_subscriptions":
        data_store.inject_into_all_subscriptions(injection_payload)
    elif injection_target == "all_subscriptions_fields":
        data_store.inject_fields_into_all_subscriptions(injection_payload)

    mock_tools = MockStripeTools(data_store)
    agent = PlanThenExecuteAgent(verbose=verbose, mock_tools=mock_tools)
    result = agent.run(user_input)
    return result, agent.get_mock_call_log()
