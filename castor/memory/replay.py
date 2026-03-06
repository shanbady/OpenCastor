"""
Episode replay for OpenCastor memory system (issue #443).

Replays historical episodes through the current consolidation pipeline
to backfill knowledge missed before pipeline improvements.

Usage:
    castor memory replay --since 2026-01-01 --dry-run
    castor memory replay --since 2026-01-01
    castor memory replay --episode-id abc123 --dry-run
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReplayStats:
    episodes_found: int = 0
    episodes_skipped: int = 0
    episodes_replayed: int = 0
    insights_promoted: int = 0
    insights_merged: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Episodes found:    {self.episodes_found}",
            f"  Already indexed: {self.episodes_skipped}",
            f"  Replayed:        {self.episodes_replayed}",
            f"Insights promoted: {self.insights_promoted}",
            f"Insights merged:   {self.insights_merged}",
            f"Errors:            {len(self.errors)}",
            f"Time:              {self.elapsed_s:.1f}s",
        ]
        if self.errors:
            lines.append("\nErrors:")
            for e in self.errors[:5]:
                lines.append(f"  • {e}")
        return "\n".join(lines)


def _load_episode(path: Path) -> dict | None:
    """Load a single episode JSON file. Returns None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.debug("Failed to load episode %s: %s", path, e)
        return None


def _parse_episode_timestamp(ep: dict, path: Path) -> datetime | None:
    """Extract a datetime from an episode dict or filename."""
    # Try episode fields
    for field_name in ("timestamp", "created_at", "episode_at", "start_time"):
        val = ep.get(field_name)
        if val:
            try:
                if isinstance(val, (int, float)):
                    return datetime.fromtimestamp(val, tz=timezone.utc)
                ts = str(val)
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d",
                ):
                    try:
                        return datetime.strptime(ts[:19], fmt[: len(fmt)])
                    except ValueError:
                        pass
            except Exception:
                pass
    # Fall back to file mtime
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return None


def _get_indexed_ids(semantic_dir: Path) -> set[str]:
    """Load already-indexed episode IDs from L1-semantic layer."""
    indexed: set[str] = set()
    if not semantic_dir.exists():
        return indexed
    for path in semantic_dir.rglob("*.json"):
        try:
            data = json.loads(path.read_text())
            # Episode IDs may be in source_episode_ids, episode_id, or episodes list
            if isinstance(data, dict):
                if "source_episode_ids" in data:
                    indexed.update(data["source_episode_ids"])
                if "episode_id" in data:
                    indexed.add(data["episode_id"])
                if "episodes" in data and isinstance(data["episodes"], list):
                    indexed.update(str(e) for e in data["episodes"])
        except Exception:
            pass
    return indexed


async def _replay_episode(ep: dict, consolidation_fn: Any, dry_run: bool) -> dict:
    """
    Run a single episode through the consolidation pipeline.

    Returns {"promoted": int, "merged": int, "error": str|None}
    """
    result = {"promoted": 0, "merged": 0, "error": None}
    if dry_run:
        result["promoted"] = 1  # simulate
        return result

    try:
        if asyncio.iscoroutinefunction(consolidation_fn):
            outcome = await consolidation_fn(ep)
        else:
            outcome = consolidation_fn(ep)

        if isinstance(outcome, dict):
            result["promoted"] = outcome.get("promoted", 0)
            result["merged"] = outcome.get("merged", 0)
    except Exception as e:
        result["error"] = str(e)

    return result


