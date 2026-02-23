"""
LLM Response Cache for OpenCastor (#170).

Caches LLM responses in SQLite keyed by SHA-256(instruction + image_hash).
Dramatically reduces API costs when the robot repeatedly encounters similar
scenes or receives identical commands.

Usage::

    from castor.response_cache import get_cache, CachedProvider

    # Wrap any provider with caching
    cache = get_cache()
    cached_brain = CachedProvider(brain, cache)
    thought = cached_brain.think(image_bytes, instruction)

Env:
  CASTOR_CACHE_DB      — SQLite path (default ~/.castor/response_cache.db)
  CASTOR_CACHE_MAX_AGE — max entry age in seconds (default 3600 = 1 hour)
  CASTOR_CACHE_MAX_SIZE — max entries before LRU eviction (default 10000)
  CASTOR_CACHE_ENABLED  — "0" to disable globally (default enabled)

REST API:
  GET  /api/cache/stats   — {hits, misses, entries, hit_rate_pct}
  POST /api/cache/clear   — delete all cached entries
  POST /api/cache/disable — bypass cache for this session
  POST /api/cache/enable  — re-enable cache
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.ResponseCache")

_DB_PATH = os.getenv("CASTOR_CACHE_DB", os.path.expanduser("~/.castor/response_cache.db"))
_MAX_AGE_S = int(os.getenv("CASTOR_CACHE_MAX_AGE", "3600"))
_MAX_SIZE = int(os.getenv("CASTOR_CACHE_MAX_SIZE", "10000"))
_ENABLED = os.getenv("CASTOR_CACHE_ENABLED", "1") != "0"

_singleton: Optional["ResponseCache"] = None
_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS response_cache (
    key         TEXT PRIMARY KEY,
    instruction TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    action_json TEXT,
    created_at  REAL NOT NULL,
    hits        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_created ON response_cache(created_at);
"""


