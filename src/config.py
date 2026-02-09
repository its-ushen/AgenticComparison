"""
Configuration and environment setup for the payment agent.
"""

import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Config(BaseModel):
    """Application configuration."""

    # API Keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    stripe_api_key: str = os.getenv("STRIPE_API_KEY", "")

    # Model settings
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0  # Deterministic for reproducibility, pick the most likely token. 

    # Agent settings
    max_iterations: int = 10  # Prevent infinite loops

    def validate_keys(self, require_stripe: bool = False) -> bool:
        """
        Check that required API keys are present.

        Args:
            require_stripe: If True, also require STRIPE_API_KEY (for real API mode)
        """
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")
        if require_stripe and not self.stripe_api_key:
            raise ValueError("STRIPE_API_KEY not set in environment")
        return True


# Global config instance
config = Config()
