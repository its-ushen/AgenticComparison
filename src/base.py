"""
Shared data structures used across agent implementations.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Result of an agent run. Shared across all agent architectures."""
    success: bool
    final_response: str
    turns: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    # Latency tracking (milliseconds)
    latency_ms: float = 0.0
    latency_breakdown: dict[str, float] = field(default_factory=dict)
    # Token usage (accumulated across all LLM calls in this run)
    input_tokens: int = 0
    output_tokens: int = 0
