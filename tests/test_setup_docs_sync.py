"""Ensure setup catalog snippets in docs/site stay synchronized."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT_PATH = ROOT / "scripts" / "sync_setup_docs.py"
SPEC = spec_from_file_location("sync_setup_docs", SYNC_SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load sync script: {SYNC_SCRIPT_PATH}")
SYNC_SETUP_DOCS = module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC_SETUP_DOCS)

API_REF_PATH = SYNC_SETUP_DOCS.API_REF_PATH
README_PATH = SYNC_SETUP_DOCS.README_PATH
SITE_DOCS_PATH = SYNC_SETUP_DOCS.SITE_DOCS_PATH
_build_api_ref_block = SYNC_SETUP_DOCS._build_api_ref_block
_build_readme_block = SYNC_SETUP_DOCS._build_readme_block
_build_site_block = SYNC_SETUP_DOCS._build_site_block
_replace_between_markers = SYNC_SETUP_DOCS._replace_between_markers

START = "<!-- SETUP_CATALOG:BEGIN -->"
END = "<!-- SETUP_CATALOG:END -->"


def _assert_synced(path: Path, body: str) -> None:
    original = path.read_text(encoding="utf-8")
    updated = _replace_between_markers(original, START, END, body)
    assert original == updated


def test_readme_setup_catalog_snippet_synced():
    _assert_synced(README_PATH, _build_readme_block())


def test_api_reference_setup_catalog_snippet_synced():
    _assert_synced(API_REF_PATH, _build_api_ref_block())


def test_site_docs_setup_catalog_snippet_synced():
    _assert_synced(SITE_DOCS_PATH, _build_site_block())
