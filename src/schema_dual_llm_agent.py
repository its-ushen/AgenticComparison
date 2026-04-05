"""Schema Dual LLM — Dual LLM with deterministic per-tool field allowlist filtering."""

import json
import time
from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.config import config
from src.tools import TOOLS, ANTHROPIC_TOOLS, execute_tool, get_mock_tools, set_mock_tools, reset_mock_tools, MockStripeTools, MockDataStore, _api_name_to_dot
from src.prompts import DUAL_LLM_ORCHESTRATOR_PROMPT
from src.base import AgentResult
from src.react_agent import _convert_tools_to_openai_format

console = Console()

OPENAI_TOOLS = _convert_tools_to_openai_format(TOOLS)


TOOL_SCHEMAS: dict[str, list[str]] = {
    "customers.create":      ["id", "email"],
    "customers.read":        ["id", "email"],
    "paymentIntents.read":   ["id", "amount", "currency", "status", "customer"],
    "refunds.create":        ["id", "amount", "status", "payment_intent"],
    "invoices.create":       ["id", "status", "customer"],
    "invoices.read":         ["id", "status", "customer", "amount_due"],
    "invoices.update":       ["id", "status", "hosted_invoice_url"],
    "invoiceItems.create":   ["id", "customer", "price", "invoice"],
    "products.create":       ["id"],
    "products.read":         ["id", "name"],
    "prices.create":         ["id", "unit_amount", "currency", "product"],
    "prices.read":           ["id", "unit_amount", "currency", "product"],
    "paymentLinks.create":   ["id", "url", "active"],
    "subscriptions.read":    ["id", "status", "customer", "cancel_at_period_end", "current_period_end"],
    "subscriptions.update":  ["id", "status", "cancel_at_period_end"],
    "balance.read":          ["available", "pending"],
}


def _extract_fields(raw_result: Any, allowed_fields: list[str]) -> Any:
    if isinstance(raw_result, dict):
        return {k: v for k, v in raw_result.items() if k in allowed_fields}
    elif isinstance(raw_result, list):
        return [
            {k: v for k, v in item.items() if k in allowed_fields}
            if isinstance(item, dict) else item
            for item in raw_result
        ]
    return raw_result


