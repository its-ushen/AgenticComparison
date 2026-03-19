"""Config — loads from .env, supports anthropic and openai-compatible providers."""

import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Config(BaseModel):
    # LLM Provider: "anthropic" or "openai"
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")

    # Anthropic settings
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # OpenAI-compatible settings (for Groq, OpenRouter, Together, etc.)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")

    # Model settings
    model: str = os.getenv("MODEL_NAME", "claude-haiku-4-5")
    judge_model: str = os.getenv("JUDGE_MODEL", "claude-haiku-4-5")
    max_tokens: int = 4096
    temperature: float = 0.0  # Deterministic for reproducibility

    # Agent settings
    max_iterations: int = 10  # Prevent infinite loops

    def validate_keys(self) -> bool:
        if self.llm_provider == "anthropic":
            if not self.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY not set in environment")
        elif self.llm_provider == "openai":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY not set in environment (needed for OpenAI-compatible provider)")
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {self.llm_provider}. Use 'anthropic' or 'openai'")
        return True


config = Config()
