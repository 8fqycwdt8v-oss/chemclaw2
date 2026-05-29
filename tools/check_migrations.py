"""Migration policy linter.

Enforces the rules documented in migrations/MIGRATIONS.md:

  1. Numbering: every file matches `NNNN[a-z]?_<slug>.sql`. No duplicate
     numeric prefixes (lexicographic suffix `a`/`b`/… is allowed and
     orders correctly after the unsuffixed slot).
  2. CONCURRENTLY isolation: files that contain `CREATE INDEX CONCURRENTLY`
     or `REINDEX ... CONCURRENTLY` must be single-statement so the CI
     apply step's autocommit-mode `ON_ERROR_STOP` keeps atomicity.
  3. DDL idempotency (strict mode only — opt-in via `--strict`): every
     CREATE / ALTER ADD COLUMN / DROP should be guarded by IF [NOT] EXISTS.
     Run before adding a new migration; off by default in CI because the
     historical 0001–0035 set predates the policy and rewriting them
     retroactively has no effect (already applied in prod).

Run: `python -m tools.check_migrations` (exits non-zero on violation).
CI invokes it before applying migrations so a malformed file fails the
build before psql sees it.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# NNNN with an optional single-letter suffix, then _slug.sql.
_NAME_RE = re.compile(r"^(\d{4})([a-z]?)_[a-z0-9_]+\.sql$")

# CREATE INDEX CONCURRENTLY ... — case-insensitive, optional schema/IF NOT EXISTS.
_CONCURRENTLY_RE = re.compile(
    r"\b(?:CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY|REINDEX\b[^;]*\bCONCURRENTLY)",
    re.IGNORECASE,
)

# DDL forms that should be guarded by IF [NOT] EXISTS.
_IDEMPOTENT_PATTERNS = [
    # (regex, error message)
    (
        re.compile(r"\bCREATE\s+TABLE\b(?!\s+IF\s+NOT\s+EXISTS)", re.IGNORECASE),
        "CREATE TABLE without IF NOT EXISTS",
    ),
    (
        re.compile(
            # CREATE INDEX — but exempt the CONCURRENTLY variants (caught by
            # the dedicated check above) and CREATE UNIQUE INDEX which is
            # often a constraint surrogate that callers want to fail loudly.
            r"\bCREATE\s+(?!UNIQUE\s+)INDEX(?!\s+(?:CONCURRENTLY\s+)?IF\s+NOT\s+EXISTS)",
            re.IGNORECASE,
        ),
        "CREATE INDEX without IF NOT EXISTS",
    ),
    (
        re.compile(
            r"\bALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\b(?!\s+IF\s+NOT\s+EXISTS)",
            re.IGNORECASE,
        ),
        "ADD COLUMN without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bDROP\s+TABLE\b(?!\s+IF\s+EXISTS)", re.IGNORECASE),
        "DROP TABLE without IF EXISTS",
    ),
]


_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z0-9_]*)\$")


def _scrub_sql(sql: str) -> str:
    """Strip comments + string-literal + dollar-quoted bodies so downstream
    regexes (CREATE INDEX detection, statement splitting) don't false-match
    on text inside `'...'` literals, `$$...$$` function bodies, or
    `-- comments`.

    The replacement preserves structure (whitespace + final `;`) so the
    output retains the original statement count. Inside literal/comment
    spans, every character becomes a space — the regexes are word-boundary
    sensitive but length-agnostic, so this is safe.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # /* ... */ block comment.
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        # -- line comment.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        # Single-quoted string literal. Postgres escapes `''` inside.
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2  # escaped quote
                        continue
                    j += 1
                    break
                j += 1
            out.append(" " * (j - i))
            i = j
            continue
        # Dollar-quoted body: $tag$ ... $tag$ (tag may be empty: $$).
        if ch == "$":
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                tag = m.group(0)
                end = sql.find(tag, m.end())
                end = n if end == -1 else end + len(tag)
                out.append(" " * (end - i))
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_comments(sql: str) -> str:
    """Back-compat shim — `_scrub_sql` is the real implementation."""
    return _scrub_sql(sql)


def _split_statements(sql: str) -> list[str]:
    """Split on top-level `;`. Safe across dollar-quoted function bodies
    and string literals because `_scrub_sql` blanks them out first."""
    scrubbed = _scrub_sql(sql)
    return [stmt.strip() for stmt in scrubbed.split(";") if stmt.strip()]


def check_file(path: Path, *, strict: bool) -> list[str]:
    """Return a list of human-readable violations for `path`. Empty = clean."""
    problems: list[str] = []

    m = _NAME_RE.match(path.name)
    if not m:
        problems.append(
            f"filename {path.name!r} does not match NNNN[a-z]?_<snake_case_slug>.sql",
        )

    sql = path.read_text(encoding="utf-8")
    stripped = _strip_comments(sql)

    has_concurrently = bool(_CONCURRENTLY_RE.search(stripped))
    if has_concurrently:
        statements = _split_statements(sql)
        if len(statements) > 1:
            problems.append(
                "contains CONCURRENTLY DDL but has multiple statements — must be "
                "single-statement so CI's autocommit `ON_ERROR_STOP` is atomic "
                f"(found {len(statements)} statements)",
            )

    if strict:
        for pattern, msg in _IDEMPOTENT_PATTERNS:
            if pattern.search(stripped):
                problems.append(f"{msg} — add the guard so partial-failure retry is safe")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint migrations/ against MIGRATIONS.md policy")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also enforce IF [NOT] EXISTS idempotency guards "
             "(off by default — old migrations predate the policy)",
    )
    args = parser.parse_args()

    if not MIGRATIONS_DIR.is_dir():
        print(f"error: migrations directory not found at {MIGRATIONS_DIR}", file=sys.stderr)
        return 2

    files = sorted(p for p in MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")
    if not files:
        print("warning: no .sql files found in migrations/", file=sys.stderr)
        return 0

    # Duplicate-numeric-prefix detection: 0029_a.sql + 0029_b.sql is OK
    # (suffix disambiguates lexically); 0029_foo.sql + 0029_bar.sql is not.
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for path in files:
        m = _NAME_RE.match(path.name)
        if m:
            prefix, suffix = m.group(1), m.group(2)
            by_prefix[f"{prefix}{suffix or '-unsuffixed'}"].append(path.name)

    duplicate_problems: list[str] = []
    for slot, names in by_prefix.items():
        if len(names) > 1:
            duplicate_problems.append(f"slot {slot}: {', '.join(names)}")

    file_problems: list[tuple[str, list[str]]] = []
    for path in files:
        violations = check_file(path, strict=args.strict)
        if violations:
            file_problems.append((path.name, violations))

    if not file_problems and not duplicate_problems:
        print(f"OK: {len(files)} migrations conform to policy")
        return 0

    if duplicate_problems:
        print("duplicate numeric prefixes:", file=sys.stderr)
        for entry in duplicate_problems:
            print(f"  {entry}", file=sys.stderr)
        print(file=sys.stderr)

    for name, violations in file_problems:
        print(f"{name}:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