class SchemaDualLLMAgent:

    def __init__(self, verbose: bool = True, mock_tools: MockStripeTools | None = None):
        self.provider = config.llm_provider
        self.model = config.model
        self.max_iterations = config.max_iterations
        self.verbose = verbose
        self.messages: list[dict[str, Any]] = []
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
        self.messages = []
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

    def _filter(self, tool_name: str, raw_result: Any) -> str:
        allowed = TOOL_SCHEMAS.get(tool_name, [])
        clean = _extract_fields(raw_result, allowed) if allowed else {}
        result_str = json.dumps(clean)
        self._log(f"📋 {tool_name}", f"allowed: {allowed}\nresult: {result_str}", style="yellow")
        return result_str

    def _call_orchestrator_anthropic(self):
        return self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=DUAL_LLM_ORCHESTRATOR_PROMPT,
            tools=ANTHROPIC_TOOLS,
            messages=self.messages,
        )

    def _call_orchestrator_openai(self):
        openai_messages = [{"role": "system", "content": DUAL_LLM_ORCHESTRATOR_PROMPT}]
        openai_messages.extend(self.messages)
        return self.client.chat.completions.create(
            model=self.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            tools=OPENAI_TOOLS,
            messages=openai_messages,
        )

    def _run_anthropic(self, user_input: str) -> AgentResult:
        self.reset()
        self.messages.append({"role": "user", "content": user_input})
        self._log("👤 User Input", user_input, style="cyan")
        t_start = time.time()

        iterations = 0
        while iterations < self.max_iterations:
            iterations += 1
            try:
                response = self._call_orchestrator_anthropic()
            except Exception as e:
                error_msg = f"Orchestrator API failed: {type(e).__name__}: {e}"
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False, final_response="", turns=iterations,
                    tool_calls=self.tool_call_history, error=error_msg,
                    latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
                )

            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens

            if response.stop_reason == "end_turn":
                final_text = "".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                self._log("🤖 Final Response", final_text, style="green")
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=True,
                    final_response=final_text,
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    latency_ms=elapsed,
                    latency_breakdown={"total_ms": elapsed},
                    input_tokens=self._input_tokens,
                    output_tokens=self._output_tokens,
                )

            elif response.stop_reason == "tool_use":
                assistant_content = []
                tool_results = []

                for block in response.content:
                    if block.type == "text":
                        self._log("💭 Thinking", block.text, style="magenta")
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        tool_name_dot = _api_name_to_dot(block.name)
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                        self._log(f"🔧 {tool_name_dot}", json.dumps(block.input, indent=2), style="cyan")

                        raw_result = execute_tool(tool_name_dot, block.input)
                        self.tool_call_history.append({
                            "tool": tool_name_dot,
                            "input": block.input,
                            "output": raw_result,
                        })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": self._filter(tool_name_dot, raw_result),
                        })

                self.messages.append({"role": "assistant", "content": assistant_content})
                self.messages.append({"role": "user", "content": tool_results})
            else:
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False, final_response="", turns=iterations,
                    tool_calls=self.tool_call_history,
                    error=f"Unexpected stop reason: {response.stop_reason}",
                    latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
                )

        elapsed = (time.time() - t_start) * 1000
        return AgentResult(
            success=False, final_response="", turns=iterations,
            tool_calls=self.tool_call_history, error="Max iterations reached",
            latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
        )

    def _run_openai(self, user_input: str) -> AgentResult:
        self.reset()
        self.messages.append({"role": "user", "content": user_input})
        self._log("👤 User Input", user_input, style="cyan")
        t_start = time.time()

        iterations = 0
        while iterations < self.max_iterations:
            iterations += 1
            try:
                response = self._call_orchestrator_openai()
            except Exception as e:
                error_msg = f"Orchestrator API failed: {type(e).__name__}: {e}"
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False, final_response="", turns=iterations,
                    tool_calls=self.tool_call_history, error=error_msg,
                    latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
                )

            message = response.choices[0].message
            self._input_tokens += response.usage.prompt_tokens
            self._output_tokens += response.usage.completion_tokens

            if not message.tool_calls:
                final_text = message.content or ""
                self._log("🤖 Final Response", final_text, style="green")
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=True,
                    final_response=final_text,
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    latency_ms=elapsed,
                    latency_breakdown={"total_ms": elapsed},
                    input_tokens=self._input_tokens,
                    output_tokens=self._output_tokens,
                )

            self.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in message.tool_calls
                ]
            })

            if message.content:
                self._log("💭 Thinking", message.content, style="magenta")

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                raw_result = execute_tool(tool_name, tool_input)
                self.tool_call_history.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "output": raw_result,
                })

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._filter(tool_name, raw_result),
                })

        elapsed = (time.time() - t_start) * 1000
        return AgentResult(
            success=False, final_response="", turns=iterations,
            tool_calls=self.tool_call_history, error="Max iterations reached",
            latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
        )

    def run(self, user_input: str) -> AgentResult:
        if self.provider == "anthropic":
            return self._run_anthropic(user_input)
        else:
            return self._run_openai(user_input)


def run_schema_dual_llm_agent(user_input: str, verbose: bool = True) -> AgentResult:
    agent = SchemaDualLLMAgent(verbose=verbose)
    return agent.run(user_input)


def run_schema_dual_llm_with_injection(
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
        data_store.inject_into_payment(injection_target.split(":")[1], injection_payload)
    elif injection_target == "all_customers":
        for c in data_store.customers:
            data_store.inject_into_customer(c["id"], injection_payload)
    elif injection_target == "all_customers_fields":
        data_store.inject_fields_into_all_customers(injection_payload)
    elif injection_target.startswith("customer:"):
        data_store.inject_into_customer(injection_target.split(":")[1], injection_payload)
    elif injection_target == "all_subscriptions":
        data_store.inject_into_all_subscriptions(injection_payload)
    elif injection_target == "all_subscriptions_fields":
        data_store.inject_fields_into_all_subscriptions(injection_payload)
    elif injection_target.startswith("subscription:"):
        data_store.inject_into_subscription(injection_target.split(":")[1], injection_payload)

    mock_tools = MockStripeTools(data_store)
    agent = SchemaDualLLMAgent(verbose=verbose, mock_tools=mock_tools)
    result = agent.run(user_input)
    return result, agent.get_mock_call_log()
