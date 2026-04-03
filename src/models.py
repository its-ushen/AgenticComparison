"""
Data models for evaluation logging.

EvalRequest: The test setup (payload, user request, expected behavior)
EvalResponse: The results (tool calls, agent response, TCR/ASR scores)
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A single tool call made by the agent."""
    tool: str
    input: dict
    output: dict | list  # Some tools return lists (e.g., customers.read)


class EvalRequest(BaseModel):
    """
    The evaluation test case - what we're testing.

    This captures the "input" side of an evaluation:
    - What the user asked for
    - What malicious payload was injected
    - What we expect the agent to do
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Test identification
    payload_id: str
    operation: str
    attack_vector: str

    # Test setup
    user_request: str
    injection_point: str
    injected_payload: str
    expected_behavior: str
    goal: str

    # Model info
    model_name: str
    provider: str


class EvalResponse(BaseModel):
    """
    The evaluation result - what happened.

    This captures the "output" side of an evaluation:
    - What tool calls the agent made
    - What the agent told the user
    - Whether the task was completed and attack succeeded
    """
    request_id: str  # Links to EvalRequest.id
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Agent behavior
    tool_calls: list[ToolCall]
    agent_response: str

    # Evaluation results
    task_completed: bool
    attack_succeeded: bool
    tcr_reason: str
    asr_reason: str

    # Outcome category
    outcome: str  # IDEAL, COMPROMISED, OVER_REFUSAL, WORST, ERROR

    # Token usage
    input_tokens: int = 0
    output_tokens: int = 0

    # Error tracking (if any)
    error: str | None = None


class EvalRun(BaseModel):
    """
    A complete evaluation run containing multiple request/response pairs.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Run metadata
    agent_type: str  # "react", "pte", or "dual_llm"
    model_name: str
    provider: str
    judge_model: str

    # Filter info (if any)
    operation_filter: str | None = None
    payload_filter: str | None = None

    # Results
    total_payloads: int
    results: list[tuple[EvalRequest, EvalResponse]] = Field(default_factory=list)

    # Aggregate metrics
    @property
    def tcr(self) -> float:
        """Task Completion Rate."""
        valid = [(req, resp) for req, resp in self.results if resp.outcome != "ERROR"]
        if not valid:
            return 0.0
        return sum(resp.task_completed for _, resp in valid) / len(valid)

    @property
    def asr(self) -> float:
        """Attack Success Rate."""
        valid = [(req, resp) for req, resp in self.results if resp.outcome != "ERROR"]
        if not valid:
            return 0.0
        return sum(resp.attack_succeeded for _, resp in valid) / len(valid)

    @property
    def error_count(self) -> int:
        """Number of errors."""
        return sum(1 for _, resp in self.results if resp.outcome == "ERROR")
