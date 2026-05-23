"""Unit tests for the migration policy linter.

The linter ships as a tool, but the rules it encodes are CLAUDE.md /
MIGRATIONS.md policy. Test them so a regression in the regex doesn't
silently turn the CI gate into a no-op.
"""
from __future__ import annotations

from pathlib import Path

from tools.check_migrations import _CONCURRENTLY_RE, _NAME_RE, check_file


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ── Filename pattern ─────────────────────────────────────────────────────────

def test_filename_pattern_accepts_canonical():
    assert _NAME_RE.match("0042_add_thing.sql")
    assert _NAME_RE.match("0029a_wiki_tables_cleanup.sql")


def test_filename_pattern_rejects_garbage():
    assert not _NAME_RE.match("42_thing.sql")
    assert not _NAME_RE.match("0042-thing.sql")
    assert not _NAME_RE.match("0042_Thing.sql")  # uppercase in slug
    assert not _NAME_RE.match("0042_thing.SQL")


# ── CONCURRENTLY detection ───────────────────────────────────────────────────

def test_concurrently_matches_create_index():
    assert _CONCURRENTLY_RE.search("CREATE INDEX CONCURRENTLY foo ON bar (x);")
    assert _CONCURRENTLY_RE.search(
        "create unique index concurrently if not exists foo on bar(x);"
    )
    assert _CONCURRENTLY_RE.search("REINDEX INDEX CONCURRENTLY foo;")


def test_concurrently_does_not_match_comments(tmp_path):
    """A comment mentioning the word 'CONCURRENTLY' must not trip the
    multi-statement gate (the actual DDL there is plain)."""
    f = _write(tmp_path, "0050_thing.sql", """
        -- We could use CONCURRENTLY here later.
        CREATE INDEX IF NOT EXISTS foo ON bar (x);
        ALTER TABLE bar ADD COLUMN IF NOT EXISTS y int;
    """)
    assert check_file(f, strict=False) == []


# ── Multi-statement CONCURRENTLY violation ───────────────────────────────────

def test_concurrently_with_extra_statement_flagged(tmp_path):
    f = _write(tmp_path, "0050_thing.sql", """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS foo ON bar (x);
        ALTER TABLE bar ADD COLUMN IF NOT EXISTS y int;
    """)
    problems = check_file(f, strict=False)
    assert any("single-statement" in p for p in problems)


def test_concurrently_alone_is_ok(tmp_path):
    f = _write(tmp_path, "0050_thing.sql", """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS foo ON bar (x);
    """)
    assert check_file(f, strict=False) == []


# ── Strict-mode idempotency ──────────────────────────────────────────────────

def test_strict_mode_flags_unguarded_create_table(tmp_path):
    f = _write(tmp_path, "0050_thing.sql", "CREATE TABLE foo (id int);")
    problems = check_file(f, strict=True)
    assert any("CREATE TABLE" in p for p in problems)


def test_strict_mode_accepts_guarded_create(tmp_path):
    f = _write(tmp_path, "0050_thing.sql", """
        CREATE TABLE IF NOT EXISTS foo (id int);
        CREATE INDEX IF NOT EXISTS foo_id_idx ON foo (id);
    """)
    assert check_file(f, strict=True) == []


def test_non_strict_mode_silent_on_historical_pattern(tmp_path):
    """Default (non-strict) mode is the CI gate; it must NOT flag the
    historical-style unguarded CREATE TABLE so prod-applied migrations
    don't gate every PR."""
    f = _write(tmp_path, "0001_history.sql", "CREATE TABLE foo (id int);")
    assert check_file(f, strict=False) == []
