"""Task-aware model router — selects the best provider for each task category."""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class TaskCategory(str, Enum):
    SENSOR_POLL = "sensor_poll"  # fast, cheap
    NAVIGATION = "navigation"  # mid-tier
    REASONING = "reasoning"  # high-capability (default)
    CODE = "code"  # specialized coding model
    SEARCH = "search"  # agentic search
    VISION = "vision"  # multimodal
    SAFETY = "safety"  # NEVER downgrade — always top tier


# Default category → preferred provider ordered list
# First available provider in the list wins
_DEFAULT_ROUTING: dict[TaskCategory, list[str]] = {
    TaskCategory.SENSOR_POLL: ["ollama", "mlx", "llamacpp", "gemini", "anthropic"],
    TaskCategory.NAVIGATION: ["anthropic", "gemini", "openai", "ollama"],
    TaskCategory.REASONING: ["anthropic", "openai", "gemini", "ollama"],
    TaskCategory.CODE: ["deepseek", "openai", "anthropic", "ollama"],
    TaskCategory.SEARCH: ["gemini", "anthropic", "openai"],
    TaskCategory.VISION: ["gemini", "anthropic", "openai"],
    TaskCategory.SAFETY: ["anthropic", "openai", "gemini"],
}


class TaskRouter:
    """Routes task categories to the best available provider."""

    def __init__(
        self,
        routing_table: dict[str, list[str]] | None = None,
    ) -> None:
        self._table: dict[TaskCategory, list[str]] = _DEFAULT_ROUTING.copy()
        if routing_table:
            for k, v in routing_table.items():
                try:
                    self._table[TaskCategory(k)] = v
                except ValueError:
                    logger.warning("Unknown task category in routing config: %s", k)

    def select(
        self,
        category: TaskCategory | str,
        available_providers: list[str],
    ) -> str | None:
        """Return best available provider for category, or None if none available."""
        if isinstance(category, str):
            try:
                category = TaskCategory(category)
            except ValueError:
                logger.warning("Unknown category %r, using REASONING", category)
                category = TaskCategory.REASONING
        preferred = self._table.get(category, self._table[TaskCategory.REASONING])
        for p in preferred:
            if p in available_providers:
                logger.debug("TaskRouter: %s -> %s", category.value, p)
                return p
        # Fallback: first available
        if available_providers:
            logger.debug(
                "TaskRouter: no preferred for %s, fallback to %s",
                category.value,
                available_providers[0],
            )
            return available_providers[0]
        return None

    def update(self, category: str, providers: list[str]) -> None:
        """Update routing preference for a category."""
        self._table[TaskCategory(category)] = providers