class ResponseCache:
    """SQLite-backed LLM response cache.

    Entries are keyed by SHA-256(instruction + image_hash).
    Expired entries are pruned lazily on each cache miss.
    LRU eviction is applied when the table exceeds max_size.
    """

    def __init__(
        self,
        db_path: str = _DB_PATH,
        max_age_s: int = _MAX_AGE_S,
        max_size: int = _MAX_SIZE,
        enabled: bool = _ENABLED,
    ):
        self._db_path = db_path
        self._max_age_s = max_age_s
        self._max_size = max_size
        self._enabled = enabled
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._init_db()
        logger.info(
            "ResponseCache ready (db=%s, max_age=%ds, max_size=%d, enabled=%s)",
            db_path,
            max_age_s,
            max_size,
            enabled,
        )

    # ── DB helpers ────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    # ── Key generation ────────────────────────────────────────────────────

    @staticmethod
    def make_key(instruction: str, image_bytes: Optional[bytes] = None) -> str:
        """Return a SHA-256 cache key for an (instruction, image) pair."""
        h = hashlib.sha256()
        h.update(instruction.encode("utf-8", errors="replace"))
        if image_bytes:
            # Use a hash of the image, not the raw bytes
            img_hash = hashlib.md5(image_bytes).hexdigest()
            h.update(img_hash.encode())
        return h.hexdigest()

    # ── Public interface ──────────────────────────────────────────────────

    def get(self, instruction: str, image_bytes: Optional[bytes] = None) -> Optional[dict]:
        """Look up a cached response. Returns None on miss.

        Returns:
            {raw_text, action} or None.
        """
        if not self._enabled:
            return None

        key = self.make_key(instruction, image_bytes)
        now = time.time()

        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT raw_text, action_json, created_at FROM response_cache WHERE key = ?",
                    (key,),
                ).fetchone()

                if row is None:
                    self._misses += 1
                    # Prune expired entries lazily
                    self._prune(conn, now)
                    return None

                age = now - row["created_at"]
                if age > self._max_age_s:
                    conn.execute("DELETE FROM response_cache WHERE key = ?", (key,))
                    self._misses += 1
                    return None

                # Update hit count
                conn.execute(
                    "UPDATE response_cache SET hits = hits + 1 WHERE key = ?", (key,)
                )
                self._hits += 1

        action = None
        if row["action_json"]:
            try:
                action = json.loads(row["action_json"])
            except json.JSONDecodeError:
                pass
        return {"raw_text": row["raw_text"], "action": action}

    def put(
        self,
        instruction: str,
        raw_text: str,
        action: Optional[dict],
        image_bytes: Optional[bytes] = None,
    ) -> None:
        """Store a response in the cache."""
        if not self._enabled:
            return

        key = self.make_key(instruction, image_bytes)
        action_json = json.dumps(action) if action is not None else None
        now = time.time()

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO response_cache(key, instruction, raw_text, action_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        raw_text=excluded.raw_text,
                        action_json=excluded.action_json,
                        created_at=excluded.created_at,
                        hits=0
                    """,
                    (key, instruction[:500], raw_text, action_json, now),
                )
                # LRU eviction
                count = conn.execute(
                    "SELECT COUNT(*) FROM response_cache"
                ).fetchone()[0]
                if count > self._max_size:
                    to_delete = count - self._max_size
                    conn.execute(
                        """
                        DELETE FROM response_cache WHERE key IN (
                            SELECT key FROM response_cache
                            ORDER BY created_at ASC
                            LIMIT ?
                        )
                        """,
                        (to_delete,),
                    )

    def clear(self) -> int:
        """Delete all cached entries. Returns count deleted."""
        with self._lock:
            with self._connect() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM response_cache"
                ).fetchone()[0]
                conn.execute("DELETE FROM response_cache")
        self._hits = 0
        self._misses = 0
        logger.info("ResponseCache cleared (%d entries deleted)", count)
        return count

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), SUM(hits) FROM response_cache"
            ).fetchone()
            entries = row[0] or 0
            total_hits_stored = row[1] or 0

        total = self._hits + self._misses
        hit_rate = round(self._hits / max(total, 1) * 100, 1)
        return {
            "entries": entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": hit_rate,
            "total_hits_stored": total_hits_stored,
            "enabled": self._enabled,
            "max_age_s": self._max_age_s,
            "max_size": self._max_size,
        }

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    # ── Private ───────────────────────────────────────────────────────────

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        """Delete expired entries (called inside lock)."""
        cutoff = now - self._max_age_s
        conn.execute(
            "DELETE FROM response_cache WHERE created_at < ?", (cutoff,)
        )


class CachedProvider:
    """Transparent cache wrapper around any BaseProvider.

    Delegates to the underlying provider on cache miss; stores the result.
    On cache hit, returns a Thought reconstructed from the stored data.

    Usage::

        from castor.response_cache import get_cache, CachedProvider
        cached = CachedProvider(brain, get_cache())
        thought = cached.think(image_bytes, instruction)
    """

    def __init__(self, provider, cache: Optional[ResponseCache] = None):
        self._provider = provider
        self._cache = cache or get_cache()

    def think(self, image_bytes: bytes, instruction: str, surface: str = "whatsapp"):
        from castor.providers.base import Thought

        hit = self._cache.get(instruction, image_bytes)
        if hit is not None:
            logger.debug("Cache HIT for instruction: %.60s…", instruction)
            return Thought(raw_text=hit["raw_text"], action=hit["action"])

        thought = self._provider.think(image_bytes, instruction, surface)
        self._cache.put(instruction, thought.raw_text, thought.action, image_bytes)
        return thought

    def think_stream(self, image_bytes: bytes, instruction: str, surface: str = "whatsapp"):
        """Streaming: check cache first; if hit, yield full text; else stream + cache result."""
        hit = self._cache.get(instruction, image_bytes)
        if hit is not None:
            logger.debug("Cache HIT (stream) for instruction: %.60s…", instruction)
            yield hit["raw_text"]
            return

        chunks = []
        for chunk in self._provider.think_stream(image_bytes, instruction, surface):
            chunks.append(chunk)
            yield chunk

        full_text = "".join(chunks)
        from castor.providers.base import Thought

        thought = Thought(raw_text=full_text, action=self._provider._clean_json(full_text)
                          if hasattr(self._provider, "_clean_json") else None)
        self._cache.put(instruction, full_text, thought.action, image_bytes)

    def health_check(self) -> dict:
        result = self._provider.health_check()
        result["cache_enabled"] = self._cache.enabled
        result["cache_hit_rate_pct"] = self._cache.stats()["hit_rate_pct"]
        return result

    def __getattr__(self, name: str):
        return getattr(self._provider, name)


def get_cache() -> ResponseCache:
    """Return the process-wide ResponseCache singleton."""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = ResponseCache()
    return _singleton
