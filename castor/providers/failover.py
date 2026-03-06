"""
Multi-Provider Failover — automatically retry with backup providers on failure.

When the primary brain provider fails (timeout, rate limit, connection error),
ProviderFailoverChain tries each configured fallback in order before surfacing
an error to the operator.

Config (robot.rcan.yaml):
    agent:
      provider: huggingface
      model: Qwen/Qwen2.5-VL-7B-Instruct
      fallbacks:
        - provider: ollama
          model: qwen2.5:7b
        - provider: anthropic
          model: claude-haiku-3-5
      fallback_on: [timeout, connection_error, rate_limit]
      fallback_timeout_ms: 3000

Usage:
    chain = ProviderFailoverChain.from_config(config, providers)
    thought = await chain.think(instruction, image=frame)
    print(chain.last_provider_used)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("OpenCastor.ProviderFailover")

# Error categories that trigger fallback
_FALLBACK_TRIGGERS = {
    "timeout": (asyncio.TimeoutError, TimeoutError),
    "connection_error": (ConnectionError, OSError),
    "rate_limit": (),  # matched by string in exception message
}

_RATE_LIMIT_PHRASES = ("rate limit", "429", "too many requests", "quota")


@dataclass
class FallbackSpec:
    """Configuration for a single fallback provider."""

    provider: str
    model: str
    label: str = ""
    timeout_ms: int = 5000


@dataclass
class FailoverResult:
    """Result from a failover chain think() call."""

    thought: Any
    provider_used: str
    model_used: str
    attempts: int
    fallback_used: bool
    latency_ms: float


class ProviderFailoverChain:
    """
    Wraps multiple providers in a priority chain.

    Tries the primary provider first. On failure (categories in
    ``fallback_on``), tries each fallback in order. Exposes
    ``last_provider_used`` for telemetry and audit logging.

    Args:
        primary:       The primary brain provider instance.
        primary_spec:  (provider_name, model_name) for the primary.
        fallbacks:     List of (spec, provider_instance) pairs.
        fallback_on:   Error categories that trigger fallback.
        timeout_ms:    Per-provider timeout in milliseconds.
    """

    def __init__(
        self,
        primary: Any,
        primary_spec: tuple[str, str],
        fallbacks: list[tuple[FallbackSpec, Any]] | None = None,
        fallback_on: list[str] | None = None,
        timeout_ms: int = 5000,
    ) -> None:
        self._primary = primary
        self._primary_spec = primary_spec  # (provider_name, model)
        self._fallbacks = fallbacks or []
        self._fallback_on = set(fallback_on or ["timeout", "connection_error", "rate_limit"])
        self._timeout_ms = timeout_ms

        self.last_provider_used: str = primary_spec[0]
        self.last_model_used: str = primary_spec[1]
        self._stats: dict[str, int] = {}  # provider → success count

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def think(
        self,
        instruction: str,
        image: bytes | None = None,
        **kwargs: Any,
    ) -> FailoverResult:
        """
        Call ``think()`` on the provider chain, failing over on error.

        Returns:
            :class:`FailoverResult` with the thought and metadata.

        Raises:
            Exception: Only if ALL providers fail.
        """
        providers = [(self._primary_spec, self._primary)] + [
            ((spec.provider, spec.model), inst) for spec, inst in self._fallbacks
        ]

        last_exc: Exception | None = None
        attempts = 0

        for i, ((pname, pmodel), provider) in enumerate(providers):
            attempts += 1
            timeout_s = self._timeout_ms / 1000
            t0 = time.perf_counter()

            try:
                thought = await asyncio.wait_for(
                    self._call_think(provider, instruction, image, **kwargs),
                    timeout=timeout_s,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
                self.last_provider_used = pname
                self.last_model_used = pmodel
                self._stats[pname] = self._stats.get(pname, 0) + 1

                if i > 0:
                    logger.info(
                        "Failover succeeded: primary=%s fallback=%s attempt=%d",
                        self._primary_spec[0],
                        pname,
                        i,
                    )

                return FailoverResult(
                    thought=thought,
                    provider_used=pname,
                    model_used=pmodel,
                    attempts=attempts,
                    fallback_used=(i > 0),
                    latency_ms=latency_ms,
                )

            except Exception as exc:
                last_exc = exc
                if not self._should_fallback(exc):
                    raise
                if i < len(providers) - 1:
                    logger.warning(
                        "Provider %s failed (%s), trying fallback %d/%d",
                        pname,
                        type(exc).__name__,
                        i + 1,
                        len(providers) - 1,
                    )
                continue

        raise last_exc or RuntimeError("All providers failed")

    @property
    def stats(self) -> dict[str, int]:
        """Per-provider success counts since creation."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: dict,
        provider_factory: Any,
    ) -> ProviderFailoverChain | None:
        """
        Build a ProviderFailoverChain from an RCAN YAML config dict.

        Args:
            config:           Full robot RCAN config.
            provider_factory: Callable(provider_key, model) → provider instance.
                              Returns None if provider not available.

        Returns:
            Configured chain, or None if no fallbacks are configured.
        """
        agent = config.get("agent", {})
        fallback_specs = agent.get("fallbacks", [])
        if not fallback_specs:
            return None

        primary_key = agent.get("provider", "")
        primary_model = agent.get("model", "")
        timeout_ms = int(agent.get("fallback_timeout_ms", 5000))
        fallback_on = agent.get("fallback_on", ["timeout", "connection_error", "rate_limit"])

        try:
            primary = provider_factory(primary_key, primary_model)
        except Exception as e:
            logger.error("Failed to build primary provider for failover: %s", e)
            return None

        fallbacks = []
        for spec_dict in fallback_specs:
            pkey = spec_dict.get("provider", "")
            pmodel = spec_dict.get("model", "")
            spec = FallbackSpec(
                provider=pkey,
                model=pmodel,
                label=spec_dict.get("label", f"{pkey}/{pmodel}"),
                timeout_ms=spec_dict.get("timeout_ms", timeout_ms),
            )
            try:
                inst = provider_factory(pkey, pmodel)
                if inst is not None:
                    fallbacks.append((spec, inst))
            except Exception as e:
                logger.warning("Skipping fallback %s/%s: %s", pkey, pmodel, e)

        if not fallbacks:
            return None

        return cls(
            primary=primary,
            primary_spec=(primary_key, primary_model),
            fallbacks=fallbacks,
            fallback_on=fallback_on,
            timeout_ms=timeout_ms,
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _call_think(
        self,
        provider: Any,
        instruction: str,
        image: bytes | None,
        **kwargs: Any,
    ) -> Any:
        """Dispatch think() call handling both sync and async providers."""
        think_fn = getattr(provider, "think", None)
        if think_fn is None:
            raise AttributeError(f"Provider {provider!r} has no think() method")

        if image is not None:
            result = think_fn(instruction, image=image, **kwargs)
        else:
            result = think_fn(instruction, **kwargs)

        if asyncio.iscoroutine(result):
            return await result
        return result

    def _should_fallback(self, exc: Exception) -> bool:
        """Return True if this exception category should trigger failover."""
        # Check typed triggers
        for category, exc_types in _FALLBACK_TRIGGERS.items():
            if category in self._fallback_on and exc_types and isinstance(exc, exc_types):
                return True

        # Rate limit: string match
        if "rate_limit" in self._fallback_on:
            msg = str(exc).lower()
            if any(phrase in msg for phrase in _RATE_LIMIT_PHRASES):
                return True

        # Timeout by string (some providers raise generic exceptions)
        if "timeout" in self._fallback_on:
            msg = str(exc).lower()
            if "timeout" in msg or "timed out" in msg:
                return True

        return False
