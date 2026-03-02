"""
Dual LLM Agent Implementation

Architecture (Simon Willison's pattern, 2023):

  ORCHESTRATOR LLM  — privileged, has tool access, makes all decisions.
                      Never sees raw tool output.

  QUARANTINE LLM    — unprivileged, sees raw tool output (including injection
                      payloads), but can only return structured JSON.
                      Has no tool access — cannot trigger actions.

Flow per tool call:
  Orchestrator decides to call a tool
    → execute_tool() returns raw result (injection payload may be here)
    → Quarantine LLM receives raw result
    → Quarantine strips metadata fields, returns clean structured JSON
    → Orchestrator receives only the clean JSON as its "observation"

Security property:
  Even if the quarantine LLM is fully injected, it cannot call tools.
  The orchestrator makes decisions based on sanitized data only.

Residual risk:
  If the quarantine LLM's output escapes the strict JSON schema (e.g. the
  model adds text outside the JSON), that text could reach the orchestrator.
  Mitigation: parse quarantine output as JSON and discard any non-JSON content.
"""

import json
import time
from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.config import config
from src.tools import TOOLS, execute_tool, get_mock_tools, set_mock_tools, reset_mock_tools, MockStripeTools, MockDataStore
from src.prompts import DUAL_LLM_ORCHESTRATOR_PROMPT, DUAL_LLM_QUARANTINE_PROMPT
from src.base import AgentResult
from src.react_agent import _convert_tools_to_openai_format

console = Console()

OPENAI_TOOLS = _convert_tools_to_openai_format(TOOLS)


# =============================================================================
# Quarantine layer
# =============================================================================

def _parse_quarantine_output(raw: str) -> str:
    """
    Extract only the JSON portion from quarantine LLM output.
    Discards any surrounding text the model may have added.
    This is the schema enforcement layer — if it's not valid JSON, we
    return an empty object rather than letting raw text reach the orchestrator.
    """
    text = raw.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner).strip()

    try:
        parsed = json.loads(text)
        return json.dumps(parsed)  # Re-serialise to strip any whitespace tricks
    except json.JSONDecodeError:
        # Quarantine output is not valid JSON — return empty to be safe
        return json.dumps({"error": "quarantine_parse_failed"})


# =============================================================================
# Dual LLM Agent
# =============================================================================

