"""
Embedding Interpreter — semantic perception layer for the tiered brain.

Multi-backend orchestrator that wraps any EmbeddingBackend implementation
(CLIP local by default, Gemini Embedding 2 premium, or ImageBind/CLAP Tier 1)
as pre/post-think hooks around the L0/L1/L2 brain pipeline.

Three roles:
  Pre-think  — embed current scene, score vs. mission goal, retrieve K nearest
               past episodes (RAG).  Forces L2 escalation when goal similarity
               drops below threshold.
  Post-think — persist the episode (scene embedding + action + outcome) to a
               local vector store for future retrieval.
  RAG inject — formats nearest episodes as a context string injected into the
               L2 planner prompt so the planner can learn from past situations.

The interpreter is **best-effort and non-blocking**: if the backend is missing,
it falls back to mock mode and the brain continues without semantic guidance.

RCAN config key: ``interpreter``

Example::

    interpreter:
      enabled: true
      backend: auto          # auto / local / gemini / mock / local_extended
      goal_similarity_threshold: 0.65
      novelty_threshold: 0.4
      episode_store: ~/.opencastor/episodes/
      max_episodes: 2000
      rag_k: 3
      gemini:
        dimensions: 1536
      local:
        model: openai/clip-vit-base-patch32
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .providers.base import Thought

logger = logging.getLogger("OpenCastor.EmbeddingInterpreter")

_TICK_COUNTER = 0
_TICK_LOCK = threading.Lock()


def _next_tick() -> int:
    global _TICK_COUNTER
    with _TICK_LOCK:
        _TICK_COUNTER += 1
        return _TICK_COUNTER


@dataclass
class SceneContext:
    """Output of a pre-think embedding pass.

    Attributes:
        embedding:        Float32 vector of shape ``(dimensions,)``.
        goal_similarity:  Cosine similarity to mission goal embedding, -1..1.
                          1.0 when no goal has been set or provider is unavailable.
        nearest_episodes: Up to ``rag_k`` past episodes sorted by similarity.
        should_escalate:  True if ``goal_similarity < goal_similarity_threshold``.
        tick_id:          Monotonically increasing tick identifier.
        backend:          Name of the backend that produced this context.
        latency_ms:       Time taken for pre_think in milliseconds.
    """

    embedding: np.ndarray
    goal_similarity: float = 1.0
    nearest_episodes: list[dict[str, Any]] = field(default_factory=list)
    should_escalate: bool = False
    tick_id: int = 0
    backend: str = "unknown"
    latency_ms: float = 0.0
    is_null: bool = False  # True when returned by _null_context() on error — skip storage


# Sentinel used when no pre_think result is available
def _null_context(backend: str = "none", dims: int = 1) -> SceneContext:
    """Return a zero-vector SceneContext marked as null so post_think skips storage."""
    return SceneContext(
        embedding=np.zeros(dims, dtype=np.float32),
        backend=backend,
        is_null=True,
    )


class EmbeddingInterpreter:
    """Multi-backend semantic pre/post-think interpreter.

    Selects an embedding backend at construction time based on ``config.backend``:
    - ``"gemini"`` → GeminiEmbeddingProvider (Tier 2 premium)
    - ``"local"``  → CLIPEmbeddingProvider (Tier 0 default)
    - ``"auto"``   → Try Gemini first; fall back to CLIP
    - ``"mock"``   → CLIPEmbeddingProvider in mock mode (for testing)
    - ``"local_extended"`` → ImageBind if available, else CLIP

    Args:
        config: The ``interpreter`` sub-dict from the RCAN config (not the full config).
    """

    def __init__(self, config: dict):
        self._cfg = config
        self._enabled = config.get("enabled", False)
        self._goal_sim_threshold = float(config.get("goal_similarity_threshold", 0.65))
        self._novelty_threshold = float(config.get("novelty_threshold", 0.4))
        self._rag_k = int(config.get("rag_k", 3))
        self._max_episodes = int(config.get("max_episodes", 2000))

        store_path = config.get("episode_store", "~/.opencastor/episodes/")
        self._store_dir = Path(os.path.expanduser(store_path))
        self._store_dir.mkdir(parents=True, exist_ok=True)

        # Select backend
        self._backend = self._select_backend(config)

        # Episode store in memory (loaded lazily)
        self._embeddings: np.ndarray | None = None  # shape (N, D)
        self._meta: list[dict] = []
        self._store_lock = threading.Lock()
        self._load_episode_store()

        # Goal embedding
        self._goal_embedding: np.ndarray | None = None

        # Stats (for /api/interpreter/status)
        self._request_count: int = 0
        self._escalation_count: int = 0
        self._latency_samples: list[float] = []
        self._last_goal_similarity: float | None = None
        self._stats_lock = threading.Lock()

        # Prometheus metrics (registered lazily to avoid import-time side effects)
        self._m_requests = None
        self._m_errors = None
        self._m_escalations = None
        self._m_latency_hist = None
        self._m_similarity = None
        self._m_episodes = None
        self._init_metrics()

        logger.info(
            "EmbeddingInterpreter ready — backend=%s dims=%d store=%s",
            self._backend.backend_name,
            self._backend.dimensions,
            self._store_dir,
        )

    # ── Backend selection ─────────────────────────────────────────────────────

    @staticmethod
    def _select_backend(config: dict):
        from .providers.clip_embedding_provider import get_clip_provider
        from .providers.gemini_embedding_provider import GeminiEmbeddingProvider

        backend_name = config.get("backend", "auto")

        if backend_name == "gemini":
            return GeminiEmbeddingProvider(config.get("gemini", {}))
        elif backend_name == "local":
            return get_clip_provider(config.get("local", {}))
        elif backend_name == "auto":
            g = GeminiEmbeddingProvider(config.get("gemini", {}))
            return g if g.available else get_clip_provider(config.get("local", {}))
        elif backend_name == "mock":
            from .providers.clip_embedding_provider import CLIPEmbeddingProvider

            return CLIPEmbeddingProvider({"model": "mock"})
        elif backend_name == "local_extended":
            try:
                from .providers.imagebind_provider import ImageBindProvider

                p = ImageBindProvider(config.get("local", {}))
                if p.available:
                    return p
            except Exception:
                pass
            return get_clip_provider(config.get("local", {}))
        else:
            return get_clip_provider(config.get("local", {}))

    # ── Metrics init ─────────────────────────────────────────────────────────

    def _init_metrics(self) -> None:
        """Register Prometheus metrics with the global MetricsRegistry."""
        try:
            from .metrics import Counter, Gauge, ProviderLatencyTracker, get_registry

            reg = get_registry()

            def _ensure_counter(name: str, help_text: str, labels: tuple):
                if name not in reg._counters:
                    reg._counters[name] = Counter(name, help_text, labels)
                return reg._counters[name]

            def _ensure_gauge(name: str, help_text: str):
                if name not in reg._gauges:
                    reg._gauges[name] = Gauge(name, help_text)
                return reg._gauges[name]

            self._m_requests = _ensure_counter(
                "opencastor_embedding_requests_total",
                "Embedding requests by backend and modality",
                ("backend", "modality"),
            )
            self._m_errors = _ensure_counter(
                "opencastor_embedding_errors_total",
                "Embedding errors by backend and error type",
                ("backend", "error_type"),
            )
            self._m_escalations = _ensure_counter(
                "opencastor_embedding_escalations_total",
                "L2 escalations triggered by interpreter",
                ("backend",),
            )
            # Per-backend latency tracking (supports backend label)
            lat_key = "opencastor_embedding_latency_ms"
            if lat_key not in reg._histograms:
                reg._histograms[lat_key] = ProviderLatencyTracker(
                    buckets=(1, 5, 10, 25, 50, 100, 200, 500, 1000, 5000)
                )
            self._m_latency_hist = reg._histograms[lat_key]
            self._m_similarity = _ensure_gauge(
                "opencastor_embedding_goal_similarity",
                "Last tick goal similarity",
            )
            self._m_episodes = _ensure_gauge(
                "opencastor_embedding_episode_count",
                "Episode store size",
            )
        except Exception as exc:
            logger.debug("Could not init embedding metrics: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when the interpreter is enabled via config."""
        return self._enabled

    def set_goal(self, goal_text: str) -> None:
        """Embed and store the current mission goal.

        Args:
            goal_text: Natural-language description of the current mission.
        """
        emb = self._backend.embed(text=goal_text)
        with self._stats_lock:
            self._goal_embedding = emb
        logger.info("Goal embedding updated (%d dims): %.60s…", len(emb), goal_text)

    def pre_think(
        self,
        image_bytes: bytes | None,
        instruction: str,
        sensor_data: dict | None = None,
    ) -> SceneContext:
        """Embed the current scene and score it against mission and past episodes.

        Args:
            image_bytes:  Raw camera frame (JPEG/PNG) or None.
            instruction:  Current task instruction text.
            sensor_data:  Optional dict of sensor readings (depth, battery, …).

        Returns:
            :class:`SceneContext` with embedding, similarity, and escalation flag.
        """
        t_start = time.perf_counter()
        tick_id = _next_tick()
        modality = "scene" if image_bytes is not None else "text"

        try:
            scene_emb = self._backend.embed(text=instruction, image_bytes=image_bytes)

            # Increment request counter
            if self._m_requests is not None:
                try:
                    self._m_requests.inc(backend=self._backend.backend_name, modality=modality)
                except Exception:
                    pass

            # Goal similarity
            goal_sim = 1.0
            with self._stats_lock:
                if self._goal_embedding is not None:
                    goal_sim = self._backend.similarity(scene_emb, self._goal_embedding)
                self._last_goal_similarity = goal_sim

            # Nearest episode retrieval
            nearest = self._find_nearest(scene_emb, self._rag_k)

            should_escalate = goal_sim < self._goal_sim_threshold

            latency_ms = (time.perf_counter() - t_start) * 1000.0

            # Update stats
            with self._stats_lock:
                self._request_count += 1
                self._latency_samples.append(latency_ms)
                if len(self._latency_samples) > 100:
                    self._latency_samples = self._latency_samples[-100:]
                if should_escalate:
                    self._escalation_count += 1

            # Update metrics
            if self._m_latency_hist is not None:
                try:
                    self._m_latency_hist.observe(
                        f"{self._backend.backend_name}:{modality}", latency_ms
                    )
                except Exception:
                    pass
            if self._m_similarity is not None:
                try:
                    self._m_similarity.set(goal_sim, backend=self._backend.backend_name)
                except Exception:
                    pass
            if should_escalate and self._m_escalations is not None:
                try:
                    self._m_escalations.inc(backend=self._backend.backend_name)
                except Exception:
                    pass

            if should_escalate:
                logger.info(
                    "Interpreter: escalating to L2 — goal_similarity=%.3f < threshold=%.3f",
                    goal_sim,
                    self._goal_sim_threshold,
                )

            return SceneContext(
                embedding=scene_emb,
                goal_similarity=goal_sim,
                nearest_episodes=nearest,
                should_escalate=should_escalate,
                tick_id=tick_id,
                backend=self._backend.backend_name,
                latency_ms=round(latency_ms, 2),
            )

        except Exception as exc:
            logger.debug("pre_think error: %s", exc)
            if self._m_errors is not None:
                try:
                    self._m_errors.inc(
                        backend=self._backend.backend_name, error_type="embed_failed"
                    )
                except Exception:
                    pass
            return _null_context(self._backend.backend_name, self._backend.dimensions)

    def post_think(
        self,
        scene_ctx: SceneContext,
        thought: Thought,
        outcome: str = "unknown",
    ) -> None:
        """Persist the episode to the vector store (non-blocking).

        Args:
            scene_ctx: The :class:`SceneContext` from the matching pre_think call.
            thought:   The :class:`~castor.providers.base.Thought` produced by the brain.
            outcome:   Human-readable outcome tag.
        """
        if scene_ctx.is_null:
            return
        t = threading.Thread(
            target=self._store_episode,
            args=(scene_ctx, thought, outcome),
            daemon=True,
        )
        self._last_store_thread = t
        t.start()

    def flush(self) -> None:
        """Block until any in-flight background episode store completes.

        Used in tests to avoid ``time.sleep()`` races.  No-op if no store is pending.
        """
        t = getattr(self, "_last_store_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=5.0)

    def format_rag_context(self, scene_ctx: SceneContext, k: int | None = None) -> str:
        """Format nearest past episodes as a context string for the L2 planner.

        Args:
            scene_ctx: A :class:`SceneContext` with ``nearest_episodes`` populated.
            k:         Max episodes to include; defaults to ``rag_k`` config value.

        Returns:
            A human-readable string describing similar past situations, or ``""``
            when no episodes are available.
        """
        episodes = scene_ctx.nearest_episodes[: k or self._rag_k]
        if not episodes:
            return ""
        lines = ["[Semantic Memory — similar past situations]"]
        for i, ep in enumerate(episodes, 1):
            sim_pct = int(ep.get("similarity", 0) * 100)
            lines.append(
                f'  {i}. ({sim_pct}% match) Instruction: "{ep.get("instruction", "?")}" '
                f"→ Action: {ep.get('action_type', '?')} "
                f"→ Outcome: {ep.get('outcome', '?')}"
            )
        return "\n".join(lines)

    def status(self) -> dict:
        """Return status dict for /api/interpreter/status endpoint.

        Returns:
            Dict with backend name, dimensions, episode count, similarity, etc.
        """
        with self._stats_lock:
            avg_lat = (
                round(sum(self._latency_samples) / len(self._latency_samples), 2)
                if self._latency_samples
                else None
            )
            episode_count = len(self._meta)
            recent = list(self._meta[-10:]) if self._meta else []
            return {
                "enabled": self._enabled,
                "backend": self._backend.backend_name,
                "dimensions": self._backend.dimensions,
                "episode_count": episode_count,
                "last_goal_similarity": self._last_goal_similarity,
                "escalations_session": self._escalation_count,
                "avg_latency_ms": avg_lat,
                "recent_episodes": recent,
            }

    # ── Episode store ─────────────────────────────────────────────────────────

    def _load_episode_store(self) -> None:
        """Load embeddings.npy + meta.json from disk into memory."""
        emb_path = self._store_dir / "embeddings.npy"
        meta_path = self._store_dir / "meta.json"
        try:
            with self._store_lock:
                if emb_path.exists() and meta_path.exists():
                    self._embeddings = np.load(str(emb_path)).astype(np.float32)
                    self._meta = json.loads(meta_path.read_text())
                    logger.debug(
                        "Episode store loaded: %d episodes (dims=%d)",
                        len(self._meta),
                        self._embeddings.shape[1] if self._embeddings.ndim == 2 else 0,
                    )
        except Exception as exc:
            logger.debug("Could not load episode store: %s — starting fresh", exc)
            self._embeddings = None
            self._meta = []

    def _save_episode_store(self) -> None:
        """Persist in-memory store to embeddings.npy + meta.json."""
        emb_path = self._store_dir / "embeddings.npy"
        meta_path = self._store_dir / "meta.json"
        try:
            with self._store_lock:
                if self._embeddings is not None and len(self._meta) > 0:
                    np.save(str(emb_path), self._embeddings)
                    meta_path.write_text(json.dumps(self._meta, indent=2))
        except Exception as exc:
            logger.debug("Could not save episode store: %s", exc)

    def _store_episode(
        self,
        scene_ctx: SceneContext,
        thought: Thought,
        outcome: str,
    ) -> None:
        """Add an episode to the in-memory store and persist to disk."""
        if scene_ctx.is_null:
            return
        meta = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instruction": getattr(thought, "raw_text", "")[:200],
            "action_type": (thought.action or {}).get("type", "unknown")
            if thought.action
            else "none",
            "outcome": outcome,
            "goal_similarity": round(scene_ctx.goal_similarity, 4),
            "tick_id": scene_ctx.tick_id,
            "backend": scene_ctx.backend,
        }
        emb = scene_ctx.embedding.astype(np.float32)
        if emb.shape[0] == 0:
            return

        try:
            with self._store_lock:
                if self._embeddings is None:
                    self._embeddings = emb.reshape(1, -1)
                    self._meta = [meta]
                else:
                    # Dimension mismatch guard
                    if self._embeddings.shape[1] != emb.shape[0]:
                        logger.debug(
                            "Episode dimension mismatch (%d vs %d) — resetting store",
                            self._embeddings.shape[1],
                            emb.shape[0],
                        )
                        self._embeddings = emb.reshape(1, -1)
                        self._meta = [meta]
                    else:
                        self._embeddings = np.vstack([self._embeddings, emb.reshape(1, -1)])
                        self._meta.append(meta)

                # FIFO eviction
                if len(self._meta) > self._max_episodes:
                    excess = len(self._meta) - self._max_episodes
                    self._embeddings = self._embeddings[excess:]
                    self._meta = self._meta[excess:]

            self._save_episode_store()

            if self._m_episodes is not None:
                try:
                    with self._store_lock:
                        cnt = len(self._meta)
                    self._m_episodes.set(cnt, backend=self._backend.backend_name)
                except Exception:
                    pass

            logger.debug("Episode stored (tick=%d outcome=%s)", scene_ctx.tick_id, outcome)
        except Exception as exc:
            logger.warning("Failed to store episode: %s", exc)

    def _find_nearest(self, query: np.ndarray, k: int) -> list[dict]:
        """Cosine similarity search over the episode store.

        Args:
            query: Query embedding vector.
            k:     Maximum number of results to return.

        Returns:
            List of metadata dicts sorted by descending similarity, each with
            an added ``similarity`` key.
        """
        with self._store_lock:
            if self._embeddings is None or len(self._meta) == 0:
                return []
            embeddings = self._embeddings.copy()
            meta = list(self._meta)

        # Dimension mismatch guard
        if embeddings.shape[1] != query.shape[0]:
            return []

        # Vectorised cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms < 1e-9, 1.0, norms)
        normed = embeddings / norms

        q_norm = float(np.linalg.norm(query))
        if q_norm < 1e-9:
            return []
        q = query / q_norm

        sims = normed @ q  # shape (N,)
        top_k_idx = np.argsort(sims)[::-1][:k]
        return [{**meta[i], "similarity": round(float(sims[i]), 4)} for i in top_k_idx]
