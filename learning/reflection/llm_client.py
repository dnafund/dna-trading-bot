"""LLM client wrapper — supports Claude (Anthropic) and Gemini (CLI)."""

import json
import logging
import subprocess
from typing import Optional

from learning.config import (
    ANTHROPIC_API_KEY,
    LLM_MODEL_CLAUDE,
    LLM_MODEL_GEMINI,
    LLM_PROVIDER,
)

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM client for Claude and Gemini."""

    def __init__(self, provider: Optional[str] = None):
        self._provider = provider or LLM_PROVIDER

    def generate(self, system: str, prompt: str) -> str:
        """Generate a response from the LLM.

        Args:
            system: System prompt setting the context.
            prompt: User prompt with the analysis data.

        Returns:
            The LLM's response text.

        Raises:
            RuntimeError: If LLM call fails.
        """
        if self._provider == "claude":
            return self._call_claude(system, prompt)
        if self._provider == "gemini":
            return self._call_gemini(system, prompt)
        raise ValueError(f"Unknown LLM provider: {self._provider}")

    def _call_claude(self, system: str, prompt: str) -> str:
        """Call Claude via Anthropic SDK."""
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. "
                "Set it via environment variable or use --no-llm flag."
            )

        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. "
                "Run: pip install anthropic"
            )

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL_CLAUDE,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        logger.info(
            "Claude response: %d chars, %d input + %d output tokens",
            len(text),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return text

    def _call_gemini(self, system: str, prompt: str) -> str:
        """Call Gemini via CLI (gemini-cli or google-genai)."""
        full_prompt = f"{system}\n\n---\n\n{prompt}"

        # Try gemini CLI first
        try:
            result = subprocess.run(
                ["gemini", "-p", full_prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                text = result.stdout.strip()
                logger.info("Gemini CLI response: %d chars", len(text))
                return text
        except FileNotFoundError:
            logger.debug("gemini CLI not found, trying google-genai SDK")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Gemini CLI timed out after 120s")

        # Fallback: try google-genai SDK
        try:
            from google import genai

            client = genai.Client()
            response = client.models.generate_content(
                model=LLM_MODEL_GEMINI,
                contents=full_prompt,
            )
            text = response.text or ""
            logger.info("Gemini SDK response: %d chars", len(text))
            return text
        except ImportError:
            raise RuntimeError(
                "Neither gemini CLI nor google-genai SDK available. "
                "Install: pip install google-genai  OR  npm i -g @anthropic-ai/gemini-cli"
            )
        except Exception as e:
            raise RuntimeError(f"Gemini SDK call failed: {e}")