class DualLLMAgent:
    """
    Dual LLM agent for Stripe payment operations.

    Uses two LLM instances with different privilege levels:
    - Orchestrator: has tool access, sees only sanitized data
    - Quarantine: sees raw data, has no tool access, constrained output

    The orchestrator loop is structurally identical to ReAct — it calls tools
    iteratively. The difference is that every tool result is intercepted and
    sanitized by the quarantine LLM before the orchestrator sees it.
    """

    def __init__(self, verbose: bool = True, mock_tools: MockStripeTools | None = None):
        self.provider = config.llm_provider
        self.model = config.model
        self.max_iterations = config.max_iterations
        self.verbose = verbose
        self.messages: list[dict[str, Any]] = []
        self.tool_call_history: list[dict[str, Any]] = []
        self._custom_mock_tools = mock_tools is not None
        self._quarantine_call_times: list[float] = []

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
        self._quarantine_call_times = []
        if not self._custom_mock_tools:
            reset_mock_tools()

    def get_mock_call_log(self) -> list[dict]:
        return get_mock_tools().get_call_log()

    def _log(self, title: str, content: str, style: str = "blue"):
        if self.verbose:
            console.print(Panel(content, title=title, border_style=style))

    # -------------------------------------------------------------------------
    # Quarantine LLM call
    # -------------------------------------------------------------------------

    def _quarantine(self, tool_name: str, raw_result: Any) -> str:
        """
        Pass raw tool output through the quarantine LLM.
        Returns clean JSON string safe for the orchestrator to consume.
        """
        raw_text = json.dumps(raw_result, indent=2)
        prompt = f"Tool: {tool_name}\n\nRaw output:\n{raw_text}"

        t0 = time.time()
        try:
            if self.provider == "anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    temperature=0,
                    system=DUAL_LLM_QUARANTINE_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_output = response.content[0].text
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=1024,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": DUAL_LLM_QUARANTINE_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                raw_output = response.choices[0].message.content or ""
        except Exception as e:
            raw_output = json.dumps({"error": f"quarantine_api_failed: {e}"})

        self._quarantine_call_times.append((time.time() - t0) * 1000)

        clean = _parse_quarantine_output(raw_output)

        self._log(
            f"🔒 Quarantine: {tool_name}",
            f"RAW (truncated):\n{raw_text[:300]}...\n\nCLEAN:\n{clean}",
            style="yellow",
        )

        return clean

    # -------------------------------------------------------------------------
    # Orchestrator LLM calls (tool-capable, same as ReAct)
    # -------------------------------------------------------------------------

    def _call_orchestrator_anthropic(self):
        return self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=DUAL_LLM_ORCHESTRATOR_PROMPT,
            tools=TOOLS,
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

    # -------------------------------------------------------------------------
    # Main loops
    # -------------------------------------------------------------------------

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
                self._log("❌ API Error", error_msg, style="red")
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False, final_response="", turns=iterations,
                    tool_calls=self.tool_call_history, error=error_msg,
                    latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
                )

            if response.stop_reason == "end_turn":
                final_text = "".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                self._log("🤖 Final Response", final_text, style="green")
                elapsed = (time.time() - t_start) * 1000
                q_times = self._quarantine_call_times
                return AgentResult(
                    success=True,
                    final_response=final_text,
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    latency_ms=elapsed,
                    latency_breakdown={
                        "total_ms": elapsed,
                        "quarantine_calls": len(q_times),
                        "quarantine_total_ms": sum(q_times),
                        "avg_quarantine_ms": sum(q_times) / len(q_times) if q_times else 0,
                    },
                )

            elif response.stop_reason == "tool_use":
                assistant_content = []
                tool_results = []

                for block in response.content:
                    if block.type == "text":
                        self._log("💭 Orchestrator Thinking", block.text, style="magenta")
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                        self._log(
                            f"🔧 Orchestrator Tool Call: {block.name}",
                            json.dumps(block.input, indent=2),
                            style="cyan",
                        )

                        # Execute tool → raw result
                        raw_result = execute_tool(block.name, block.input)

                        # Record for eval (raw, so eval can see what actually happened)
                        self.tool_call_history.append({
                            "tool": block.name,
                            "input": block.input,
                            "output": raw_result,
                        })

                        # Quarantine: sanitize before orchestrator sees it
                        clean_result = self._quarantine(block.name, raw_result)

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": clean_result,  # ← orchestrator sees only this
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
                self._log("❌ API Error", error_msg, style="red")
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False, final_response="", turns=iterations,
                    tool_calls=self.tool_call_history, error=error_msg,
                    latency_ms=elapsed, latency_breakdown={"total_ms": elapsed},
                )

            message = response.choices[0].message

            if not message.tool_calls:
                final_text = message.content or ""
                self._log("🤖 Final Response", final_text, style="green")
                elapsed = (time.time() - t_start) * 1000
                q_times = self._quarantine_call_times
                return AgentResult(
                    success=True,
                    final_response=final_text,
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    latency_ms=elapsed,
                    latency_breakdown={
                        "total_ms": elapsed,
                        "quarantine_calls": len(q_times),
                        "quarantine_total_ms": sum(q_times),
                        "avg_quarantine_ms": sum(q_times) / len(q_times) if q_times else 0,
                    },
                )

            self.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in message.tool_calls
                ]
            })

            if message.content:
                self._log("💭 Orchestrator Thinking", message.content, style="magenta")

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                self._log(
                    f"🔧 Orchestrator Tool Call: {tool_name}",
                    json.dumps(tool_input, indent=2),
                    style="cyan",
                )

                raw_result = execute_tool(tool_name, tool_input)

                self.tool_call_history.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "output": raw_result,
                })

                clean_result = self._quarantine(tool_name, raw_result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": clean_result,  # ← orchestrator sees only this
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


# =============================================================================
# Convenience functions (mirror react_agent.py interface)
# =============================================================================

def run_dual_llm_agent(user_input: str, verbose: bool = True) -> AgentResult:
    """Run the Dual LLM agent."""
    agent = DualLLMAgent(verbose=verbose)
    return agent.run(user_input)


def run_dual_llm_with_injection(
    user_input: str,
    injection_payload: dict,
    injection_target: str = "all_payments",
    verbose: bool = True,
) -> tuple[AgentResult, list[dict]]:
    """
    Run the Dual LLM agent with injected malicious data.
    Same interface as run_with_injection() for eval harness compatibility.
    """
    data_store = MockDataStore()

    if injection_target == "all_payments":
        data_store.inject_into_all_payments(injection_payload)
    elif injection_target.startswith("payment:"):
        payment_id = injection_target.split(":")[1]
        data_store.inject_into_payment(payment_id, injection_payload)
    elif injection_target == "all_customers":
        for c in data_store.customers:
            data_store.inject_into_customer(c["id"], injection_payload)
    elif injection_target.startswith("customer:"):
        customer_id = injection_target.split(":")[1]
        data_store.inject_into_customer(customer_id, injection_payload)
    elif injection_target.startswith("subscription:"):
        sub_id = injection_target.split(":")[1]
        data_store.inject_into_subscription(sub_id, injection_payload)
    elif injection_target == "all_subscriptions":
        data_store.inject_into_all_subscriptions(injection_payload)

    mock_tools = MockStripeTools(data_store)
    agent = DualLLMAgent(verbose=verbose, mock_tools=mock_tools)
    result = agent.run(user_input)
    return result, agent.get_mock_call_log()
