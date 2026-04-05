"""ReAct agent — baseline architecture for the security evaluation."""

import json
import time
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from src.config import config
from src.tools import TOOLS, ANTHROPIC_TOOLS, execute_tool, get_mock_tools, set_mock_tools, reset_mock_tools, MockStripeTools, MockDataStore, _api_name_to_dot
from src.prompts import REACT_SYSTEM_PROMPT
from src.base import AgentResult

console = Console()


def _convert_tools_to_openai_format(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool format to OpenAI function calling format."""
    openai_tools = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            }
        })
    return openai_tools


OPENAI_TOOLS = _convert_tools_to_openai_format(TOOLS)


class ReActAgent:

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

        # Initialize the appropriate client
        if self.provider == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        else:  # openai-compatible
            import openai
            self.client = openai.OpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
            )

        # Set up mock tools (allows custom injection scenarios)
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

    def _call_llm_anthropic(self):
        response = self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=REACT_SYSTEM_PROMPT,
            tools=ANTHROPIC_TOOLS,
            messages=self.messages,
        )
        return response

    def _call_llm_openai(self):
        # prepend system message for OpenAI format
        openai_messages = [{"role": "system", "content": REACT_SYSTEM_PROMPT}]
        openai_messages.extend(self.messages)

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            tools=OPENAI_TOOLS,
            messages=openai_messages,
        )
        return response

    def _process_tool_call_simple(self, tool_name: str, tool_input: dict) -> dict[str, Any]:
        self._log(
            f"🔧 Tool Call: {tool_name}",
            json.dumps(tool_input, indent=2),
            style="yellow"
        )

        # Execute the tool
        result = execute_tool(tool_name, tool_input)

        # Record the tool call
        self.tool_call_history.append({
            "tool": tool_name,
            "input": tool_input,
            "output": result,
        })

        self._log(
            f"📋 Tool Result: {tool_name}",
            json.dumps(result, indent=2),
            style="green"
        )

        return result

    def _run_anthropic(self, user_input: str) -> AgentResult:
        """Run the agent loop using Anthropic API."""
        self.reset()
        self.messages.append({"role": "user", "content": user_input})
        self._log("👤 User Input", user_input, style="cyan")
        t_start = time.time()

        iterations = 0
        while iterations < self.max_iterations:
            iterations += 1
            try:
                response = self._call_llm_anthropic()
            except Exception as e:
                error_msg = f"API call failed: {type(e).__name__}: {e}"
                self._log("❌ API Error", error_msg, style="red")
                return AgentResult(
                    success=False,
                    final_response="",
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    error=error_msg,
                )

            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens

            if response.stop_reason == "end_turn":
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
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
                        result = self._process_tool_call_simple(tool_name_dot, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"<external_content>\n{json.dumps(result)}\n</external_content>",
                        })

                self.messages.append({"role": "assistant", "content": assistant_content})
                self.messages.append({"role": "user", "content": tool_results})
            else:
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False,
                    final_response="",
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    error=f"Unexpected stop reason: {response.stop_reason}",
                    latency_ms=elapsed,
                    latency_breakdown={"total_ms": elapsed},
                )

        elapsed = (time.time() - t_start) * 1000
        return AgentResult(
            success=False,
            final_response="",
            turns=iterations,
            tool_calls=self.tool_call_history,
            error="Max iterations reached",
            latency_ms=elapsed,
            latency_breakdown={"total_ms": elapsed},
        )

    def _run_openai(self, user_input: str) -> AgentResult:
        """Run the agent loop using OpenAI-compatible API."""
        self.reset()
        self.messages.append({"role": "user", "content": user_input})
        self._log("👤 User Input", user_input, style="cyan")
        t_start = time.time()

        iterations = 0
        while iterations < self.max_iterations:
            iterations += 1
            try:
                response = self._call_llm_openai()
            except Exception as e:
                error_msg = f"API call failed: {type(e).__name__}: {e}"
                self._log("❌ API Error", error_msg, style="red")
                elapsed = (time.time() - t_start) * 1000
                return AgentResult(
                    success=False,
                    final_response="",
                    turns=iterations,
                    tool_calls=self.tool_call_history,
                    error=error_msg,
                    latency_ms=elapsed,
                    latency_breakdown={"total_ms": elapsed},
                )
            message = response.choices[0].message
            self._input_tokens += response.usage.prompt_tokens
            self._output_tokens += response.usage.completion_tokens

            # Check if done (no tool calls)
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

            # Process tool calls
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
                self._log("💭 Thinking", message.content, style="magenta")

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                result = self._process_tool_call_simple(tool_name, tool_input)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"<external_content>\n{json.dumps(result)}\n</external_content>",
                })

        elapsed = (time.time() - t_start) * 1000
        return AgentResult(
            success=False,
            final_response="",
            turns=iterations,
            tool_calls=self.tool_call_history,
            error="Max iterations reached",
            latency_ms=elapsed,
            latency_breakdown={"total_ms": elapsed},
        )

    def run(self, user_input: str) -> AgentResult:
        if self.provider == "anthropic":
            return self._run_anthropic(user_input)
        else:
            return self._run_openai(user_input)


def run_react_agent(user_input: str, verbose: bool = True) -> AgentResult:
    agent = ReActAgent(verbose=verbose)
    return agent.run(user_input)


def run_with_injection(
    user_input: str,
    injection_payload: dict,
    injection_target: str = "all_payments",
    verbose: bool = True
) -> tuple[AgentResult, list[dict]]:
    """
    Run the agent with injected malicious data.

    Args:
        user_input: The legitimate user request
        injection_payload: Malicious metadata to inject
        injection_target: Where to inject ("all_payments", "payment:{id}", "customer:{id}")
        verbose: Whether to log output

    Returns:
        Tuple of (AgentResult, call_log) for analysis
    """
    # Set up mock tools with injection
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

    agent = ReActAgent(verbose=verbose, mock_tools=mock_tools)
    result = agent.run(user_input)
    return result, agent.get_mock_call_log()
