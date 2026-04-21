"""
Trinity Model Router - Route LLM calls to appropriate endpoints.

Supports:
- Direct Ollama (local, fast)
- BeigeBox operator (route to OpenRouter, cloud models)
"""

import json
import httpx
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .logger import TrinityLogger, TrinityLogConfig


@dataclass
class ModelConfig:
    """Configuration for a model."""
    name: str
    provider: str  # "ollama" or "beigebox"
    model_id: str
    route_via_beigebox: bool


class TrinityModelRouter:
    """Routes LLM calls to appropriate endpoints."""

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        beigebox_url: str = "http://localhost:8000",
        route_via_beigebox: bool = False,
        logger: Optional[TrinityLogger] = None,
    ):
        self.ollama_url = ollama_url.rstrip('/')
        self.beigebox_url = beigebox_url.rstrip('/')
        self.route_via_beigebox = route_via_beigebox
        self.logger = logger if logger is not None else TrinityLogger('noop', TrinityLogConfig(enabled=False))

        # Model configs - can route via Ollama (local) or BeigeBox (cloud via OpenRouter)
        if route_via_beigebox:
            self.models = {
                "haiku": ModelConfig(
                    name="Claude Haiku",
                    provider="beigebox",
                    model_id="claude/claude-haiku-4-5-20251001",
                    route_via_beigebox=True,
                ),
                "grok-4.1-fast": ModelConfig(
                    name="Claude Opus 4.7",
                    provider="beigebox",
                    model_id="anthropic/claude-opus-4-7",
                    route_via_beigebox=True,
                ),
                "arcee-trinity-large": ModelConfig(
                    name="Arcee Trinity Large (OpenRouter)",
                    provider="beigebox",
                    model_id="arcee-ai/trinity-large-thinking",
                    route_via_beigebox=True,
                ),
                "qwen-max": ModelConfig(
                    name="Qwen Max (OpenRouter)",
                    provider="beigebox",
                    model_id="qwen/qwen-max",
                    route_via_beigebox=True,
                ),
                "deepseek-coder": ModelConfig(
                    name="DeepSeek Coder (OpenRouter)",
                    provider="beigebox",
                    model_id="deepseek/deepseek-coder",
                    route_via_beigebox=True,
                ),
            }
        else:
            self.models = {
                "haiku": ModelConfig(
                    name="Qwen 4B (Haiku equivalent)",
                    provider="ollama",
                    model_id="qwen3:4b",
                    route_via_beigebox=False,
                ),
                "grok-4.1-fast": ModelConfig(
                    name="Qwen 32B (Deep Reasoner)",
                    provider="ollama",
                    model_id="qwen2.5:32b-instruct-q4_K_M",
                    route_via_beigebox=False,
                ),
                "arcee-trinity-large": ModelConfig(
                    name="Gemma 12B (Specialist)",
                    provider="ollama",
                    model_id="gemma3:12b",
                    route_via_beigebox=False,
                ),
                "qwen-max": ModelConfig(
                    name="Llama 3.2 3B (Appellate)",
                    provider="ollama",
                    model_id="llama3.2:3b",
                    route_via_beigebox=False,
                ),
                "deepseek-coder": ModelConfig(
                    name="Gemma 4B (Coder)",
                    provider="ollama",
                    model_id="gemma3:4b",
                    route_via_beigebox=False,
                ),
            }

    async def call_model(
        self,
        model_key: str,
        prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.0,
        system: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Call a model and return response.

        Args:
            model_key: Key in self.models dict
            prompt: User prompt
            max_tokens: Max output tokens (NOTE: accepted but not forwarded to Ollama)
            temperature: Temperature (0=deterministic)
            system: System prompt

        Returns:
            {
                "content": "...",
                "tokens_used": 1234,
                "model": "...",
                "provider": "...",
            }
        """
        if model_key not in self.models:
            raise ValueError(f"Unknown model: {model_key}. Available: {list(self.models.keys())}")

        config = self.models[model_key]

        if config.provider == "ollama":
            return await self._call_ollama(config, prompt, max_tokens, temperature, system)
        elif config.provider == "beigebox":
            return await self._call_beigebox(config, prompt, max_tokens, temperature, system)
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    async def _call_ollama(
        self,
        config: ModelConfig,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: Optional[str],
    ) -> Dict[str, Any]:
        """Call Ollama API directly.

        Note: max_tokens is accepted for interface compatibility but is NOT included
        in the Ollama payload — Ollama uses its own defaults for output length.
        """
        self.logger.trace(
            "max_tokens accepted but not forwarded to Ollama payload",
            phase="model_router",
            model=config.model_id,
            max_tokens=max_tokens,
        )

        async with httpx.AsyncClient(timeout=300) as client:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": config.model_id,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            }

            self.logger.llm_request(config.model_id, prompt, 0, phase="model_router")

            try:
                response = await client.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                )

                if response.status_code != 200:
                    self.logger.error(
                        "Ollama HTTP error",
                        phase="model_router",
                        model=config.model_id,
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    raise Exception(f"Ollama HTTP {response.status_code}: {response.text[:200]}")

                data = response.json()

                content = data.get("message", {}).get("content", "")
                tokens_used = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

                if not content:
                    self.logger.warn(
                        "LLM returned empty content",
                        phase="model_router",
                        model=config.model_id,
                        response_preview=str(data)[:200],
                    )

                # llm_response handles the tokens_used==0 warning internally
                self.logger.llm_response(config.model_id, content, tokens_used, phase="model_router")

                return {
                    "content": content,
                    "tokens_used": tokens_used,
                    "model": config.name,
                    "provider": "ollama",
                    "stop_reason": "stop",
                }
            except Exception as e:
                self.logger.error("Ollama call failed", phase="model_router", exc=e, model=config.model_id)
                raise

    async def _call_beigebox(
        self,
        config: ModelConfig,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: Optional[str],
    ) -> Dict[str, Any]:
        """Call model via BeigeBox operator (routes to OpenRouter, etc)."""
        self.logger.llm_request(config.model_id, prompt[:200], 0, phase="model_router")

        async with httpx.AsyncClient(timeout=300) as client:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            # OpenAI-compatible payload
            payload = {
                "model": config.model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }

            try:
                response = await client.post(
                    f"{self.beigebox_url}/v1/chat/completions",
                    json=payload,
                )

                if response.status_code != 200:
                    self.logger.error(
                        "BeigeBox HTTP error",
                        phase="model_router",
                        model=config.model_id,
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    raise Exception(f"BeigeBox HTTP {response.status_code}: {response.text[:200]}")

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                tokens_used = data.get("usage", {}).get("total_tokens", 0)

                if not content:
                    self.logger.warn(
                        "LLM returned empty content",
                        phase="model_router",
                        model=config.model_id,
                        tokens=tokens_used,
                    )

                self.logger.llm_response(config.model_id, content[:500], tokens_used, phase="model_router")

                return {
                    "content": content,
                    "tokens_used": tokens_used,
                    "model": config.name,
                    "provider": "beigebox",
                    "stop_reason": data.get("choices", [{}])[0].get("finish_reason", "stop"),
                }
            except Exception as e:
                self.logger.error("BeigeBox call failed", phase="model_router", exc=e, model=config.model_id)
                raise

    def get_available_models(self) -> Dict[str, str]:
        """Return available models."""
        return {key: config.name for key, config in self.models.items()}

    def register_model(self, key: str, config: ModelConfig) -> None:
        """Register a new model at runtime."""
        self.models[key] = config
