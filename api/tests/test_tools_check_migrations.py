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


# ── Dollar-quoted function bodies ────────────────────────────────────────────

def test_dollar_quoted_function_body_does_not_false_split(tmp_path):
    """A `CREATE FUNCTION ... AS $$ ... ; ... $$` body contains semicolons
    that must not count as statement separators."""
    f = _write(tmp_path, "0050_thing.sql", """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS foo ON bar (x);
    """)
    assert check_file(f, strict=False) == []

    f2 = _write(tmp_path, "0051_func.sql", """
        CREATE OR REPLACE FUNCTION audit_log_insert() RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit (id) VALUES (NEW.id);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    # Function body has 2 semicolons inside $$ ... $$, plus the final ;.
    # Without dollar-quote awareness this would look like 3 statements.
    from tools.check_migrations import _split_statements
    assert len(_split_statements(f2.read_text())) == 1


def test_dollar_quoted_concurrently_only_is_ok(tmp_path):
    """The DO $$ ... $$ block is one statement; combined with a single
    CONCURRENTLY DDL is two — flag it. Combined alone, no flag."""
    f = _write(tmp_path, "0052_thing.sql", """
        DO $$ BEGIN
            RAISE NOTICE 'hello; world';
        END $$;
    """)
    assert check_file(f, strict=False) == []


# ── String-literal awareness ─────────────────────────────────────────────────

def test_string_literal_does_not_trip_create_index_check(tmp_path):
    """A string literal mentioning CREATE INDEX must not look like real DDL
    to the linter."""
    f = _write(tmp_path, "0053_thing.sql", """
        INSERT INTO migration_log (note) VALUES ('CREATE INDEX -- not really');
    """)
    # Non-strict: no CREATE INDEX or CONCURRENTLY issue.
    assert check_file(f, strict=False) == []
    # Strict: same — the CREATE INDEX inside the string literal must
    # not trigger the unguarded-DDL warning.
    assert check_file(f, strict=True) == []


def test_string_literal_with_dashes_not_treated_as_comment(tmp_path):
    """`-- ` inside a string literal must survive the comment stripper."""
    f = _write(tmp_path, "0054_thing.sql", """
        INSERT INTO note (text) VALUES ('hyphen -- dash is fine');
        CREATE TABLE IF NOT EXISTS foo (id int);
    """)
    from tools.check_migrations import _split_statements
    # Two statements regardless of the dashes inside the literal.
    assert len(_split_statements(f.read_text())) == 2