async def replay_episodes(
    episodes_dir: str | Path | None = None,
    semantic_dir: str | Path | None = None,
    since: str | None = None,
    episode_id: str | None = None,
    dry_run: bool = False,
    consolidation_fn: Any = None,
    verbose: bool = False,
) -> ReplayStats:
    """
    Replay historical episodes through the current consolidation pipeline.

    Args:
        episodes_dir:     Path to L0-episodic/episodes/ (auto-detected if None)
        semantic_dir:     Path to L1-semantic/ (auto-detected if None)
        since:            ISO date string — only replay episodes on/after this date
        episode_id:       Replay a single specific episode by ID
        dry_run:          Simulate without writing anything
        consolidation_fn: async or sync callable(episode_dict) → {"promoted": N, "merged": N}
                          If None, attempts to import from continuonos brain-a.
        verbose:          Print each episode being replayed
    """
    stats = ReplayStats()
    t0 = time.perf_counter()

    # Auto-detect paths
    if episodes_dir is None:
        candidates = [
            Path.home() / "continuonos/brain-a/data/L0-episodic/episodes",
            Path.cwd() / "data/L0-episodic/episodes",
            Path("/data/L0-episodic/episodes"),
        ]
        for c in candidates:
            if c.exists():
                episodes_dir = c
                break
        else:
            stats.errors.append("Could not find episodes directory — pass --episodes-dir")
            stats.elapsed_s = time.perf_counter() - t0
            return stats

    if semantic_dir is None:
        base = Path(episodes_dir).parent.parent
        semantic_dir = base / "L1-semantic"

    episodes_path = Path(episodes_dir)
    semantic_path = Path(semantic_dir)

    # Parse since filter
    since_dt: datetime | None = None
    if since:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                since_dt = datetime.strptime(since, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                pass
        if since_dt is None:
            stats.errors.append(f"Could not parse --since date: {since}")
            stats.elapsed_s = time.perf_counter() - t0
            return stats

    # Load all episode files
    episode_files = sorted(episodes_path.rglob("*.json"))
    if not episode_files:
        logger.info("No episodes found in %s", episodes_path)
        stats.elapsed_s = time.perf_counter() - t0
        return stats

    # Load already-indexed IDs to skip
    indexed_ids = _get_indexed_ids(semantic_path)

    # Load consolidation function if not provided
    if consolidation_fn is None and not dry_run:
        try:
            from brain_a.memory.consolidation import consolidate_episode  # type: ignore

            consolidation_fn = consolidate_episode
        except ImportError:
            try:

                def consolidation_fn(ep):
                    return {"promoted": 0, "merged": 0}
            except Exception:

                def consolidation_fn(ep):
                    return {"promoted": 0, "merged": 0}

    # Process episodes
    for ep_path in episode_files:
        ep = _load_episode(ep_path)
        if ep is None:
            continue

        ep_id = ep.get("episode_id") or ep.get("id") or ep_path.stem
        stats.episodes_found += 1

        # Filter by episode_id
        if episode_id and str(ep_id) != str(episode_id):
            continue

        # Filter by since date
        if since_dt:
            ep_ts = _parse_episode_timestamp(ep, ep_path)
            if ep_ts and ep_ts.replace(tzinfo=timezone.utc) < since_dt:
                continue

        # Skip already indexed
        if str(ep_id) in indexed_ids:
            stats.episodes_skipped += 1
            if verbose:
                logger.info("  skip %s (already indexed)", ep_id)
            continue

        if verbose:
            logger.info("  replay %s ...", ep_id)

        result = await _replay_episode(ep, consolidation_fn, dry_run)

        if result["error"]:
            stats.errors.append(f"{ep_id}: {result['error']}")
        else:
            stats.episodes_replayed += 1
            stats.insights_promoted += result["promoted"]
            stats.insights_merged += result["merged"]

    stats.elapsed_s = time.perf_counter() - t0
    return stats


def run_replay_cli(args: Any) -> None:
    """Entry point from castor memory replay subcommand."""
    try:
        from rich.console import Console

        con = Console()
        HAS_RICH = True
    except ImportError:
        con = None
        HAS_RICH = False

    def pr(text, **kw):
        if HAS_RICH and con:
            con.print(text, **kw)
        else:
            import re

            print(re.sub(r"\[/?[a-z_ ]+\]", "", text))

    dry_run = getattr(args, "dry_run", False)
    since = getattr(args, "since", None)
    ep_id = getattr(args, "episode_id", None)
    episodes_dir = getattr(args, "episodes_dir", None)
    verbose = getattr(args, "verbose", False)

    if dry_run:
        pr("\n[yellow]DRY RUN — no changes will be written[/yellow]")

    pr(
        "\n🔄 [bold]castor memory replay[/bold]"
        + (f"  since {since}" if since else "")
        + (f"  episode {ep_id}" if ep_id else "")
        + ("\n")
    )

    stats = asyncio.run(
        replay_episodes(
            episodes_dir=episodes_dir,
            since=since,
            episode_id=ep_id,
            dry_run=dry_run,
            verbose=verbose,
        )
    )

    pr("\n[bold]Results:[/bold]")
    for line in stats.summary().splitlines():
        pr(f"  {line}")
    pr("")

    if stats.errors:
        pr(f"[yellow]⚠️  {len(stats.errors)} error(s) — run with --verbose for details[/yellow]")

    if dry_run and stats.episodes_replayed > 0:
        pr(f"\n[dim]Run without --dry-run to apply {stats.episodes_replayed} replay(s)[/dim]")
