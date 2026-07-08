import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context

load_dotenv(Path(__file__).parent / ".env")


_DB_PASSWORD = os.getenv("DB_PASSWORD", "")
if not _DB_PASSWORD:
    raise RuntimeError(
        "DB_PASSWORD env var is required. Refusing to start with blank password."
    )

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "db": os.getenv("DB_NAME", "PQREACT"),
    "user": os.getenv("DB_USER", "pqreact"),
    "password": _DB_PASSWORD,
    "cursorclass": pymysql.cursors.Cursor,
}


def _connect():
    """Open and return a new PyMySQL connection."""
    return pymysql.connect(**DB_CONFIG)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Verify DB connectivity on startup."""
    try:
        conn = _connect()
        conn.close()
        yield {"db_ok": True}
    except pymysql.Error as e:
        yield {"db_ok": False, "db_error": str(e)}


mcp = FastMCP("PQREACT Database", lifespan=lifespan, host="0.0.0.0", port=9000)



def _rows_to_table(columns: list[str], rows: list[tuple]) -> str:
    """Format query results as a plain-text aligned table."""
    if not rows:
        return "No results found."

    str_rows = [columns] + [
        [str(v) if v is not None else "NULL" for v in row] for row in rows
    ]
    col_widths = [max(len(r[i]) for r in str_rows) for i in range(len(columns))]

    def fmt_row(r):
        return " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(r))

    separator = "-+-".join("-" * w for w in col_widths)
    lines = [fmt_row(columns), separator] + [fmt_row(r) for r in str_rows[1:]]
    return "\n".join(lines)


# ── Measurement methodology legend ───────────────────────────────────────
# `performance_test.duration` is an OVERLOADED column: it stores a different
# physical quantity depending on `source`. Without an explanation, users see
# the same algorithm reported as 0.07 s (CAM TLS handshake) and 9.6 s (QUJATA
# iperf wall-time) and conclude the data is broken. Every tool that reports
# PQC metrics appends this legend so the provenance is always visible.
_SOURCE_METHODOLOGY = {
    "upstream":
        "Telefónica reference benchmark (QUJATA upstream dataset). `duration` "
        "is the per-run handshake/operation time from the published reference "
        "measurements; `power_watts`/`energy_joules` are instrumented here.",
    "cam-context-agility":
        "CAM demo panel. `duration` is the **TLS 1.3 handshake time** (seconds) "
        "measured by `curl` over OQS-OpenSSL against an nginx server, median of "
        "an iteration sweep. Typically 0.02–0.15 s. Energy/power are not "
        "instrumented (NULL).",
    "campaign-b-qujata-live":
        "QUJATA live run. `duration` is the **iperf3 transfer wall-time** "
        "(seconds) over a slice-shaped PQ-IPsec tunnel — NOT the handshake. "
        "Typically 10–120 s. For the per-handshake KEM time use the QUJATA "
        "test-suite tools (`status_message.kem_time`).",
    "campaign-b-qujata":
        "QUJATA (earlier run) — same iperf3 transfer wall-time semantics as "
        "`campaign-b-qujata-live`.",
    "campaign-a-kpi":
        "Campaign-A UC3 KPI. `duration` is the **IKEv2 phase processing "
        "latency** captured by `pqc_Mon.py` from live StrongSwan traffic "
        "(per-exchange, not a full session).",
    "campaign-b-demo4":
        "Demo 4 KEM matrix. `duration` is a one-shot Kyber KEM client/server "
        "exchange time.",
    "campaign-b-demo5":
        "Demo 5 crypto bench. `duration` is a one-shot KEM/signature operation "
        "time.",
    "campaign-b-demo5-bench":
        "Demo 5 benchmark sweep. `duration` is the averaged KEM/signature "
        "operation time over the bench loop.",
}

_METHODOLOGY_HEADER = (
    "ℹ️ **How these were measured** — `duration` stores a different quantity "
    "per `source`, so values are only comparable **within the same source**:"
)


def _methodology_note(sources=None) -> str:
    """Markdown legend explaining how each source's metrics were obtained.

    `sources`: iterable of source strings to explain. If None/empty, the
    full legend is returned (used by aggregate tools that mix sources)."""
    keys = [s for s in (sources or []) if s in _SOURCE_METHODOLOGY]
    if not keys:
        keys = list(_SOURCE_METHODOLOGY)
    lines = ["\n---", _METHODOLOGY_HEADER]
    for k in keys:
        lines.append(f"- `{k}` — {_SOURCE_METHODOLOGY[k]}")
    lines.append(
        "Comparing raw `duration` across different sources is not "
        "apples-to-apples."
    )
    return "\n".join(lines)


def _methodology_for_rows(columns: list[str], rows: list[tuple]) -> str:
    """Detect a `source`/`sources` column in a result and return the legend
    for just the sources present; fall back to the full legend if the result
    does not expose its source (e.g. cross-source aggregates)."""
    idx = next((i for i, c in enumerate(columns)
                if str(c).lower() in ("source", "sources")), None)
    found = set()
    if idx is not None:
        for r in rows:
            val = r[idx]
            if val:
                found.update(s.strip() for s in str(val).split(","))
    return _methodology_note(sorted(found) if found else None)


# Short per-source labels used as sub-table headers when results are split
# by source (so CAM and QUJATA measurements never share a table).
_SOURCE_SHORT = {
    "cam-context-agility":    "CAM — TLS 1.3 handshake time (seconds)",
    "campaign-a-kpi":         "Campaign-A UC3 — IKEv2 phase latency",
    "campaign-b-demo4":       "Demo 4 — one-shot Kyber KEM exchange (seconds)",
    "campaign-b-demo5":       "Demo 5 — one-shot KEM/signature op (seconds)",
    "campaign-b-demo5-bench": "Demo 5 bench — averaged KEM/signature op (seconds)",
    "campaign-b-qujata":      "QUJATA (earlier) — iperf3 transfer wall-time (seconds)",
    "campaign-b-qujata-live": "QUJATA — iperf3 transfer wall-time (seconds)",
    "upstream":               "Telefónica reference benchmark",
}

# Stable display order: live testbed sources first (CAM, QUJATA), then the
# demo/bench sources, then the upstream reference.
_SOURCE_ORDER = [
    "cam-context-agility", "campaign-b-qujata-live", "campaign-b-qujata",
    "campaign-a-kpi", "campaign-b-demo4", "campaign-b-demo5",
    "campaign-b-demo5-bench", "upstream",
]


def _render_by_source(columns: list[str], rows: list[tuple],
                      per_source_limit: int | None = None) -> str:
    """Render one sub-table per `source` so different measurement methods are
    never interleaved in a single table. Rows are assumed pre-sorted within
    each source (callers ORDER BY the relevant metric). Falls back to a single
    table when there is no `source` column."""
    idx = next((i for i, c in enumerate(columns)
                if str(c).lower() == "source"), None)
    if idx is None or not rows:
        return _rows_to_table(columns, rows)
    keep = [i for i in range(len(columns)) if i != idx]
    sub_cols = [columns[i] for i in keep]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r[idx], []).append([r[i] for i in keep])
    ordered = ([s for s in _SOURCE_ORDER if s in groups]
               + [s for s in sorted(groups) if s not in _SOURCE_ORDER])
    parts = []
    for src in ordered:
        g = groups[src]
        shown = g[:per_source_limit] if per_source_limit else g
        label = _SOURCE_SHORT.get(src, src)
        parts.append(
            f"#### `{src}` — {label}  ({len(g)} row(s))\n\n"
            + _rows_to_table(sub_cols, shown)
        )
    return "\n\n".join(parts)


def _check_ctx(ctx: Context) -> str | None:
    """Return an error string if the DB is not available, else None."""
    state = ctx.request_context.lifespan_context
    if not state.get("db_ok", True):
        return f"Database unavailable: {state.get('db_error', 'unknown error')}"
    return None


@mcp.tool()
def query_by_algorithm(algorithm_name: str, ctx: Context) -> str:
    """[DB] Return all rows for a specific algorithm (case-insensitive).

    Args:
        algorithm_name: e.g. 'kyber512', 'bikel1', 'hqc128'
    """
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, duration, power_watts, "
                "message_size, energy_joules, security_level, source "
                "FROM performance_test "
                "WHERE LOWER(algorithm_name) = LOWER(%s) "
                "ORDER BY source, message_size",
                (algorithm_name,),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return f"No results found for algorithm '{algorithm_name}'."
        return (
            f"Results for '{algorithm_name}' — {len(rows)} row(s), "
            "grouped by measurement source\n\n"
            + _render_by_source(columns, rows)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def query_by_message_size(size: int, ctx: Context) -> str:
    """[DB] Return all rows for a specific message size.

    Args:
        size: The message size in bytes (integer)
    """
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, duration, "
                "power_watts, message_size, source "
                "FROM performance_test "
                "WHERE message_size = %s "
                "ORDER BY source, algorithm_name",
                (size,),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return f"No results found for message size {size}."
        return (
            f"Results for message size {size} — {len(rows)} row(s), "
            "grouped by measurement source\n\n"
            + _render_by_source(columns, rows)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def get_fastest_algorithms(limit: int = 10, ctx: Context = None) -> str:
    """[DB] Return the top N algorithms sorted by average duration (ascending).

    Args:
        limit: Number of algorithms to return (default 10)
    """
    if ctx and (err := _check_ctx(ctx)):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, source, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "GROUP BY algorithm_name, source "
                "HAVING avg_duration IS NOT NULL "
                "ORDER BY avg_duration ASC"
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        return (
            f"Fastest algorithms by average duration — top {limit} within each "
            "measurement source (sources are kept separate, not blended)\n\n"
            + _render_by_source(columns, rows, per_source_limit=limit)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def compare_algorithms(algo1: str, algo2: str, ctx: Context) -> str:
    """[DB] Side-by-side comparison of two algorithms by their average metrics.

    Args:
        algo1: First algorithm name (e.g. 'kyber512')
        algo2: Second algorithm name (e.g. 'bikel1')
    """
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, source, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "WHERE LOWER(algorithm_name) IN (LOWER(%s), LOWER(%s)) "
                "GROUP BY algorithm_name, source "
                "ORDER BY algorithm_name",
                (algo1, algo2),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return f"No data found for '{algo1}' or '{algo2}'."
        found = {r[0].lower() for r in rows}
        missing = [a for a in [algo1, algo2] if a.lower() not in found]
        result = (
            f"Comparison: {algo1} vs {algo2} — compare only within the same "
            "source block (durations are not comparable across sources)\n\n"
            + _render_by_source(columns, rows)
        )
        if missing:
            result += f"\n\nNote: No data found for: {', '.join(missing)}"
        result += "\n" + _methodology_for_rows(columns, rows)
        return result
    except pymysql.Error as e:
        return f"Database error: {e}"

@mcp.tool()
def query_by_security_level(level: int, ctx: Context) -> str:
    """[DB] Return all algorithms for a given NIST PQC security level.

    Args:
        level: Security level — 1 (AES-128 equivalent), 3 (AES-192), or 5 (AES-256)
    """
    if level not in (1, 3, 5):
        return "Error: security_level must be 1, 3, or 5."
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, security_level, source, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "WHERE security_level = %s "
                "GROUP BY algorithm_name, security_level, source "
                "ORDER BY avg_duration ASC",
                (level,),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return f"No results found for security level {level}."
        return (
            f"Algorithms at security level {level}, grouped by measurement "
            "source\n\n"
            + _render_by_source(columns, rows)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def execute_query(sql: str, ctx: Context) -> str:
    """[DB] Execute a custom SELECT query against the PQREACT database (read-only).

    Args:
        sql: A valid SQL SELECT statement
    """
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT"):
        return "Error: Only SELECT queries are allowed."
    inner = sql.strip().rstrip(";")
    if ";" in inner:
        return "Error: Multiple statements are not allowed."
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                conn.close()
                return "Query executed but returned no columns."
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        out = f"{len(rows)} row(s)\n\n" + _rows_to_table(columns, rows)
        # Append the measurement legend whenever the query reads
        # performance_test (or its overloaded `duration` column), so raw-SQL
        # results carry the same provenance as the typed tools.
        sql_l = sql.lower()
        if "performance_test" in sql_l or "duration" in sql_l:
            out += "\n" + _methodology_for_rows(columns, rows)
        return out
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def get_lowest_power_for_size(size: int, ctx: Context) -> str:
    """[DB] Return algorithms ranked by lowest average CPU power for a specific message size.
    """
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, "
                "source, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "ROUND(AVG(cpu_util_pct), 4) AS avg_cpu_util_pct, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "WHERE message_size = %s "
                "GROUP BY algorithm_name, source "
                "ORDER BY avg_power_watts ASC",
                (size,),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return (
                f"No data found for message size {size}. "
                "Use query_available_sizes to see what sizes are available."
            )
        return (
            f"Algorithms ranked by lowest CPU power for message size {size}, "
            "grouped by measurement source\n\n"
            + _render_by_source(columns, rows)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def get_lowest_energy_algorithms(limit: int = 10, ctx: Context = None) -> str:
    """[DB] Return the top N algorithms ranked by lowest average energy
    (power_watts × duration), aggregated across all message sizes."""
    if ctx and (err := _check_ctx(ctx)):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, "
                "source, "
                "ROUND(AVG(power_watts * duration), 6) AS avg_energy_joules, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "GROUP BY algorithm_name, source "
                "ORDER BY avg_energy_joules ASC"
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        return (
            f"Lowest energy consumption (cpu_power × duration) — top {limit} "
            "within each measurement source\n\n"
            + _render_by_source(columns, rows, per_source_limit=limit)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def get_lowest_energy_for_size(size: int, ctx: Context) -> str:
    """[DB] Rank algorithms by lowest average energy (power_watts × duration)
    at a specific message size. Combines the size filter with the energy
    metric so callers don't have to chain query_by_message_size +
    get_lowest_energy_algorithms."""
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT algorithm_name, "
                "source, "
                "ROUND(AVG(power_watts * duration), 6) AS avg_energy_joules, "
                "ROUND(AVG(power_watts), 4) AS avg_power_watts, "
                "ROUND(AVG(duration), 4) AS avg_duration, "
                "COUNT(*) AS sample_count "
                "FROM performance_test "
                "WHERE message_size = %s "
                "GROUP BY algorithm_name, source "
                "ORDER BY avg_energy_joules ASC",
                (size,),
            )
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        if not rows:
            return (
                f"No data found for message size {size}. "
                "Use query_available_sizes to see what sizes are available."
            )
        return (
            f"Algorithms ranked by lowest energy consumption for message size "
            f"{size}, grouped by measurement source\n\n"
            + _render_by_source(columns, rows)
            + "\n" + _methodology_for_rows(columns, rows)
        )
    except pymysql.Error as e:
        return f"Database error: {e}"


@mcp.tool()
def query_available_sizes(ctx: Context) -> str:
    """[DB] List the distinct message sizes that have benchmark rows in
    the performance_test table. Useful for introspection — call this
    before query_by_message_size to discover what's available."""
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT message_size FROM performance_test ORDER BY message_size"
            )
            rows = cur.fetchall()
        conn.close()
        sizes = [str(r[0]) for r in rows]
        if not sizes:
            return "No data found in the database."
        return "Available message sizes: " + ", ".join(sizes)
    except pymysql.Error as e:
        return f"Database error: {e}"


# ── Phase C: cross-DB integration with QUJATA-TID ─────────────────────────
# Two tools that reach beyond the local performance_test table into the
# QUJATA-TID benchmarking stack's MySQL (typically qujata-mysql:3306).

QUJATA_DB_CONFIG = {
    "host":     os.getenv("QUJATA_DB_HOST", "qujata-mysql"),
    "port":     int(os.getenv("QUJATA_DB_PORT", "3306")),
    "db":       os.getenv("QUJATA_DB_NAME", "qujata"),
    "user":     os.getenv("QUJATA_DB_USER", "root"),
    "password": os.getenv("QUJATA_DB_PASSWORD", "qujata"),
    "cursorclass": pymysql.cursors.DictCursor,
}


def _qujata_connect():
    return pymysql.connect(**QUJATA_DB_CONFIG)


def _parse_suite_params(description):
    """Parse `launch_qujata_experiment` kwargs out of a free-text suite
    description (e.g. 'Demo 1 PQC tax baseline: classical vs mlkem768, 10s @ 50 Mbps').
    Returns kwargs dict or None if no algorithm could be identified."""
    if not description:
        return None
    import re as _re
    text = description.lower()
    alg = None
    for cand in sorted(_VALID_ALGS, key=len, reverse=True):
        if cand.lower() in text:
            alg = cand
            break
    if not alg:
        return None
    duration_s = 10
    m = _re.search(r"(\d+)\s*(?:s\b|sec(?:onds?)?\b)", text)
    if m:
        duration_s = max(1, min(60, int(m.group(1))))
    bandwidth_kbps = 50000
    m = _re.search(r"(\d+)\s*mbps", text)
    if m:
        bandwidth_kbps = max(1, min(200000, int(m.group(1)) * 1000))
    else:
        m = _re.search(r"(\d+)\s*gbps", text)
        if m:
            bandwidth_kbps = max(1, min(200000, int(m.group(1)) * 1000000))
    slice_id = "mgmt"
    for s in _VALID_SLICES:
        if s.lower() in text:
            slice_id = s
            break
    return {"algorithm": alg, "slice_id": slice_id, "duration_s": duration_s,
            "bandwidth_kbps": bandwidth_kbps, "message_size": 1200, "connections": 1}


@mcp.tool()
def query_test_suite(test_suite_id: int, ctx: Context,
                     auto_relaunch: bool = True) -> str:
    """[QUJATA] Fetch a QUJATA test suite by id. Returns the suite metadata + every
    test_run inside it (algorithm, message size, status, embedded JSON metrics
    from status_message such as throughput / jitter / loss / kem latency).

    Use this when the user mentions a specific 'test suite N', 'run N', or
    'the experiment I just launched'. The suite ids come from QUJATA's MySQL
    (qujata.test_suites), not from performance_test.

    auto_relaunch (default True): if the suite exists but has 0 test_runs
    (orphan from initial bring-up), parse algorithm/duration/bandwidth/slice
    from the suite description, fire `launch_qujata_experiment` with those
    parameters (qujata-api always creates a NEW suite_id), wait up to ~35 s
    for runs to land, then return the new suite's data with a header noting
    which empty suite triggered the relaunch. Pass auto_relaunch=False for
    strict read-only behaviour."""
    auto_header = ""
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _qujata_connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, start_time, end_time "
                "FROM test_suites WHERE id = %s", (int(test_suite_id),))
            suite = cur.fetchone()
            if not suite:
                conn.close()
                return f"No QUJATA test suite found with id={test_suite_id}."

            cur.execute(
                "SELECT id, algorithm, iterations, message_size, status, "
                "       status_message, start_time, end_time "
                "FROM test_runs WHERE test_suite_id = %s ORDER BY id",
                (int(test_suite_id),))
            runs = list(cur.fetchall())
        conn.close()

        if not runs and auto_relaunch:
            params = _parse_suite_params(suite.get('description') or '')
            if not params:
                auto_header = (
                    f"Suite #{test_suite_id} ('{suite['name']}') has 0 test_runs "
                    f"and the description didn't carry parseable parameters. "
                    f"Call launch_qujata_experiment(...) manually with explicit "
                    f"algorithm/slice/duration/bandwidth.\n\n"
                )
            else:
                launch_result = launch_qujata_experiment(**params, ctx=ctx)
                bad_prefixes = ("Refusing:", "Could not reach", "QUJATA API error")
                if any(launch_result.startswith(p) for p in bad_prefixes):
                    auto_header = (
                        f"Suite #{test_suite_id} ('{suite['name']}') has 0 test_runs "
                        f"and auto-relaunch was declined: {launch_result}\n\n"
                    )
                else:
                    import re as _re2
                    import time as _time
                    m = _re2.search(r"#(\d+)\s+launched", launch_result)
                    if not m:
                        auto_header = (
                            f"Suite #{test_suite_id} had 0 runs; auto-relaunch fired "
                            f"but the launch result was unparseable: "
                            f"{launch_result[:200]}\n\n"
                        )
                    else:
                        new_id = int(m.group(1))
                        deadline = _time.time() + 35
                        new_runs = []
                        new_suite = None
                        while _time.time() < deadline:
                            try:
                                conn = _qujata_connect()
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "SELECT id, name, description, start_time, end_time "
                                        "FROM test_suites WHERE id = %s", (new_id,))
                                    new_suite = cur.fetchone()
                                    cur.execute(
                                        "SELECT id, algorithm, iterations, message_size, status, "
                                        "       status_message, start_time, end_time "
                                        "FROM test_runs WHERE test_suite_id = %s ORDER BY id",
                                        (new_id,))
                                    new_runs = list(cur.fetchall())
                                conn.close()
                            except pymysql.Error:
                                pass
                            if new_runs:
                                break
                            _time.sleep(2)
                        if new_runs and new_suite:
                            auto_header = (
                                f"Suite #{test_suite_id} ('{suite['name']}') had 0 "
                                f"test_runs (likely orphaned). Auto-relaunched a fresh "
                                f"experiment with parsed params {params} → new suite "
                                f"#{new_id}. Showing the NEW suite's data:\n\n"
                            )
                            suite = new_suite
                            runs = new_runs
                        else:
                            auto_header = (
                                f"Suite #{test_suite_id} had 0 runs; auto-launched a "
                                f"fresh experiment as suite #{new_id} but its runs "
                                f"haven't landed yet (waited 35 s). Call "
                                f"query_test_suite(test_suite_id={new_id}) in another ~30 s.\n\n"
                            )
    except pymysql.Error as e:
        return f"QUJATA database error: {e}"

    import json as _json
    lines = [
        f"### QUJATA Test Suite #{suite['id']} — {suite['name']}",
        f"_{suite['description'] or '(no description)'}_",
        "",
        f"Started: {suite['start_time']}  ·  Ended: {suite['end_time']}",
        f"Test runs: {len(runs)}",
        "",
        "| run id | algorithm | msg size | status | metrics |",
        "|--------|-----------|----------|--------|---------|",
    ]
    for r in runs:
        m = {}
        try:
            m = _json.loads(r.get('status_message') or '{}')
        except Exception:
            pass
        mbps    = m.get('mbps')
        jitter  = m.get('jitter_ms')
        loss    = m.get('lost_percent')
        kem     = m.get('kem_total_ms')
        slice_  = m.get('slice_id', '')
        if mbps is not None:
            try:
                metrics_str = (
                    f"slice={slice_} mbps={mbps} "
                    f"jitter={float(jitter):.4f}ms loss={loss}% kem={kem}ms"
                )
            except Exception:
                metrics_str = f"slice={slice_} mbps={mbps}"
        else:
            metrics_str = f"slice={slice_} (no metrics)"
        lines.append(
            f"| {r['id']} | {r['algorithm']} | {r['message_size']} | "
            f"{r['status']} | {metrics_str} |"
        )
    return auto_header + "\n".join(lines)


@mcp.tool()
def get_latest_test_suite(ctx: Context) -> str:
    """[QUJATA] Return the most recently created QUJATA test suite (id, name,
    test-run count, timestamps). Useful for follow-up after a run completes."""
    if err := _check_ctx(ctx):
        return err
    try:
        conn = _qujata_connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts.id, ts.name, ts.description, ts.start_time, ts.end_time, "
                "       (SELECT COUNT(*) FROM test_runs tr WHERE tr.test_suite_id = ts.id) AS n_runs "
                "FROM test_suites ts ORDER BY ts.id DESC LIMIT 1")
            row = cur.fetchone()
        conn.close()
    except pymysql.Error as e:
        return f"QUJATA database error: {e}"
    if not row:
        return "No test suites yet."
    header = (f"Latest QUJATA test suite: id={row['id']}, name={row['name']!r}, "
              f"runs={row['n_runs']}, started={row['start_time']}, ended={row['end_time']}.\n"
              f"Description: {row['description']}\n")
    # If the latest suite already has runs, fold the full suite markdown
    # in here so callers don't have to chain query_test_suite. llama3.1:8b
    # frequently drops the integer id between two consecutive tool calls
    # and ends up querying test_suite_id=0; this collapses the chain.
    if int(row.get('n_runs') or 0) > 0:
        try:
            details = query_test_suite(int(row['id']), ctx, auto_relaunch=False)
            return header + "\n" + details
        except Exception:
            pass
    return header + f"Run query_test_suite(test_suite_id={row['id']}) to see the full results."


# ── Phase D: launch a new QUJATA experiment ──────────────────────────────
# Lets the LLM kick off a new iperf experiment via the QUJATA API. Hard
# safety caps are enforced server-side regardless of what the LLM asks.

QUJATA_API_BASE = os.getenv("QUJATA_API_BASE", "http://qujata-api:3020/qujata-api")

# Hard safety bounds — the LLM cannot bypass these from chat
_MAX_DURATION_S    = 60     # 1 min
_MAX_BANDWIDTH_KBPS = 200000  # 200 Mbps
_MAX_CONNECTIONS   = 8
_VALID_SLICES      = {"research", "iot", "video", "mgmt", "slice1", "slice2", "slice3",
                      "uc3-slice-1", "uc3-slice-2", "uc3-slice-3"}
_VALID_ALGS        = {"classical", "kyber512", "kyber768", "kyber1024",
                      "mlkem512", "mlkem768", "mlkem1024",
                      "p256_mlkem512", "p384_mlkem768", "x25519mlkem768"}


@mcp.tool()
def launch_qujata_experiment(
    algorithm: str,
    slice_id: str = "mgmt",
    duration_s: int = 10,
    bandwidth_kbps: int = 50000,
    message_size: int = 1200,
    connections: int = 1,
    ctx: Context = None,
) -> str:
    """[QUJATA] Launch a new QUJATA iperf experiment via the QUJATA API.

    Required:
      algorithm     one of: classical, mlkem512, mlkem768, mlkem1024
                    (also accepted: kyber{512,768,1024}, hybrids like x25519mlkem768)
    Optional:
      slice_id      'research' | 'iot' | 'video' | 'mgmt' (default 'mgmt')
      duration_s    1..60 seconds (default 10)
      bandwidth_kbps 1..200000 Kbps (default 50000 = 50 Mbps)
      message_size  any positive int (default 1200, near-MTU)
      connections   1..8 parallel iperf streams (default 1)

    Returns the new test_suite_id and a hint about how to fetch results
    via query_test_suite once the run completes (~30 seconds later).

    Hard caps:  duration ≤ 60s, bandwidth ≤ 200 Mbps, connections ≤ 8.
    Returns an error if the QUJATA API reports another run is in flight
    (HTTP 423 Locked)."""
    if err := _check_ctx(ctx):
        return err

    # Validate
    alg = (algorithm or "").strip().lower()
    sid = (slice_id or "mgmt").strip().lower()
    if alg not in _VALID_ALGS:
        return f"Refusing: algorithm '{algorithm}' is not in the allowed list ({sorted(_VALID_ALGS)})."
    if sid not in _VALID_SLICES:
        return f"Refusing: slice_id '{slice_id}' is not valid ({sorted(_VALID_SLICES)})."
    if not (1 <= int(duration_s) <= _MAX_DURATION_S):
        return f"Refusing: duration_s must be between 1 and {_MAX_DURATION_S}."
    if not (1 <= int(bandwidth_kbps) <= _MAX_BANDWIDTH_KBPS):
        return f"Refusing: bandwidth_kbps must be between 1 and {_MAX_BANDWIDTH_KBPS}."
    if not (1 <= int(connections) <= _MAX_CONNECTIONS):
        return f"Refusing: connections must be between 1 and {_MAX_CONNECTIONS}."

    payload = {
        "experimentName":    f"mcp-launched-{alg}-{sid}",
        "description":       f"Launched via MCP launch_qujata_experiment tool",
        "ipsecAlgorithms":   [alg],
        "time":              [int(duration_s)],
        "connections":       [int(connections)],
        "messageSizeIperf":  [int(message_size)],
        "bandwidth":         [int(bandwidth_kbps)],
        "intervals":         [1],
        "sliceId":           sid,
    }
    try:
        import urllib.request, urllib.error, json as _json
        req = urllib.request.Request(
            QUJATA_API_BASE.rstrip("/") + "/analyze",
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = _json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 423:
            return ("Refusing: a QUJATA test is already in flight. "
                    "Wait for it to finish (use get_latest_test_suite to check) "
                    "and try again.")
        return f"QUJATA API error {e.code}: {body}"
    except Exception as e:
        return f"Could not reach QUJATA API ({QUJATA_API_BASE}): {e}"

    suite_id = resp.get("test_suite_id")
    return (f"OK — QUJATA test suite #{suite_id} launched "
            f"({alg} on slice='{sid}', {duration_s}s @ {bandwidth_kbps/1000:.0f} Mbps, "
            f"msg={message_size}B, P={connections}).\n"
            f"It will finish in ~{int(duration_s) + 5}s. "
            f"Run query_test_suite(test_suite_id={suite_id}) to see the full results.")


# ── Katana NBI + KPI collector integration ───────────────────────────────
# Tools that reach the Katana Slice Manager NBI (slice lifecycle) and the
# katana-pqc-kpi collector (UC3 KPI events). Both endpoints are env-gated:
# if KATANA_NBI_BASE / KATANA_KPI_BASE are unset or unreachable the tools
# return a soft error so the agent can fall back gracefully.

KATANA_NBI_BASE = os.getenv("KATANA_NBI_BASE", "http://katana-nbi:8000/api")
KATANA_KPI_BASE = os.getenv("KATANA_KPI_BASE", "http://katana-pqc-kpi:8080")
_KATANA_HTTP_TIMEOUT = int(os.getenv("KATANA_HTTP_TIMEOUT", "10"))

# Hard caps to prevent the LLM from creating arbitrary large slices or
# deleting things it shouldn't.
_KATANA_MAX_LIST_ITEMS = 50
_KATANA_ALLOW_CREATE   = os.getenv("KATANA_ALLOW_CREATE", "false").lower() == "true"
_KATANA_ALLOW_DELETE   = os.getenv("KATANA_ALLOW_DELETE", "false").lower() == "true"


def _katana_http(method: str, url: str, payload=None) -> tuple:
    """Returns (ok, body_or_err). Soft-fail on connection errors."""
    import urllib.request, urllib.error, json as _json
    data = None
    headers = {}
    if payload is not None:
        data = _json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_KATANA_HTTP_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return True, _json.loads(body)
            except Exception:
                return True, body
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return False, f"Could not reach {url}: {e}"


@mcp.tool()
def katana_list_slices(ctx: Context) -> str:
    """[KATANA] List network slices currently registered in the Katana Slice Manager
    NBI. Returns slice id, name, status, and creation timestamp for each.

    Use when the user asks "which slices are running", "list slices", or
    "show me the testbed slices". Does not modify state.
    """
    if err := _check_ctx(ctx):
        return err
    ok, body = _katana_http("GET", KATANA_NBI_BASE.rstrip("/") + "/slice")
    if not ok:
        return f"Katana NBI unreachable: {body}"
    if not isinstance(body, list):
        return f"Unexpected NBI response shape: {str(body)[:200]}"
    if not body:
        return "No slices registered in Katana NBI."
    rows = body[:_KATANA_MAX_LIST_ITEMS]
    lines = [
        "### Katana slices",
        "",
        "| _id | name | status | created_at |",
        "|-----|------|--------|------------|",
    ]
    for s in rows:
        lines.append(
            f"| {s.get('_id') or s.get('slice_id', '?')} "
            f"| {s.get('slice_name') or s.get('name', '?')} "
            f"| {s.get('status', '?')} "
            f"| {s.get('created_at', '?')} |"
        )
    if len(body) > _KATANA_MAX_LIST_ITEMS:
        lines.append(f"\n_Showing first {_KATANA_MAX_LIST_ITEMS} of {len(body)} slices._")
    return "\n".join(lines)


@mcp.tool()
def katana_get_slice(slice_id: str, ctx: Context) -> str:
    """[KATANA] Fetch full details of a single Katana slice by id.

    Returns deployment_time, coverage, throughput targets, and runtime
    status. The slice id is the value shown in `katana_list_slices`.
    """
    if err := _check_ctx(ctx):
        return err
    sid = (slice_id or "").strip()
    if not sid:
        return "Refusing: slice_id is empty."
    ok, body = _katana_http("GET", KATANA_NBI_BASE.rstrip("/") + f"/slice/{sid}")
    if not ok:
        return f"Katana NBI error: {body}"
    if not isinstance(body, dict):
        return f"Unexpected NBI response: {str(body)[:200]}"
    import json as _json
    pretty = _json.dumps(body, indent=2, default=str)
    if len(pretty) > 4000:
        pretty = pretty[:4000] + "\n... (truncated)"
    return f"### Katana slice {sid}\n\n```json\n{pretty}\n```"


@mcp.tool()
def katana_create_slice(gst: dict, ctx: Context) -> str:
    """[KATANA] Create a new Katana network slice from a GST descriptor.

    Body must be a dict with a top-level "gst" object containing at
    least base_slice_descriptor / service_descriptor / test_descriptor.
    Disabled by default — set KATANA_ALLOW_CREATE=true on the MCP server
    to enable.
    """
    if err := _check_ctx(ctx):
        return err
    if not _KATANA_ALLOW_CREATE:
        return ("Refusing: slice creation is disabled on this MCP server "
                "(KATANA_ALLOW_CREATE=false). Ask the operator to flip the "
                "env var if you want chat-driven slice creation.")
    if not isinstance(gst, dict) or not gst:
        return "Refusing: gst must be a non-empty dict."
    payload = gst if "gst" in gst else {"gst": gst}
    ok, body = _katana_http("POST", KATANA_NBI_BASE.rstrip("/") + "/slice", payload)
    if not ok:
        return f"Katana NBI error: {body}"
    return f"Slice creation accepted by NBI: {str(body)[:500]}"


@mcp.tool()
def katana_delete_slice(slice_id: str, ctx: Context) -> str:
    """[KATANA] Delete a Katana network slice by id.

    Disabled by default — set KATANA_ALLOW_DELETE=true on the MCP server.
    Irreversible; use with care.
    """
    if err := _check_ctx(ctx):
        return err
    if not _KATANA_ALLOW_DELETE:
        return ("Refusing: slice deletion is disabled on this MCP server "
                "(KATANA_ALLOW_DELETE=false).")
    sid = (slice_id or "").strip()
    if not sid:
        return "Refusing: slice_id is empty."
    ok, body = _katana_http("DELETE", KATANA_NBI_BASE.rstrip("/") + f"/slice/{sid}")
    if not ok:
        return f"Katana NBI error: {body}"
    return f"Slice {sid} delete request accepted: {str(body)[:300]}"


@mcp.tool()
def katana_kpi_events(limit: int = 20, ctx: Context = None) -> str:
    """[KATANA] Fetch the most recent UC3 PQC KPI events from the katana-pqc-kpi
    collector (events emitted over UDP by Kyber/Besu agents on the testbed).

    Returns up to `limit` events (default 20, hard cap 50). Each event
    typically carries kpi_id, algorithm, key_size, reporter_ip, ts.
    """
    if err := _check_ctx(ctx):
        return err
    n = max(1, min(int(limit or 20), _KATANA_MAX_LIST_ITEMS))
    ok, body = _katana_http("GET", KATANA_KPI_BASE.rstrip("/") + "/api/events")
    if not ok:
        return f"katana-pqc-kpi unreachable: {body}"
    if not isinstance(body, list):
        return f"Unexpected KPI response shape: {str(body)[:200]}"
    if not body:
        return "No KPI events captured yet."
    events = body[-n:]
    lines = [
        f"### Last {len(events)} UC3 KPI events",
        "",
        "| ts | kpi_id | algorithm | key_size | reporter |",
        "|----|--------|-----------|----------|----------|",
    ]
    for e in events:
        if not isinstance(e, dict):
            continue
        lines.append(
            f"| {e.get('ts', '?')} "
            f"| {e.get('kpi_id', '?')} "
            f"| {e.get('algorithm', '?')} "
            f"| {e.get('key_size', '?')} "
            f"| {e.get('reporter_ip', '?')} |"
        )
    return "\n".join(lines)


@mcp.tool()
def katana_kpi_stats(ctx: Context) -> str:
    """[KATANA] Aggregated stats from katana-pqc-kpi: distinct kpi_ids, algorithms,
    reporters, total bytes, fragmented count, average key size."""
    if err := _check_ctx(ctx):
        return err
    ok, body = _katana_http("GET", KATANA_KPI_BASE.rstrip("/") + "/api/stats")
    if not ok:
        return f"katana-pqc-kpi unreachable: {body}"
    if not isinstance(body, dict):
        return f"Unexpected KPI stats shape: {str(body)[:200]}"
    import json as _json
    return "### katana-pqc-kpi stats\n\n```json\n" + _json.dumps(body, indent=2, default=str) + "\n```"


# ── UC3 (Campaign A) integration ─────────────────────────────────────────
# UC3 is the original PQ-REACT Use Case 3 pilot: 5G slicing + Hyperledger
# Besu IBFT 2-node DLT for KPI anchoring + a Kyber-768 KEM daemon over
# StrongSwan IPsec. The 9 official KPIs (UC3-01..UC3-09) are emitted as
# UDP events to the katana-pqc-kpi collector and pulled by katana_kpi_*
# above. The tools below cover the prerequisites that the existing
# Katana tools don't reach — Besu RPC reachability, slice-bandwidth
# validation runs, KPI-id-filtered summaries, and a composite readiness
# gate so the chat can answer "is UC3 ready to demo?" in one tool hop.
#
# Endpoints are env-gated and soft-fail on connection error so the
# orchestrator can degrade gracefully when a UC3 prerequisite is offline.
# See UC3_MCP_INTEGRATION_PLAN.md for the full surface area + roadmap.

# Single-host Besu RPC (the IBFT validator on the core VM). The RAN-side
# validator is reachable via the IPsec tunnel, but for status questions
# the core RPC is enough and matches what the bandwidth/KPI scripts use.
BESU_RPC_BASE = os.getenv("BESU_RPC_BASE", "")
UC3_KYBER_HEALTH_URL = os.getenv("UC3_KYBER_HEALTH_URL", "")
_UC3_HTTP_TIMEOUT = int(os.getenv("UC3_HTTP_TIMEOUT", "5"))

# Phase 7b — exec-runner support. UC3_ALLOW_EXEC gates the two mutating
# tools (uc3_run_bandwidth_validation, uc3_run_kpi_experiments). UC3_BIN_DIR
# points at a directory holding the upstream platform/bin scripts
# (run_uc3_bandwidth_validation.sh, run_uc3_kpi_experiments.sh) on the MCP
# server's filesystem. UC3_ARTIFACTS_DIR is read-only and used by
# uc3_list_artifacts; safe to set without exec.
UC3_ALLOW_EXEC = os.getenv("UC3_ALLOW_EXEC", "false").lower() == "true"
UC3_BIN_DIR = os.getenv("UC3_BIN_DIR", "")
UC3_ARTIFACTS_DIR = os.getenv("UC3_ARTIFACTS_DIR", "")
_UC3_RUN_TIMEOUT_S = int(os.getenv("UC3_RUN_TIMEOUT_S", "600"))

# Recognised UC3 KPI ids (the existing katana-pqc-kpi server normalises to
# these strings in /api/stats; we use the same set for filtering).
_UC3_KPI_IDS = {
    "KPI-UC3-01", "KPI-UC3-02", "KPI-UC3-03", "KPI-UC3-04",
    "KPI-UC3-05", "KPI-UC3-06", "KPI-UC3-07", "KPI-UC3-08",
    "KPI-UC3-09",
}


def _uc3_jsonrpc(method: str, params=None) -> tuple:
    """Single-shot Besu JSON-RPC call. Returns (ok, result_or_err).
    Soft-fails on connection error so the composite uc3_health tool can
    keep checking the rest of the prerequisites instead of bailing out."""
    if not BESU_RPC_BASE:
        return False, "BESU_RPC_BASE is not set on this server"
    import json as _json
    import urllib.request, urllib.error
    payload = _json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        BESU_RPC_BASE,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_UC3_HTTP_TIMEOUT) as r:
            body = _json.loads(r.read().decode("utf-8", errors="replace"))
        if "error" in body:
            return False, f"Besu RPC error: {body['error']}"
        return True, body.get("result")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} from Besu: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:
        return False, f"Besu RPC unreachable: {e}"


@mcp.tool()
def uc3_blockchain_status(ctx: Context) -> str:
    """[KATANA] Live status of the UC3 Hyperledger Besu IBFT 2-node DLT.

    Returns the latest block height (eth_blockNumber), the validator's
    current peer count (net_peerCount), and whether it's still syncing
    (eth_syncing). Use to answer "is the UC3 blockchain reachable?",
    "are both Besu nodes peered?", or "what block number is UC3 on?".

    Requires BESU_RPC_BASE to be set (e.g. http://<core-vm-host>:8545).
    Soft-fails when Besu is offline so the LLM can fall back to a
    degraded answer instead of crashing the chat round.
    """
    if err := _check_ctx(ctx):
        return err
    if not BESU_RPC_BASE:
        return ("UC3 blockchain status unavailable: BESU_RPC_BASE not "
                "configured on this MCP server.")
    ok_bn, bn = _uc3_jsonrpc("eth_blockNumber")
    ok_pc, pc = _uc3_jsonrpc("net_peerCount")
    ok_sy, sy = _uc3_jsonrpc("eth_syncing")
    if not ok_bn:
        return f"UC3 blockchain unreachable: {bn}"

    def _hex(v):
        try:
            return int(v, 16) if isinstance(v, str) and v.startswith("0x") else v
        except Exception:
            return v

    block_h = _hex(bn)
    peers = _hex(pc) if ok_pc else None
    syncing = (sy is False) if ok_sy else None  # `false` = up to date

    lines = [
        "### UC3 blockchain (Hyperledger Besu IBFT)",
        "",
        f"  block_height: **{block_h}**",
        f"  peer_count:   **{peers}**" if peers is not None else
        f"  peer_count:   _unavailable: {pc}_",
        (f"  status:       **synced**"
         if syncing is True else
         (f"  status:       **syncing**" if syncing is False
          else f"  status:       _unknown: {sy}_")),
        "",
        f"_Source: {BESU_RPC_BASE}_",
    ]
    if peers is not None and peers < 1:
        lines.append("")
        lines.append("⚠ **Peer count is 0** — the second Besu validator "
                     "is unreachable. Check the besu-pqc IKEv2 tunnel "
                     "with `swanctl --list-sas` on either VM.")
    return "\n".join(lines)


@mcp.tool()
def uc3_kpi_summary(kpi_id: str = "", limit: int = 50,
                   ctx: Context = None) -> str:
    """[KATANA] UC3 KPI-id-filtered summary view of the katana-pqc-kpi
    collector's events. Wraps the same /api/events endpoint as
    katana_kpi_events but filters server-side by kpi_id (e.g. 'KPI-UC3-03'
    or 'KPI-UC3-04') and returns a per-algorithm rollup instead of a row
    dump. Pairs with the comparative reports under
    `campaign-a-uc3-blockchain/reports/`.

    Args:
        kpi_id  one of KPI-UC3-01 .. KPI-UC3-09 (case-insensitive). Empty
                string ⇒ all UC3 KPIs grouped by id.
        limit   how many recent events to scan (1..200, default 50).

    Soft-fails when katana-pqc-kpi is unreachable.
    """
    if err := _check_ctx(ctx):
        return err
    n = max(1, min(int(limit or 50), 200))
    requested = (kpi_id or "").strip().upper()
    if requested and requested not in _UC3_KPI_IDS:
        return (f"Unknown kpi_id '{kpi_id}'. Valid: "
                + ", ".join(sorted(_UC3_KPI_IDS)) + ". Pass empty for all.")
    ok, body = _katana_http(
        "GET", KATANA_KPI_BASE.rstrip("/") + "/api/events"
    )
    if not ok:
        return f"katana-pqc-kpi unreachable: {body}"
    if not isinstance(body, list):
        return f"Unexpected KPI events shape: {str(body)[:200]}"
    events = body[-n:]
    matched = [
        e for e in events
        if isinstance(e, dict)
        and (not requested or str(e.get("kpi_id", "")).upper() == requested)
    ]
    if not matched:
        return (f"No events for kpi_id={requested or 'ANY'} in the last "
                f"{n} samples on katana-pqc-kpi.")

    # Roll up: count per (kpi_id, algorithm), avg key_size, fragmented count.
    rollup: dict = {}
    for e in matched:
        k = (str(e.get("kpi_id", "?")), str(e.get("algorithm", "Unknown")))
        bucket = rollup.setdefault(k, {"n": 0, "key_sizes": [], "frag": 0})
        bucket["n"] += 1
        try:
            ks = float(e.get("key_size") or 0)
            if ks > 0:
                bucket["key_sizes"].append(ks)
        except Exception:
            pass
        if int(e.get("fragmented") or 0) > 0:
            bucket["frag"] += 1

    lines = [
        f"### UC3 KPI summary  ({'all' if not requested else requested}, "
        f"window={n})",
        "",
        "| kpi_id | algorithm | events | avg key_size (B) | fragmented |",
        "|--------|-----------|--------|------------------|------------|",
    ]
    for (kid, alg), b in sorted(rollup.items()):
        avg_ks = (sum(b["key_sizes"]) / len(b["key_sizes"])) if b["key_sizes"] else 0.0
        lines.append(
            f"| {kid} | {alg} | {b['n']} | "
            f"{avg_ks:.0f} | {b['frag']} |"
        )
    lines.append("")
    lines.append(f"_Source: {KATANA_KPI_BASE.rstrip('/')}/api/events_")
    return "\n".join(lines)


# Alias — llama3.1:8b kept hallucinating this name during the 2026-05-08
# chat smoke tests ("Summarise the KPI-UC3-04 events for the last 50
# samples" → invented `uc3_kpi_events` instead of calling
# uc3_kpi_summary). Registering it as a real tool that delegates removes
# the failure mode without retraining the model. The docstring deliberately
# makes the alias relationship visible so an operator looking at the tool
# list isn't confused.
@mcp.tool()
def uc3_kpi_events(kpi_id: str = "", limit: int = 50,
                  ctx: Context = None) -> str:
    """[KATANA] Alias for `uc3_kpi_summary`. Same args, same output.
    Kept because llama3.1:8b consistently invents this name when asked
    'summarise the KPI-UC3-NN events'. New code should call
    uc3_kpi_summary directly."""
    return uc3_kpi_summary(kpi_id=kpi_id, limit=limit, ctx=ctx)


# Alias — llama3.1:8b also invents `query_slices` for "list the katana
# slices" / "show me the slices" instead of calling katana_list_slices.
# Same defensive-alias pattern as uc3_kpi_events above; saves the chat
# from a hop-budget loop or a JSON-in-content leak when the model
# guesses the tool name.
@mcp.tool()
def query_slices(ctx: Context) -> str:
    """[KATANA] Alias for `katana_list_slices`. Same output. Kept because
    llama3.1:8b consistently invents this name when asked 'list the
    katana slices'. New code should call katana_list_slices directly."""
    return katana_list_slices(ctx=ctx)


@mcp.tool()
def uc3_health(ctx: Context) -> str:
    """[KATANA] Composite UC3 demo readiness gate — checks all four
    prerequisites in one tool call so the chat can answer "is UC3 ready
    to demo?" without firing four separate tools.

    Checks:
      1. katana-nbi reachable (slice lifecycle)
      2. katana-pqc-kpi reachable (KPI collector)
      3. Besu IBFT validator reachable + peers ≥ 1 + not syncing
      4. (optional) Kyber-768 KEM daemon health endpoint, when
         UC3_KYBER_HEALTH_URL is set.

    Returns a markdown checklist with PASS/FAIL per prerequisite plus a
    one-line verdict. Never raises — every check is wrapped so the
    operator gets a partial verdict even when half the testbed is down.
    """
    if err := _check_ctx(ctx):
        return err

    checks: list[tuple[str, bool, str]] = []

    # 1. NBI
    nbi_url = KATANA_NBI_BASE.rstrip("/") + "/slice"
    ok_nbi, body_nbi = _katana_http("GET", nbi_url)
    checks.append((
        "katana-nbi (slice API)",
        ok_nbi,
        ("reachable; "
         f"{len(body_nbi) if isinstance(body_nbi, list) else 0} slices listed")
        if ok_nbi else f"unreachable ({str(body_nbi)[:120]})"
    ))

    # 2. KPI collector
    kpi_url = KATANA_KPI_BASE.rstrip("/") + "/api/health"
    ok_kpi, body_kpi = _katana_http("GET", kpi_url)
    if ok_kpi and isinstance(body_kpi, dict):
        msg = (f"reachable; udp:{body_kpi.get('udp_listen_port', '?')}, "
               f"events_in_memory={body_kpi.get('events_in_memory', '?')}")
    else:
        msg = f"unreachable ({str(body_kpi)[:120]})"
    checks.append(("katana-pqc-kpi", ok_kpi, msg))

    # 3. Besu RPC + peer count
    if BESU_RPC_BASE:
        ok_bn, bn = _uc3_jsonrpc("eth_blockNumber")
        if not ok_bn:
            checks.append(("besu-pqc IBFT", False, str(bn)[:120]))
        else:
            ok_pc, pc = _uc3_jsonrpc("net_peerCount")
            try:
                peers = int(pc, 16) if isinstance(pc, str) else int(pc or 0)
            except Exception:
                peers = 0
            try:
                block_h = (int(bn, 16)
                           if isinstance(bn, str) and bn.startswith("0x") else
                           int(bn))
            except Exception:
                block_h = bn
            ok_pc_eff = ok_pc and peers >= 1
            checks.append((
                "besu-pqc IBFT (RPC + peers)",
                bool(ok_bn and ok_pc_eff),
                f"block={block_h}, peers={peers}"
                + ("" if ok_pc_eff else " ⚠ peers<1"),
            ))
    else:
        checks.append(("besu-pqc IBFT (RPC + peers)", False,
                       "BESU_RPC_BASE not configured"))

    # 4. Optional Kyber daemon
    if UC3_KYBER_HEALTH_URL:
        ok_ky, body_ky = _katana_http("GET", UC3_KYBER_HEALTH_URL)
        checks.append((
            "kyber-768 KEM daemon",
            ok_ky,
            ("reachable" if ok_ky else
             f"unreachable ({str(body_ky)[:120]})"),
        ))

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    verdict = ("✅ UC3 demo READY" if passed == total
               else f"❌ UC3 demo NOT ready ({passed}/{total} prerequisites OK)")

    lines = [f"### UC3 readiness — {verdict}", ""]
    lines.append("| check | status | detail |")
    lines.append("|-------|--------|--------|")
    for name, ok, detail in checks:
        status = "✅ PASS" if ok else "❌ FAIL"
        lines.append(f"| {name} | {status} | {detail} |")
    return "\n".join(lines)


# ── UC3 Phase 7b — exec runners + artifact reader ───────────────────────
# uc3_list_artifacts is read-only and can run any time UC3_ARTIFACTS_DIR
# is set. The two run_* tools spawn shell jobs and are gated behind
# UC3_ALLOW_EXEC=true (off by default). Both write a row to uc3_runs
# BEFORE the subprocess starts so a hung/killed run still has a forensic
# trail; status is updated to ok/failed/timeout AFTER wait().

_UC3_RUN_KINDS = {"bandwidth_validation", "kpi_experiments"}


def _uc3_record_run_start(kind: str, args: dict, requested_by: str) -> int:
    """Insert a 'running' row in uc3_runs and return its id.

    Done BEFORE spawning the subprocess so we never apply without
    logging — same pattern as apply_pqc_configuration. On DB failure
    returns -1 and the caller refuses to spawn the subprocess (better
    to fail closed than to run blind)."""
    import json as _json
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO uc3_runs (kind, args_json, status, requested_by) "
                "VALUES (%s, %s, 'running', %s)",
                (kind[:48], _json.dumps(args)[:65000],
                 (requested_by or "")[:64] or None),
            )
            run_id = cur.lastrowid
        conn.commit()
        conn.close()
        return int(run_id)
    except pymysql.Error:
        return -1


def _uc3_record_run_finish(run_id: int, status: str, exit_code,
                          summary: dict | None, artifact_path: str,
                          notes: str) -> None:
    """Mark a uc3_runs row finished. Best-effort — silent on DB error so
    a stale 'running' row never crashes the chat round."""
    import json as _json
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE uc3_runs "
                "SET finished_at=CURRENT_TIMESTAMP, status=%s, exit_code=%s, "
                "    summary_json=%s, artifact_path=%s, notes=%s "
                "WHERE id=%s",
                (status[:16],
                 int(exit_code) if exit_code is not None else None,
                 _json.dumps(summary)[:65000] if summary else None,
                 (artifact_path or "")[:512] or None,
                 (notes or "")[:65000] or None,
                 int(run_id)),
            )
        conn.commit()
        conn.close()
    except pymysql.Error:
        pass


@mcp.tool()
def uc3_list_artifacts(kind: str = "", limit: int = 10,
                      ctx: Context = None) -> str:
    """[KATANA] List recent UC3 pilot artifacts under UC3_ARTIFACTS_DIR.

    The two upstream drivers (run_uc3_bandwidth_validation.sh and
    run_uc3_kpi_experiments.sh) drop their JSON outputs into timestamped
    subdirectories of `artifacts/uc3_bandwidth/<ts>/` and
    `artifacts/uc3_kpi/<ts>/` respectively. This tool surfaces the
    most recent runs so the chat can answer "show me the last KPI
    dataset" / "what was the latest bandwidth run's verdict" without
    triggering a new run.

    Args:
        kind   '' (any) | 'bandwidth' | 'kpi'  (case-insensitive)
        limit  1..50, default 10

    Read-only — no exec gate.
    """
    if err := _check_ctx(ctx):
        return err
    if not UC3_ARTIFACTS_DIR:
        return ("UC3 artifacts unavailable: UC3_ARTIFACTS_DIR not "
                "configured on this MCP server.")
    import os as _os
    base = UC3_ARTIFACTS_DIR.rstrip("/")
    if not _os.path.isdir(base):
        return f"UC3_ARTIFACTS_DIR={base} does not exist or is not a directory."

    n = max(1, min(int(limit or 10), 50))
    k = (kind or "").strip().lower()
    if k not in ("", "bandwidth", "kpi"):
        return ("Unknown kind '%s'. Valid: '' (any) | 'bandwidth' | 'kpi'."
                % kind)

    # Look for both subdir naming styles used by the upstream scripts.
    candidates: list[tuple[str, str, float]] = []  # (kind, full_path, mtime)
    for sub_kind, sub_dir in (("bandwidth", "uc3_bandwidth"),
                              ("kpi", "uc3_kpi")):
        if k and k != sub_kind:
            continue
        d = _os.path.join(base, sub_dir)
        if not _os.path.isdir(d):
            continue
        for entry in _os.scandir(d):
            if entry.is_dir():
                try:
                    candidates.append(
                        (sub_kind, entry.path, entry.stat().st_mtime)
                    )
                except OSError:
                    continue
    candidates.sort(key=lambda t: t[2], reverse=True)
    candidates = candidates[:n]

    if not candidates:
        return f"No UC3 {k or 'bandwidth/kpi'} artifacts found under {base}."

    import datetime as _dt
    lines = [
        f"### UC3 artifacts (last {len(candidates)})",
        "",
        "| kind | run_dir | size | mtime |",
        "|------|---------|------|-------|",
    ]
    for sub_kind, path, mtime in candidates:
        size_b = 0
        try:
            for root, _dirs, files in _os.walk(path):
                for fn in files:
                    try:
                        size_b += _os.path.getsize(_os.path.join(root, fn))
                    except OSError:
                        pass
        except OSError:
            size_b = -1
        ts = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_kb = (size_b / 1024.0) if size_b >= 0 else -1
        rel = path.replace(base, "").lstrip("/") or path
        lines.append(
            f"| {sub_kind} | `{rel}` | {size_kb:.1f} KB | {ts} |"
        )
    lines.append("")
    lines.append(f"_Source: {base}_")
    return "\n".join(lines)


def _uc3_run_subprocess(kind: str, script: str, env_overrides: dict,
                        cli_args: list, requested_by: str,
                        primary_artifact: str = "") -> str:
    """Shared exec path for uc3_run_bandwidth_validation +
    uc3_run_kpi_experiments. Returns the markdown summary the chat
    surfaces. Logs every transition to uc3_runs."""
    import json as _json
    import os as _os
    import subprocess as _sp

    if not UC3_ALLOW_EXEC:
        return ("Refusing: UC3_ALLOW_EXEC is false on this server. "
                "Re-run with UC3_ALLOW_EXEC=true to spawn the subprocess "
                "(it will write a row to uc3_runs first).")
    if not UC3_BIN_DIR:
        return ("Refusing: UC3_BIN_DIR is not configured. Set it to the "
                "directory containing the platform/bin/ scripts.")
    script_path = _os.path.join(UC3_BIN_DIR.rstrip("/"), script)
    if not _os.path.isfile(script_path):
        return f"Refusing: script not found at {script_path}."
    if not _os.access(script_path, _os.X_OK):
        return f"Refusing: script {script_path} is not executable."

    # 1. Audit insert FIRST — never run without a row.
    args_logged = {"env": env_overrides, "cli_args": cli_args}
    run_id = _uc3_record_run_start(kind, args_logged, requested_by)
    if run_id < 0:
        return ("Refusing: could not write a uc3_runs audit row before "
                "spawning the subprocess. Failing closed.")

    # 2. Spawn.
    env = _os.environ.copy()
    for k, v in env_overrides.items():
        if v is not None:
            env[str(k)] = str(v)
    cmd = ["bash", script_path] + list(cli_args)
    status = "running"
    exit_code: int | None = None
    summary: dict | None = None
    artifact_path = ""
    notes_tail = ""
    try:
        proc = _sp.run(
            cmd, env=env, cwd=UC3_BIN_DIR.rstrip("/"),
            capture_output=True, text=True,
            timeout=_UC3_RUN_TIMEOUT_S,
        )
        exit_code = int(proc.returncode)
        # Tail of stderr/stdout for the audit row + LLM context.
        stderr_tail = (proc.stderr or "").splitlines()[-20:]
        stdout_tail = (proc.stdout or "").splitlines()[-10:]
        notes_tail = ("STDERR(tail):\n" + "\n".join(stderr_tail)
                      + "\n\nSTDOUT(tail):\n" + "\n".join(stdout_tail))[:60000]

        # 3. Try to locate + parse the primary JSON artifact.
        if primary_artifact and UC3_ARTIFACTS_DIR:
            cands = []
            for root, _dirs, files in _os.walk(
                    _os.path.join(UC3_ARTIFACTS_DIR, primary_artifact)):
                for fn in files:
                    if fn.endswith(".json"):
                        full = _os.path.join(root, fn)
                        cands.append((full, _os.path.getmtime(full)))
            if cands:
                cands.sort(key=lambda t: t[1], reverse=True)
                artifact_path = cands[0][0]
                try:
                    with open(artifact_path) as fh:
                        summary = _json.load(fh)
                except Exception:
                    summary = None

        status = "ok" if exit_code == 0 else "failed"
    except _sp.TimeoutExpired:
        status = "timeout"
        notes_tail = (f"timeout after {_UC3_RUN_TIMEOUT_S}s; "
                      f"increase UC3_RUN_TIMEOUT_S if this is expected.")
    except Exception as e:
        status = "failed"
        notes_tail = f"exception: {e}"

    # 4. Audit close.
    _uc3_record_run_finish(run_id, status, exit_code, summary,
                           artifact_path, notes_tail)

    # 5. Compose the chat-visible reply.
    lines = [
        f"### UC3 {kind} run #{run_id} → **{status}**",
        "",
        f"  exit_code:    `{exit_code}`",
        f"  status:       `{status}`",
        (f"  artifact:     `{artifact_path}`"
         if artifact_path else "  artifact:     _(none captured)_"),
    ]
    if summary and isinstance(summary, dict):
        keys = sorted(summary.keys())[:8]
        lines.append("")
        lines.append("**Summary keys**: " + ", ".join(f"`{k}`" for k in keys))
        # Inline a short verdict pull when present (bandwidth_validation.json
        # uses 'verdict'; uc3_kpi summary uses 'sla' / 'overall').
        for vkey in ("verdict", "overall", "sla", "result", "status"):
            if vkey in summary:
                lines.append(f"**{vkey}**: `{summary[vkey]}`")
                break
    if notes_tail and status != "ok":
        lines.append("")
        lines.append("```")
        lines.append(notes_tail[-2000:])
        lines.append("```")
    return "\n".join(lines)


@mcp.tool()
def uc3_run_bandwidth_validation(slice_name: str = "research",
                                dl_mbps: int = 2,
                                ul_mbps: int = 2,
                                duration_s: int = 15,
                                requested_by: str = None,
                                ctx: Context = None) -> str:
    """[KATANA] Run the upstream UC3 bandwidth-validation iperf3 sweep
    and return the verdict.

    Wraps run_uc3_bandwidth_validation.sh from the KatanaSliceManagerv2
    platform/bin/ tree. Drives iperf3 UL+DL through the slice's
    ogstun/uesimtun interfaces, applies TCOS QoS to enforce DL_MBPS /
    UL_MBPS, and emits KPI events to katana-pqc-kpi.

    Required env on this MCP server:
        UC3_ALLOW_EXEC=true       — gates the subprocess invocation
        UC3_BIN_DIR=...           — directory holding the .sh scripts
        UC3_ARTIFACTS_DIR=...     — where the script drops bandwidth_validation.json

    Args:
        slice_name   default 'research' (also valid: 'iot', 'video')
        dl_mbps      target downlink (1..200, default 2)
        ul_mbps      target uplink (1..200, default 2)
        duration_s   iperf3 test duration (5..120, default 15)
        requested_by short identifier of the caller — logged in uc3_runs.

    Audit semantics: a row is written to uc3_runs BEFORE the subprocess
    spawns. Status transitions running → ok | failed | timeout AFTER
    wait. Hard ceiling on subprocess wall-time is UC3_RUN_TIMEOUT_S.

    NOTE: this script SSHes from the MCP server to the testbed VMs and
    needs sshpass + VM_USER + VM_PASS in the environment. Configure
    those out-of-band — never via MCP env on the server.
    """
    if err := _check_ctx(ctx):
        return err
    s = (slice_name or "").strip().lower() or "research"
    if s not in ("research", "iot", "video"):
        return f"Unknown slice_name '{slice_name}'. Valid: research / iot / video."
    dl = max(1, min(int(dl_mbps or 2), 200))
    ul = max(1, min(int(ul_mbps or 2), 200))
    dur = max(5, min(int(duration_s or 15), 120))
    return _uc3_run_subprocess(
        kind="bandwidth_validation",
        script="run_uc3_bandwidth_validation.sh",
        env_overrides={
            "SLICE_NAME": s,
            "DL_MBPS":    str(dl),
            "UL_MBPS":    str(ul),
            "TEST_DURATION_SEC": str(dur),
        },
        cli_args=[],
        requested_by=requested_by or "chat",
        primary_artifact="uc3_bandwidth",
    )


@mcp.tool()
def uc3_run_kpi_experiments(modes: str = "classical,hybrid,pqc",
                           events_per_mode: int = 12,
                           with_local_blockchain: bool = False,
                           requested_by: str = None,
                           ctx: Context = None) -> str:
    """[KATANA] Generate the formal UC3 KPI dataset for the pilot
    deliverable.

    Wraps run_uc3_kpi_experiments.sh — emits synthetic KPI-UC3-* events
    in the requested modes, scores them against the SLA threshold file,
    and writes kpi_summary.json + kpi_summary.csv under
    UC3_ARTIFACTS_DIR/uc3_kpi/<ts>/.

    Required env (same as uc3_run_bandwidth_validation):
        UC3_ALLOW_EXEC=true
        UC3_BIN_DIR=...
        UC3_ARTIFACTS_DIR=...

    Args:
        modes                comma-separated subset of 'classical,hybrid,pqc'
        events_per_mode      1..200, default 12
        with_local_blockchain  if true, --with-local-blockchain is passed
                               (starts the local Anvil/Besu provider)
        requested_by         short identifier of the caller — logged.

    Audit semantics: same as uc3_run_bandwidth_validation.
    """
    if err := _check_ctx(ctx):
        return err
    valid_modes = {"classical", "hybrid", "pqc"}
    requested_modes = [m.strip().lower() for m in (modes or "").split(",") if m.strip()]
    requested_modes = [m for m in requested_modes if m in valid_modes]
    if not requested_modes:
        requested_modes = ["classical", "hybrid", "pqc"]
    n_per = max(1, min(int(events_per_mode or 12), 200))
    cli = ["--modes", ",".join(requested_modes),
           "--events-per-mode", str(n_per)]
    if with_local_blockchain:
        cli.append("--with-local-blockchain")
    return _uc3_run_subprocess(
        kind="kpi_experiments",
        script="run_uc3_kpi_experiments.sh",
        env_overrides={},
        cli_args=cli,
        requested_by=requested_by or "chat",
        primary_artifact="uc3_kpi",
    )


# ── Regulatory knowledge base — Phase 1 of the recommendation engine ─────
# Tools query the regulations / obligations / sector_profiles tables that
# are populated by etl/import_regulations.py (see etl/data/regulations.yaml
# for the curated dataset). All three are read-only and tagged
# [REGULATIONS] so the keyword classifier and the LLM system prompt can
# route to them.

_VALID_SECTORS = {
    "energy", "telco", "health", "financial",
    "cross-sector", "public",
}


def _load_sector_regs(cur, sector: str) -> tuple[list[str], dict]:
    """Resolve sector_id → (applicable_regulation_ids, profile dict).

    Returns ([], {}) if the sector is unknown.
    """
    import json as _json
    cur.execute(
        "SELECT applicable_regulations, priority_assets, typical_crypto_surface, "
        "       migration_complexity, long_lived_data, notes "
        "FROM sector_profiles WHERE sector_id = %s",
        (sector,),
    )
    row = cur.fetchone()
    if not row:
        return [], {}
    try:
        applicable = _json.loads(row[0]) if row[0] else []
    except Exception:
        applicable = []
    try:
        priority_assets = _json.loads(row[1]) if row[1] else []
    except Exception:
        priority_assets = []
    profile = {
        "priority_assets": priority_assets,
        "typical_crypto_surface": row[2],
        "migration_complexity": row[3],
        "long_lived_data": bool(row[4]) if row[4] is not None else None,
        "notes": row[5],
    }
    return applicable, profile


@mcp.tool()
def query_regulations_by_sector(sector: str, ctx: Context) -> str:
    """[REGULATIONS] List the regulations and concrete crypto obligations
    that apply to a given sector. Returns a markdown table per regulation
    plus its in-scope obligations (article_ref, type, deadline,
    crypto_relevance score, recommended NIST level + algorithm families,
    hybrid-required flag).

    Sector argument matches the sector_id in sector_profiles. Valid
    values: 'energy', 'telco', 'health', 'financial', 'cross-sector',
    'public'. Unknown sectors return an error explaining the options.

    Use this when the user asks 'what regulations apply to <sector>',
    'what are the obligations for X', 'which laws does my Y need to
    comply with'.
    """
    if err := _check_ctx(ctx):
        return err
    s = (sector or "").strip().lower()
    if not s:
        return "Refusing: sector argument is empty."
    if s not in _VALID_SECTORS:
        return (f"Unknown sector '{sector}'. Valid sectors: "
                + ", ".join(sorted(_VALID_SECTORS)))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            applicable, profile = _load_sector_regs(cur, s)
            if not applicable:
                conn.close()
                return f"No applicable regulations registered for sector '{s}'."

            placeholders = ",".join(["%s"] * len(applicable))
            cur.execute(
                f"SELECT id, name, long_name, kind, effective_date, pqc_deadline, "
                f"       summary, source_url "
                f"FROM regulations WHERE id IN ({placeholders}) ORDER BY id",
                applicable,
            )
            regs = list(cur.fetchall())

            cur.execute(
                f"SELECT regulation_id, article_ref, obligation_text, obligation_type, "
                f"       deadline, crypto_relevance, nist_level_min, "
                f"       recommended_families, hybrid_required "
                f"FROM obligations WHERE regulation_id IN ({placeholders}) "
                f"ORDER BY regulation_id, deadline IS NULL, deadline",
                applicable,
            )
            obls = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    import json as _json
    obls_by_reg: dict = {}
    for o in obls:
        obls_by_reg.setdefault(o[0], []).append(o)

    lines = [
        f"### Regulations applicable to sector `{s}`",
        "",
        f"Migration complexity: **{profile['migration_complexity']}/5**"
        f"  ·  Long-lived data: **{'yes' if profile['long_lived_data'] else 'no'}**",
        "",
    ]
    if profile.get("typical_crypto_surface"):
        lines.append(f"_{profile['typical_crypto_surface'].strip()}_")
        lines.append("")

    for r in regs:
        rid, rname, rlong, rkind, reff, rpqc, rsum, rurl = r
        lines.append(f"#### {rname} — {rlong or ''}")
        lines.append(f"_{rkind}_, effective `{reff or 'n/a'}`, "
                     f"PQC deadline **{rpqc or 'n/a'}**")
        if rsum:
            lines.append("")
            lines.append(rsum.strip())
        lines.append("")
        rg_obls = obls_by_reg.get(rid) or []
        if rg_obls:
            lines.append("| article | type | deadline | relev | NIST | recommended families | hybrid |")
            lines.append("|---------|------|----------|-------|------|----------------------|--------|")
            for o in rg_obls:
                fam_str = ""
                if o[7]:
                    try:
                        fams = _json.loads(o[7])
                        fam_str = ", ".join(fams) if fams else ""
                    except Exception:
                        fam_str = "?"
                lines.append(
                    f"| {o[1]} | {o[3]} | {o[4] or '—'} | {o[5]}/5 "
                    f"| L{o[6] if o[6] is not None else '—'} | {fam_str or '—'} "
                    f"| {'yes' if o[8] else 'no'} |"
                )
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
def get_compliance_gap(sector: str, current_algorithms: list,
                       ctx: Context) -> str:
    """[REGULATIONS] Compare a list of currently-deployed algorithm names
    against the recommended_families on every in-scope obligation for
    `sector`. Returns each non-compliant obligation with its deadline
    and the recommended NIST level + algorithm family to migrate toward.

    `current_algorithms` is a JSON array of strings, e.g.
    `["ECDHE-secp256r1", "ECDSA-P256", "AES-128-GCM"]`. Algorithm name
    matching is substring-based, case-insensitive — pass family names
    like 'rsa', 'ecdh', 'ecdsa', or 'classical' if you don't know
    precise deployed identifiers.

    Use when the user asks 'where am I non-compliant', 'what's the
    compliance gap', 'what crypto do I need to migrate'.
    """
    if err := _check_ctx(ctx):
        return err
    s = (sector or "").strip().lower()
    if not s:
        return "Refusing: sector argument is empty."
    if s not in _VALID_SECTORS:
        return (f"Unknown sector '{sector}'. Valid sectors: "
                + ", ".join(sorted(_VALID_SECTORS)))
    if not isinstance(current_algorithms, list) or not current_algorithms:
        return ("Refusing: current_algorithms must be a non-empty list of "
                "algorithm name strings.")

    deployed = [str(a).lower().strip() for a in current_algorithms if str(a).strip()]
    if not deployed:
        return "Refusing: current_algorithms list is empty after normalization."

    try:
        conn = _connect()
        with conn.cursor() as cur:
            applicable, _profile = _load_sector_regs(cur, s)
            if not applicable:
                conn.close()
                return f"No applicable regulations registered for sector '{s}'."

            placeholders = ",".join(["%s"] * len(applicable))
            cur.execute(
                f"SELECT r.name, o.article_ref, o.obligation_text, o.obligation_type, "
                f"       o.deadline, o.crypto_relevance, o.nist_level_min, "
                f"       o.recommended_families, o.hybrid_required, o.algorithm_notes "
                f"FROM obligations o JOIN regulations r ON r.id = o.regulation_id "
                f"WHERE o.regulation_id IN ({placeholders}) "
                f"  AND o.crypto_relevance >= 3 "
                f"  AND o.recommended_families IS NOT NULL "
                f"ORDER BY o.deadline IS NULL, o.deadline",
                applicable,
            )
            obls = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    import json as _json
    pqc_signals = ["ml-kem", "ml-dsa", "slh-dsa", "kyber", "dilithium",
                   "sphincs", "frodo", "bike", "hqc", "falcon"]
    has_any_pqc = any(any(sig in dep for sig in pqc_signals) for dep in deployed)

    gaps: list = []
    compliant: list = []
    for o in obls:
        try:
            families = _json.loads(o[7]) if o[7] else []
        except Exception:
            families = []
        matched = False
        for fam in families:
            fam_l = str(fam).lower()
            for dep in deployed:
                if fam_l in dep or dep in fam_l:
                    matched = True
                    break
            if matched:
                break
        (compliant if matched else gaps).append((o, families))

    lines = [
        f"### Compliance gap for sector `{s}`",
        "",
        f"Deployed algorithms: `{', '.join(current_algorithms)}`",
        f"Has any PQC family in deployed list: **{'yes' if has_any_pqc else 'no'}**",
        "",
        f"**Gaps:** {len(gaps)} obligation(s) · "
        f"**Compliant:** {len(compliant)} obligation(s)",
        "",
    ]
    if gaps:
        lines.append("| regulation | article | type | deadline | relev | NIST | hybrid | recommended |")
        lines.append("|------------|---------|------|----------|-------|------|--------|-------------|")
        for o, fams in gaps:
            fam_s = ", ".join(str(f) for f in fams) if fams else "—"
            lines.append(
                f"| {o[0]} | {o[1]} | {o[3]} | {o[4] or '—'} | {o[5]}/5 "
                f"| L{o[6] if o[6] is not None else '—'} "
                f"| {'yes' if o[8] else 'no'} | {fam_s} |"
            )
        lines.append("")
    else:
        lines.append("_No gaps — every in-scope obligation has at least one "
                     "recommended family represented in the deployed list._")
    return "\n".join(lines)


@mcp.tool()
def get_pqc_timeline(sector: str, ctx: Context) -> str:
    """[REGULATIONS] Return the chronological migration timeline for a
    sector: every in-scope obligation with a non-null deadline, sorted
    earliest first, plus the regulation-level pqc_deadlines.

    Useful for project planning ('what's the next compliance milestone',
    'when does NIS2 critical-infra cutoff hit', 'show me everything
    due before 2030').
    """
    if err := _check_ctx(ctx):
        return err
    s = (sector or "").strip().lower()
    if not s:
        return "Refusing: sector argument is empty."
    if s not in _VALID_SECTORS:
        return (f"Unknown sector '{sector}'. Valid sectors: "
                + ", ".join(sorted(_VALID_SECTORS)))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            applicable, _profile = _load_sector_regs(cur, s)
            if not applicable:
                conn.close()
                return f"No applicable regulations registered for sector '{s}'."

            placeholders = ",".join(["%s"] * len(applicable))
            cur.execute(
                f"SELECT name, pqc_deadline FROM regulations "
                f"WHERE id IN ({placeholders}) AND pqc_deadline IS NOT NULL "
                f"ORDER BY pqc_deadline",
                applicable,
            )
            reg_deadlines = list(cur.fetchall())

            cur.execute(
                f"SELECT r.name, o.article_ref, o.obligation_text, o.deadline, "
                f"       o.obligation_type, o.crypto_relevance "
                f"FROM obligations o JOIN regulations r ON r.id = o.regulation_id "
                f"WHERE o.regulation_id IN ({placeholders}) "
                f"  AND o.deadline IS NOT NULL "
                f"ORDER BY o.deadline",
                applicable,
            )
            obls = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    lines = [f"### PQC migration timeline — sector `{s}`", ""]
    if reg_deadlines:
        lines.append("**Regulation-level PQC deadlines:**")
        lines.append("")
        for name, deadline in reg_deadlines:
            lines.append(f"  - `{deadline}` — {name}")
        lines.append("")
    if obls:
        lines.append("**Obligation-level deadlines:**")
        lines.append("")
        lines.append("| deadline | regulation | article | type | relev | obligation |")
        lines.append("|----------|------------|---------|------|-------|------------|")
        for r_name, art, text, dl, otype, rel in obls:
            t = (text or "").replace("\n", " ").strip()
            if len(t) > 70:
                t = t[:67] + "..."
            lines.append(f"| {dl} | {r_name} | {art} | {otype} | {rel}/5 | {t} |")
        lines.append("")
    if not (reg_deadlines or obls):
        lines.append("_(No deadlines registered for this sector.)_")
    return "\n".join(lines)


# ── Asset inventory / CBOM tools — Phase 1 of recommendation engine ──────
# Read-only views over cbom_documents / crypto_assets / compliance_results.
# When the tables are empty (typical before CBOMkit lands in Phase 3 on
# <cbomkit-host>), tools return a friendly "no CBOMs yet" message rather
# than raising — the chat agent can still reason about regulations + perf.

_VALID_ASSET_TYPES = {
    "algorithm", "certificate", "key", "protocol", "library",
}
_VALID_RISK_LEVELS = {
    "vulnerable", "partial", "safe", "unknown",
}


@mcp.tool()
def query_crypto_inventory(sector: str = None, asset_type: str = None,
                           risk_level: str = None,
                           ctx: Context = None) -> str:
    """[INVENTORY] Query the cryptographic asset inventory built from
    CBOM scans. Returns a markdown table of crypto assets matching the
    filters.

    Filters (all optional):
      sector       'energy' | 'telco' | 'health' | 'financial' | 'cross-sector' | 'public'
                   Pass 'all', 'any', '*', or '' to span all sectors.
      asset_type   'algorithm' | 'certificate' | 'key' | 'protocol' | 'library'
                   Pass 'all', 'any', '*', or '' for no filter.
      risk_level   'vulnerable' | 'partial' | 'safe' | 'unknown'
                   Pass 'all', 'any', '*', or '' for no filter.

    Use when the user asks 'what crypto is deployed', 'show me our
    vulnerable algorithms', 'list certificates in the health sector'.
    """
    if err := _check_ctx(ctx):
        return err

    # 'all' / 'any' / '*' / empty = no filter
    _PASSTHROUGH = {"", "all", "any", "*", "none", "null"}
    def _norm(x): return None if (x or "").strip().lower() in _PASSTHROUGH else (x or "").strip().lower()

    s  = _norm(sector)
    at = _norm(asset_type)
    rl = _norm(risk_level)

    if at and at not in _VALID_ASSET_TYPES:
        return (f"Unknown asset_type '{asset_type}'. Valid: "
                + ", ".join(sorted(_VALID_ASSET_TYPES))
                + ". Pass 'all' for no filter.")
    if rl and rl not in _VALID_RISK_LEVELS:
        return (f"Unknown risk_level '{risk_level}'. Valid: "
                + ", ".join(sorted(_VALID_RISK_LEVELS))
                + ". Pass 'all' for no filter.")

    clauses: list[str] = []
    params: list = []
    if s:
        clauses.append("a.sector = %s")
        params.append(s)
    if at:
        clauses.append("a.asset_type = %s")
        params.append(at)
    if rl:
        clauses.append("a.quantum_risk_level = %s")
        params.append(rl)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cbom_documents")
            (n_docs,) = cur.fetchone()
            cur.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT a.name) FROM crypto_assets a"
                f"{where}",
                params,
            )
            (n_match, n_distinct) = cur.fetchone()
            # Insight view: deduplicate by (risk, type, name) and rank by
            # how many times each asset appears, plus how many distinct
            # components it touches. This turns "30 near-identical ECDSA
            # rows at different cert paths" into "ECDSA · 5181 occurrences
            # across 43 components" — the actual signal the operator needs.
            cur.execute(
                f"SELECT a.quantum_risk_level, a.asset_type, a.name, "
                f"       COUNT(*) AS occurrences, "
                f"       COUNT(DISTINCT a.component_path) AS components "
                f"FROM crypto_assets a"
                f"{where} "
                f"GROUP BY a.quantum_risk_level, a.asset_type, a.name "
                f"ORDER BY FIELD(a.quantum_risk_level,'vulnerable','partial','safe','unknown'), "
                f"         occurrences DESC "
                f"LIMIT 20",
                params,
            )
            rows = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    if n_docs == 0:
        return ("No CBOM documents have been ingested yet. POST a CycloneDX "
                "JSON to the asset_inventory API (Phase 3 deployment on "
                "`<cbomkit-host>`), or run a CBOMkit-theia scan against the "
                "testbed Docker images and import the result.")
    if not rows:
        active_filters = []
        if s:  active_filters.append(f"sector={s}")
        if at: active_filters.append(f"asset_type={at}")
        if rl: active_filters.append(f"risk_level={rl}")
        f_str = " (" + ", ".join(active_filters) + ")" if active_filters else ""
        return (f"No assets match{f_str}. {n_docs} CBOM document(s) loaded; "
                f"try widening the filter (use 'all' to disable a filter).")

    active_filters = []
    if s:  active_filters.append(f"sector={s}")
    if at: active_filters.append(f"asset_type={at}")
    if rl: active_filters.append(f"risk_level={rl}")
    scope = (" · " + ", ".join(active_filters)) if active_filters else " · all sectors"
    lines = [
        f"### Crypto inventory — what's actually deployed{scope}",
        "",
        f"**{n_distinct} distinct cryptographic assets** across **{n_match:,} "
        f"occurrences** ({n_docs} CBOM doc(s)). Ranked by prevalence:",
        "",
        "| risk | type | asset | occurrences | components |",
        "|------|------|-------|------------:|-----------:|",
    ]
    for risk, atype, nm, occ, comps in rows:
        nm_show = (nm or "—").replace("|", "/")
        if len(nm_show) > 46:
            nm_show = nm_show[:45] + "…"
        lines.append(f"| {risk} | {atype} | {nm_show} | {occ:,} | {comps:,} |")
    if n_distinct > len(rows):
        lines.append("")
        lines.append(f"_Top {len(rows)} of {n_distinct} distinct assets shown "
                     f"(by occurrence). Use get_quantum_risk_summary for the "
                     f"posture read, or narrow with risk_level / asset_type._")
    return "\n".join(lines)


@mcp.tool()
def get_quantum_risk_summary(sector: str = None, ctx: Context = None) -> str:
    """[INVENTORY] Aggregate the crypto-asset inventory into
    quantum-vulnerability counts. Returns a bucketed summary
    (vulnerable / partial / safe / unknown), optionally filtered by
    sector. Useful for executive snapshots: 'how exposed are we to
    Q-day across sector X?'

    Pass sector='all' (or omit it entirely) for a cross-sector summary.
    """
    if err := _check_ctx(ctx):
        return err
    _PASSTHROUGH = {"", "all", "any", "*", "none", "null"}
    s = None if (sector or "").strip().lower() in _PASSTHROUGH \
        else (sector or "").strip().lower()

    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cbom_documents")
            (n_docs,) = cur.fetchone()
            if s:
                cur.execute(
                    "SELECT quantum_risk_level, asset_type, COUNT(*) "
                    "FROM crypto_assets WHERE sector = %s "
                    "GROUP BY quantum_risk_level, asset_type "
                    "ORDER BY FIELD(quantum_risk_level, 'vulnerable', 'partial', 'safe', 'unknown'), asset_type",
                    (s,),
                )
            else:
                cur.execute(
                    "SELECT quantum_risk_level, asset_type, COUNT(*) "
                    "FROM crypto_assets "
                    "GROUP BY quantum_risk_level, asset_type "
                    "ORDER BY FIELD(quantum_risk_level, 'vulnerable', 'partial', 'safe', 'unknown'), asset_type"
                )
            rows = list(cur.fetchall())
            cur.execute(
                "SELECT quantum_risk_level, COUNT(*) FROM crypto_assets "
                + ("WHERE sector = %s " if s else "")
                + "GROUP BY quantum_risk_level "
                + "ORDER BY FIELD(quantum_risk_level, 'vulnerable', 'partial', 'safe', 'unknown')",
                (s,) if s else (),
            )
            totals = list(cur.fetchall())
            # Top quantum-vulnerable families — the actionable "what to
            # migrate first" signal, deduplicated and ranked by prevalence.
            cur.execute(
                "SELECT name, COUNT(*) AS n FROM crypto_assets "
                "WHERE quantum_risk_level = 'vulnerable' "
                + ("AND sector = %s " if s else "")
                + "GROUP BY name ORDER BY n DESC LIMIT 5",
                (s,) if s else (),
            )
            top_vuln = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    if n_docs == 0:
        return ("No CBOM documents loaded yet — risk summary unavailable. "
                "See query_crypto_inventory for ingestion guidance.")
    if not totals:
        return (f"No crypto assets found"
                + (f" for sector '{s}'." if s else ".")
                + " Are CBOMs scoped to other sectors?")

    counts = {risk: n for risk, n in totals}
    grand = sum(counts.values()) or 1
    vuln = counts.get("vulnerable", 0)
    safe = counts.get("safe", 0)
    partial = counts.get("partial", 0)
    unknown = counts.get("unknown", 0)
    pct = lambda x: f"{100*x/grand:.0f}%"

    # Where the vulnerability concentrates, by asset type.
    vuln_by_type = {atype: n for risk, atype, n in rows if risk == "vulnerable"}
    conc = ", ".join(f"{n:,} {atype}{'s' if n != 1 else ''}"
                     for atype, n in sorted(vuln_by_type.items(),
                                            key=lambda kv: -kv[1])) or "—"
    fams = " · ".join(f"{nm} ({n:,})" for nm, n in top_vuln) or "—"

    scope = f" · sector `{s}`" if s else " · all sectors"
    lines = [
        f"### Quantum-risk posture{scope}",
        "",
        f"**{grand:,} cryptographic assets scanned** across {n_docs} CBOM "
        f"document(s). Quantum exposure:",
        "",
        f"- 🔴 **{pct(vuln)} quantum-vulnerable** ({vuln:,}) — broken by Shor's algorithm",
        f"- 🟢 {pct(safe)} quantum-safe ({safe:,})"
        + (f"  ·  🟡 {pct(partial)} hybrid/partial ({partial:,})" if partial else "")
        + f"  ·  ⚪ {pct(unknown)} unclassified ({unknown:,})",
        "",
        f"**Risk concentrates in:** {conc}.",
        f"**Top vulnerable families (migrate first):** {fams}.",
        "",
        "_Interpretation: most vulnerable instances are RSA/ECDSA in X.509 "
        "trust stores — broad but mechanical to rotate. Prioritise the "
        "asymmetric crypto in active TLS/IKE configuration, and treat "
        "`unknown` (mostly certificates whose key alg isn't exposed) as "
        "review-required, not safe. Long-lived data is exposed to "
        "harvest-now-decrypt-later today._",
        "",
        "**By risk level:**",
        "",
        "| risk | assets | share |",
        "|------|-------:|------:|",
    ]
    for risk, n in totals:
        lines.append(f"| {risk} | {n:,} | {pct(n)} |")
    lines.append(f"| **total** | **{grand:,}** | **100%** |")
    return "\n".join(lines)


@mcp.tool()
def get_compliance_report(cbom_doc_id: int, ctx: Context) -> str:
    """[INVENTORY] Return the CBOMkit-themis compliance check results
    for a specific CBOM document. Returns the policy name, pass/fail
    counts, and a short summary of findings. The CBOM doc id is the
    integer surfaced by query_crypto_inventory in the 'source' column."""
    if err := _check_ctx(ctx):
        return err
    try:
        cbom_id = int(cbom_doc_id)
    except (TypeError, ValueError):
        return f"Refusing: cbom_doc_id must be an integer, got {cbom_doc_id!r}."

    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_system, scan_date, sector, scan_tool "
                "FROM cbom_documents WHERE id = %s",
                (cbom_id,),
            )
            doc = cur.fetchone()
            if not doc:
                conn.close()
                return f"No CBOM document found with id={cbom_id}."

            cur.execute(
                "SELECT policy_name, pass_count, fail_count, findings_json, scan_date "
                "FROM compliance_results WHERE cbom_doc_id = %s ORDER BY scan_date DESC",
                (cbom_id,),
            )
            results = list(cur.fetchall())
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    src, scan_date, sector, tool = doc
    lines = [
        f"### Compliance report — CBOM document #{cbom_id}",
        "",
        f"Source: `{src}`  ·  Scanned by: `{tool}`  ·  Sector: `{sector or 'n/a'}`  ·  Scan date: {scan_date}",
        "",
    ]
    if not results:
        lines.append("_No compliance checks recorded for this CBOM yet._")
        lines.append("Run `cbomkit-themis` against the document with one of the")
        lines.append("registered policies to produce a report.")
        return "\n".join(lines)

    # Policy table with compliance %. The percentage is the headline
    # number — it's what an operator wants to see first.
    lines.append("| policy | pass | fail | total | compliance | scanned |")
    lines.append("|--------|-----:|-----:|------:|-----------:|---------|")
    for policy, pc, fc, _findings, sd in results:
        total = int(pc) + int(fc)
        pct = (int(pc) / total * 100) if total else 0.0
        lines.append(f"| {policy} | {pc} | {fc} | {total} | {pct:.1f}% | {sd} |")
    lines.append("")

    # Findings: bucket by (rule, severity) so a single rule firing 4000
    # times doesn't drown out 4 distinct low-frequency rules. Reported
    # 2026-05-20: the previous "top 5 raw findings" view returned 4
    # identical `manual-review-required` rows because they all happened
    # to be the most recent, even though the underlying CBOM had 8 000+
    # findings across multiple rules.
    import json as _json
    from collections import Counter, defaultdict
    most_recent = results[0]
    findings_raw = most_recent[3]
    if not findings_raw:
        return "\n".join(lines)
    try:
        findings = _json.loads(findings_raw)
    except Exception:
        return "\n".join(lines)
    if not isinstance(findings, list) or not findings:
        return "\n".join(lines)

    # Bucket: (rule, severity) → list of distinct message strings.
    # We track distinct messages (deduplicated) so the "example" column
    # is informative even when the same rule fires many times on
    # similar inputs.
    buckets: dict = defaultdict(list)
    severity_counts: Counter = Counter()
    seen_messages: dict = defaultdict(set)   # (rule, sev) → set of msgs
    for f in findings:
        if not isinstance(f, dict):
            continue
        rule = f.get("rule") or f.get("ruleId") or "?"
        sev = (f.get("severity") or "info").strip().lower()
        msg = (f.get("message") or "").strip()
        key = (rule, sev)
        buckets[key].append(msg)
        severity_counts[sev] += 1
        if msg and msg not in seen_messages[key]:
            seen_messages[key].add(msg)

    # Severity priority for sorting. Anything we don't recognise lands
    # after `info` so unknown-severity rules don't accidentally rise.
    SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    def sev_rank(sev: str) -> int:
        return SEV_ORDER.get(sev, 5)

    lines.append(f"**Findings ({len(findings)} total, "
                 f"{len(buckets)} distinct rule×severity buckets):**")
    sev_summary_parts = [
        f"{sev}={severity_counts[sev]}"
        for sev in ("critical", "high", "medium", "low", "info")
        if severity_counts.get(sev)
    ]
    if sev_summary_parts:
        lines.append("By severity: " + "  ·  ".join(sev_summary_parts))
    lines.append("")

    # Ranked bucket table — sort severity first (worst at top), then
    # by count (most-firing first). Cap at 10 buckets to keep the
    # response readable; remaining buckets get a summary footer.
    ranked = sorted(
        buckets.items(),
        key=lambda kv: (sev_rank(kv[0][1]), -len(kv[1])),
    )
    head, tail = ranked[:10], ranked[10:]

    lines.append("| count | severity | rule | example message |")
    lines.append("|------:|----------|------|-----------------|")
    for (rule, sev), msgs in head:
        example = ""
        if msgs:
            distinct = list(seen_messages[(rule, sev)])
            example = (distinct[0] if distinct else msgs[0])[:100]
        lines.append(f"| {len(msgs)} | {sev} | `{rule}` | {_escape_pipes(example)} |")

    if tail:
        tail_total = sum(len(msgs) for _, msgs in tail)
        lines.append("")
        lines.append(
            f"_{len(tail)} additional rule×severity bucket(s) "
            f"covering {tail_total} more findings._"
        )

    return "\n".join(lines)


def _escape_pipes(s: str) -> str:
    """Cell-content escape for markdown tables — pipes inside the
    message would otherwise break the column layout."""
    return (s or "").replace("|", "\\|").replace("\n", " ")


# Sector-specific narrative templates (Phase 4). Used by
# recommend_migration_priority to produce sector-aware roadmaps
# instead of a one-size-fits-all flat list.
_SECTOR_TEMPLATES = {
    "energy": {
        "intro": (
            "**Energy / smart-grid migration plan.** Field-deployed "
            "devices have 5-20 year firmware cycles and OT downtime is "
            "expensive — phase the rollout. Recommended sequence: "
            "(1) SCADA masters first (highest blast radius, smallest "
            "fleet); (2) RTU/IED firmware bumps for IEC 62351-3 mTLS "
            "and 62351-9 key management; (3) field meters last, when "
            "vendor firmware lands. Bias toward NIST L3 hybrid TLS "
            "(X25519+ML-KEM-768)."
        ),
        "anchor_standards": "IEC 62351-3 / -5 / -9, IEEE 2030.5, DLMS Blue Book",
    },
    "telco": {
        "intro": (
            "**Telco / 5G NF migration plan.** Containerized NFs allow "
            "a CI/CD rollout — much friendlier than energy or health. "
            "Recommended sequence: (1) hybrid TLS on SBI inter-NF "
            "(measured ~48-49 ms PQC overhead in free5GC sidecar); "
            "(2) IPsec on N2/N3 via StrongSwan plugin-oqs (already in "
            "the QUJATA test stack); (3) SUCI scheme replacement when "
            "3GPP SA3 R20 ships; (4) eSIM PKI roots last (longest CA "
            "validity, SLH-DSA candidate)."
        ),
        "anchor_standards": "3GPP TS 33.501 §9 §13, GSMA SGP.22, RFC 9242",
    },
    "health": {
        "intro": (
            "**Health crypto-agility roadmap.** Long-lived patient "
            "records make harvest-now-decrypt-later (HNDL) acute — "
            "this drives an aggressive transit-encryption migration "
            "regardless of regulatory deadlines. Recommended sequence: "
            "(1) EHR-DB KEK rewrap immediately (AES-256 stays, ML-KEM "
            "wraps); (2) FHIR REST + HL7 v2 MLLP transit (small JSON, "
            "high frequency — handshake overhead matters); (3) DICOM "
            "TLS for imaging (large payloads, overhead amortizes); "
            "(4) prescription/health-record signatures last — prefer "
            "SLH-DSA for 10-50 year evidence durability over ML-DSA."
        ),
        "anchor_standards": "HIPAA §164.312, EHDS Art. 28+50, HL7 FHIR R5, DICOM PS3.15",
    },
    "financial": {
        "intro": (
            "**Financial / DORA migration plan.** DORA mandates an "
            "annual residual-risk re-assessment — the CAM agility "
            "loop fits this cadence naturally. Recommended sequence: "
            "(1) HSM-backed TLS terminators (highest assurance, most "
            "audit scrutiny); (2) transaction signers (SLH-DSA for "
            "long audit trails, ML-DSA for speed-critical paths); "
            "(3) SWIFT messaging via the SWIFT CSCF working group "
            "transition; (4) eIDAS qualified-trust-service issuance "
            "last (longest cert validity, SLH-DSA mandatory)."
        ),
        "anchor_standards": "DORA Art. 9 + RTS Art. 6, eIDAS 2.0 Art. 24, PCI-DSS v4 §4.2",
    },
    "cross-sector": {
        "intro": (
            "**Cross-sector migration plan.** Aggregate view across "
            "all four pilot sectors — use this to spot common "
            "primitives (e.g. RSA-2048 cert chains, ECDHE secp256r1) "
            "where one migration unblocks several sector roadmaps."
        ),
        "anchor_standards": "Union of NIS2, DORA, eIDAS, IEC 62351, 3GPP, HIPAA, EHDS, FIPS 203/204/205",
    },
    "public": {
        "intro": (
            "**Public-sector migration plan.** EU PQC Coordinated "
            "Roadmap milestone 1 (2026-12-31) requires national "
            "transition strategies; milestone 2 (2030-12-31) targets "
            "critical-infra. Bias toward EUCC-certified products at "
            "substantial/high assurance and ENISA-recommended families."
        ),
        "anchor_standards": "EU PQC Roadmap, EU CSA + EUCC, eIDAS 2.0",
    },
}


def _urgency_score(deadline) -> int:
    """Translate a deadline (date or None) into a 0-100 urgency score.

    100 = past due / under 1 year out; 50 = 1-3 yrs; 20 = 3-5 yrs;
    5 = 5+ yrs; 0 = no deadline. Linear-ish thresholds chosen so the
    score discriminates clearly across the EU PQC Roadmap milestones
    (2026 / 2030 / 2035) when run from a 2026 vantage point.
    """
    if deadline is None:
        return 0
    import datetime as _dt
    today = _dt.date.today()
    try:
        if hasattr(deadline, "date"):
            d = deadline.date()
        else:
            d = deadline
        days = (d - today).days
    except Exception:
        return 0
    if days < 365:
        return 100
    if days < 1095:        # 3 years
        return 50
    if days < 1825:        # 5 years
        return 20
    return 5


def _risk_score(risk_level: str) -> int:
    """vulnerable=100, partial=50, unknown=25, safe=0."""
    return {
        "vulnerable": 100,
        "partial":    50,
        "unknown":    25,
        "safe":       0,
    }.get((risk_level or "").lower(), 0)


@mcp.tool()
def recommend_migration_priority(sector: str, top_n: int = 20,
                                 ctx: Context = None) -> str:
    """[INVENTORY] Cross-category synthesis: combines the asset
    inventory (what's deployed + quantum risk), the regulatory
    obligations (what must change + by when), the sector profile
    (migration complexity + harvest-now-decrypt-later flag), and the
    performance_test benchmarks (which PQC family is fastest / lowest
    energy) into a *weighted-scored* ranked migration priority list.

    Each in-scope vulnerable / partial asset gets a score:

        score = urgency × (risk / 100) × complexity_mod × hndl_mod

    where:
        urgency       0-100 from earliest matching obligation deadline
                      (100 if <1 year, 50 if <3 yrs, 20 if <5 yrs, 5
                      otherwise, 0 if no deadline)
        risk          100 vulnerable, 50 partial, 25 unknown, 0 safe
        complexity_mod 1.5× when sector_profile.migration_complexity ≥ 4
                       (high complexity → start sooner so the long
                       fleet rollout fits the deadline)
        hndl_mod      1.3× when sector_profile.long_lived_data is true
                      (harvest-now-decrypt-later urgency boost)

    The output table includes the score breakdown so a user can see
    why a row is ranked where it is. A sector-specific narrative
    template is prepended for energy / telco / health / financial /
    cross-sector / public sectors with the recommended migration
    sequence.

    Args:
        sector  energy / telco / health / financial / cross-sector / public
        top_n   how many highest-scored assets to show (default 20, cap 50)

    Falls back to sector_profile.priority_assets when the CBOM tables
    are empty.
    """
    if err := _check_ctx(ctx):
        return err
    s = (sector or "").strip().lower()
    if not s:
        return "Refusing: sector argument is empty."
    if s not in _VALID_SECTORS:
        return (f"Unknown sector '{sector}'. Valid sectors: "
                + ", ".join(sorted(_VALID_SECTORS)))
    n = max(1, min(int(top_n or 20), 50))

    import json as _json

    try:
        conn = _connect()
        with conn.cursor() as cur:
            if s in ("cross-sector", "public"):
                cur.execute(
                    "SELECT DISTINCT JSON_UNQUOTE(jt.reg_id) "
                    "FROM sector_profiles, "
                    "     JSON_TABLE(applicable_regulations, '$[*]' "
                    "                COLUMNS (reg_id VARCHAR(64) PATH '$')) jt"
                )
                applicable = [r[0] for r in cur.fetchall() if r[0]]
                # Pseudo-profile: when querying cross-sector we want
                # complexity_mod and hndl_mod off (1.0×) since they're
                # sector-specific by construction.
                profile = {
                    "priority_assets": [],
                    "typical_crypto_surface": "Union across all sector profiles.",
                    "migration_complexity": None,
                    "long_lived_data": None,
                    "notes": None,
                }
            else:
                applicable, profile = _load_sector_regs(cur, s)
            if not applicable:
                conn.close()
                return f"No applicable regulations registered for sector '{s}'."

            # 1. Pull obligations: each carries (deadline, recommended_families,
            #    nist_level_min, regulation_name). We need them per-family so
            #    each asset can find its own earliest matching deadline.
            ph = ",".join(["%s"] * len(applicable))
            cur.execute(
                f"SELECT r.name, o.deadline, o.nist_level_min, "
                f"       o.recommended_families, o.hybrid_required, "
                f"       o.crypto_relevance "
                f"FROM obligations o JOIN regulations r ON r.id = o.regulation_id "
                f"WHERE o.regulation_id IN ({ph}) "
                f"  AND o.recommended_families IS NOT NULL "
                f"ORDER BY o.deadline IS NULL, o.deadline",
                applicable,
            )
            obligations: list = []
            for row in cur.fetchall():
                rname, deadline, nl, rf, hyb, rel = row
                try:
                    fams = [str(f).upper() for f in (_json.loads(rf) or [])]
                except Exception:
                    fams = []
                obligations.append({
                    "regulation": rname,
                    "deadline":   deadline,
                    "nist_level": nl,
                    "families":   set(fams),
                    "hybrid":     bool(hyb) if hyb is not None else None,
                    "relevance":  rel,
                })

            # earliest = obligation with the soonest deadline (for the banner).
            with_deadline = [o for o in obligations if o["deadline"] is not None]
            earliest = min(with_deadline, key=lambda o: o["deadline"]) \
                       if with_deadline else None

            # 2. Inventory: vulnerable + partial assets in scope.
            cur.execute(
                "SELECT a.asset_type, a.name, a.quantum_risk_level "
                "FROM crypto_assets a "
                "WHERE (a.sector = %s OR a.sector IS NULL) "
                "  AND a.quantum_risk_level IN ('vulnerable', 'partial', 'unknown') "
                "ORDER BY FIELD(a.quantum_risk_level, 'vulnerable', 'partial', 'unknown'), a.name "
                "LIMIT 500",
                (s,),
            )
            assets_raw = list(cur.fetchall())

            # 3. Union of all recommended families (used for fallback +
            #    perf candidate lookup).
            target_families: set = set()
            for o in obligations:
                target_families.update(o["families"])

            # 4. Top-1 perf candidate per family at L3.
            family_perf: dict = {}
            for fam in target_families:
                fam_low = fam.lower().replace("-", "")
                cur.execute(
                    "SELECT algorithm_name, ROUND(AVG(duration), 4), "
                    "       ROUND(AVG(power_watts*duration), 6) "
                    "FROM performance_test "
                    "WHERE LOWER(REPLACE(algorithm_name, '-', '')) LIKE %s "
                    "  AND security_level = 3 "
                    "GROUP BY algorithm_name "
                    "ORDER BY AVG(duration) ASC LIMIT 1",
                    (f"%{fam_low}%",),
                )
                row = cur.fetchone()
                if row:
                    family_perf[fam] = (row[0], row[1], row[2])
        conn.close()
    except pymysql.Error as e:
        return f"Database error: {e}"

    # ── Score each asset ─────────────────────────────────────────────
    complexity = profile.get("migration_complexity") or 0
    long_lived = bool(profile.get("long_lived_data"))
    complexity_mod = 1.5 if complexity and complexity >= 4 else 1.0
    hndl_mod       = 1.3 if long_lived else 1.0

    def _suggested_family(asset_type: str) -> str:
        # cert/key → signature family; protocol/algorithm/library → KEM family.
        pref = (["ML-DSA", "SLH-DSA"]
                if (asset_type or "").lower() in ("certificate", "key")
                else ["ML-KEM", "ML-DSA"])
        for f in pref:
            if f in target_families:
                return f
        return next(iter(target_families), "ML-KEM")

    def _earliest_obligation_for(family: str):
        """Return (deadline, regulation, nist_level) for the soonest
        obligation that recommends `family`. None if no match."""
        candidates = [o for o in obligations
                      if o["deadline"] is not None
                      and family.upper() in o["families"]]
        if not candidates:
            return None
        e = min(candidates, key=lambda o: o["deadline"])
        return (e["deadline"], e["regulation"], e["nist_level"])

    scored: list = []
    for atype, aname, risk in assets_raw:
        fam = _suggested_family(atype)
        match = _earliest_obligation_for(fam)
        deadline = match[0] if match else None
        urg = _urgency_score(deadline)
        rsk = _risk_score(risk)
        score = urg * (rsk / 100.0) * complexity_mod * hndl_mod
        scored.append({
            "asset_type": atype, "name": aname, "risk": risk,
            "family":     fam,
            "urgency":    urg,
            "risk_n":     rsk,
            "deadline":   deadline,
            "regulation": (match[1] if match else None),
            "nist_level": (match[2] if match else None),
            "score":      score,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Render ───────────────────────────────────────────────────────
    template = _SECTOR_TEMPLATES.get(s, {})
    lines = [f"### Migration priority — sector `{s}`", ""]

    if template.get("intro"):
        lines.append(template["intro"])
        lines.append("")
        if template.get("anchor_standards"):
            lines.append(f"_Anchor standards: {template['anchor_standards']}_")
            lines.append("")

    if earliest:
        nist_str = (f"NIST L{earliest['nist_level']}" if earliest["nist_level"]
                    else "NIST L?")
        lines.append(f"⚠️  **Earliest binding deadline: `{earliest['deadline']}`** "
                     f"({earliest['regulation']}, {nist_str})")
        lines.append("")

    lines.append(f"_Score = urgency × (risk/100) × complexity_mod × hndl_mod_  ·  "
                 f"complexity_mod=**{complexity_mod}** "
                 f"(sector complexity={complexity or 'n/a'}/5)  ·  "
                 f"hndl_mod=**{hndl_mod}** (long-lived data: "
                 f"{'yes' if long_lived else 'no'})")
    lines.append("")

    # Top-N inventory-derived priority
    if scored:
        top = scored[:n]
        lines.append(f"**Top {len(top)} of {len(scored)} ranked assets:**")
        lines.append("")
        lines.append("| # | score | risk | type | asset | replace with | match deadline | reg |")
        lines.append("|---|------:|------|------|-------|--------------|----------------|-----|")
        for i, x in enumerate(top, 1):
            perf_hint = ""
            if x["family"] in family_perf:
                fp = family_perf[x["family"]]
                perf_hint = f" → `{fp[0]}` ({fp[1]}s)"
            lines.append(
                f"| {i} | {x['score']:.1f} | {x['risk']} | {x['asset_type']} "
                f"| {x['name'][:40]} | **{x['family']}**{perf_hint} "
                f"| {x['deadline'] or '—'} | {x['regulation'] or '—'} |"
            )
        if len(scored) > len(top):
            lines.append("")
            lines.append(f"_({len(scored) - len(top)} more below the top "
                         f"{len(top)} — pass top_n higher to see more.)_")
        lines.append("")
    elif profile.get("priority_assets"):
        # Fallback: no real inventory, use sector profile's priority_assets.
        lines.append("_No CBOMs ingested yet — falling back to "
                     "sector_profile.priority_assets (no scoring possible "
                     "without per-asset metadata)._")
        lines.append("")
        fam_str = ", ".join(sorted(target_families)) if target_families else "—"
        lines.append("| priority asset | recommended families |")
        lines.append("|----------------|----------------------|")
        for asset in profile["priority_assets"]:
            lines.append(f"| `{asset}` | {fam_str} |")
        lines.append("")
    else:
        lines.append("_(No CBOMs ingested and no sector_profile.priority_assets "
                     "registered — nothing to recommend.)_")

    if family_perf:
        lines.append("**Best-performing PQC candidates at NIST L3** "
                     "(from performance_test benchmarks):")
        lines.append("")
        lines.append("| family | top algorithm | avg duration (s) | avg energy (J) |")
        lines.append("|--------|---------------|------------------|----------------|")
        for fam in sorted(family_perf):
            alg, dur, egy = family_perf[fam]
            lines.append(f"| {fam} | `{alg}` | {dur} | {egy} |")

    return "\n".join(lines)


# ── Phase 5: closed-loop self-configuring crypto ─────────────────────────
# apply_pqc_configuration is the marquee tool of the closed-loop:
#
#     CAM sweep  →  recommend_migration_priority  →  apply_pqc_configuration
#                                                          │
#                                                          ▼
#                          launch_qujata_experiment ←──────┘ (verify)
#                                                          │
#                                                          ▼
#                                                  query_test_suite (read)
#
# Every call is recorded in pqc_configurations regardless of target_type
# or dry_run, so the orchestrator can reconstruct the chain. Mutating
# paths (target_type='qujata-experiment' or 'slice') are gated behind
# the operator-set env var KATANA_ALLOW_CONFIGURE — same pattern as
# katana_create_slice / katana_delete_slice.

import re as _re_apply  # local alias to avoid shadowing module-level re imports

_KATANA_ALLOW_CONFIGURE = os.getenv("KATANA_ALLOW_CONFIGURE", "false").lower() == "true"

_VALID_APPLY_TARGETS = {"audit", "qujata-experiment", "slice"}


@mcp.tool()
def apply_pqc_configuration(sector: str,
                            algorithm: str,
                            target_type: str = "audit",
                            target_ref: str = None,
                            rationale: str = None,
                            duration_s: int = 10,
                            bandwidth_kbps: int = 50000,
                            requested_by: str = None,
                            ctx: Context = None) -> str:
    """[INVENTORY] Apply a PQC configuration decision and record it in the
    pqc_configurations audit log. The marquee tool of the Phase 5
    closed loop — pairs naturally with recommend_migration_priority
    (whose top suggestion is the natural input here) and
    launch_qujata_experiment (the verification step when target_type=
    'qujata-experiment').

    Required:
        sector       energy / telco / health / financial / cross-sector / public
        algorithm    e.g. 'mldsa65', 'X25519MLKEM768', 'kyber768' — pulled
                     verbatim from a recommend_migration_priority response
                     or the performance_test leaderboard.

    Optional:
        target_type  'audit' (default) — record only, no side effects.
                     'qujata-experiment' — record + delegate to
                       launch_qujata_experiment to actually run a measurement.
                       Requires KATANA_ALLOW_CONFIGURE=true on the server.
                     'slice' — record + return the gst payload that
                       katana_create_slice WOULD send to the NBI.
        target_ref   slice id / experiment name / free-form note.
        rationale    free text — why this algorithm. Recommended to forward
                     a sentence from recommend_migration_priority's response.
        duration_s   for target_type='qujata-experiment' (1..60, default 10)
        bandwidth_kbps for the same (1..200000, default 50000 = 50 Mbps)
        requested_by short identifier of the caller — e.g. 'closed_loop',
                     'manual', or a chat session id. Logged for audit.

    Always returns the audit row id so the caller can join later.
    """
    if err := _check_ctx(ctx):
        return err

    s = (sector or "").strip().lower()
    if not s:
        return "Refusing: sector is required."
    if s not in _VALID_SECTORS:
        return (f"Unknown sector '{sector}'. Valid: "
                + ", ".join(sorted(_VALID_SECTORS)))

    alg = (algorithm or "").strip()
    if not alg:
        return "Refusing: algorithm is required (e.g. 'mldsa65', 'X25519MLKEM768')."

    tt = (target_type or "audit").strip().lower()
    if tt not in _VALID_APPLY_TARGETS:
        return (f"Unknown target_type '{target_type}'. Valid: "
                + ", ".join(sorted(_VALID_APPLY_TARGETS)))

    is_mutation = tt in ("qujata-experiment", "slice")
    if is_mutation and not _KATANA_ALLOW_CONFIGURE:
        return ("Refusing: target_type='%s' is a mutating operation and "
                "KATANA_ALLOW_CONFIGURE is false on this server. Re-run "
                "with target_type='audit' for a dry-run, or ask the "
                "operator to flip KATANA_ALLOW_CONFIGURE=true." % tt)

    requested_by_clean = (requested_by or "")[:64]
    rationale_clean = (rationale or "")[:2000]
    target_ref_clean = (target_ref or "")[:255]

    # Step 1 — record the audit row first, regardless of what comes next.
    # We do this before any side-effecting call so we never apply without
    # logging.
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pqc_configurations "
                "(sector, algorithm, target_type, target_ref, rationale, "
                " dry_run, qujata_suite_id, requested_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (s, alg[:128], tt, target_ref_clean or None,
                 rationale_clean or None,
                 (tt == "audit"), None, requested_by_clean or None),
            )
            audit_id = cur.lastrowid
        conn.commit()
        conn.close()
    except pymysql.Error as e:
        return f"Database error inserting audit row: {e}"

    lines = [
        f"### apply_pqc_configuration  → audit #{audit_id}",
        "",
        f"  sector:     `{s}`",
        f"  algorithm:  **{alg}**",
        f"  target:     `{tt}`" + (f" → `{target_ref_clean}`" if target_ref_clean else ""),
        f"  dry_run:    `{tt == 'audit'}`",
    ]
    if rationale_clean:
        lines.append(f"  rationale:  _{rationale_clean[:200]}_")
    lines.append("")

    # Step 2 — branch on target_type.
    if tt == "audit":
        lines.append("✅ Recorded in `pqc_configurations` (no side effects).")
        lines.append("")
        lines.append("To verify this choice on the live testbed, follow up "
                     "with `apply_pqc_configuration(target_type="
                     "'qujata-experiment', algorithm="
                     f"'{alg}', sector='{s}')`.")
        return "\n".join(lines)

    if tt == "qujata-experiment":
        # Map algorithm name → the QUJATA enum (lower-case, hyphen-stripped).
        alg_low = alg.lower().replace("_", "").replace("-", "")
        norm_alg = None
        for canon in ("mlkem512", "mlkem768", "mlkem1024",
                      "kyber512", "kyber768", "kyber1024",
                      "p256mlkem512", "p384mlkem768", "x25519mlkem768",
                      "classical"):
            if canon in alg_low:
                # restore underscores for hybrid enum values
                norm_alg = {
                    "p256mlkem512":   "p256_mlkem512",
                    "p384mlkem768":   "p384_mlkem768",
                }.get(canon, canon)
                break

        # If we couldn't normalize to a KEM, QUJATA cannot measure it
        # (signature families ml-dsa / slh-dsa / falcon / dilithium etc.).
        # Don't pretend we ran an experiment — flip the row back to
        # dry_run=1 with a note and explain why.
        if norm_alg is None:
            sig_families = ("mldsa", "slhdsa", "falcon", "dilithium",
                            "sphincs", "xmss", "lms")
            is_signature = any(f in alg_low for f in sig_families)
            note = (
                "QUJATA does not measure signature algorithms; only KEM/KEX. "
                "Recommend pairing with an MLKEM partner for the verification "
                "step (e.g. mlkem768 if you want NIST L3 floor)."
                if is_signature else
                f"Algorithm '{alg}' is outside QUJATA's KEM enum "
                "(mlkem512/768/1024, kyber512/768/1024, p256_mlkem512, "
                "p384_mlkem768, x25519mlkem768, classical)."
            )
            try:
                conn = _connect()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE pqc_configurations "
                        "SET dry_run = 1, notes = %s WHERE id = %s",
                        (note[:1000], audit_id),
                    )
                conn.commit()
                conn.close()
            except pymysql.Error:
                pass
            lines.append("⚠ QUJATA verification skipped — " + note)
            lines.append("")
            lines.append(f"Audit row #{audit_id} marked `dry_run=1` "
                         "with that note.")
            return "\n".join(lines)

        slice_id = (target_ref_clean if target_ref_clean
                    in {"research", "iot", "video", "mgmt", "slice1", "slice2",
                        "slice3", "uc3-slice-1", "uc3-slice-2", "uc3-slice-3"}
                    else "mgmt")
        launch_msg = launch_qujata_experiment(
            algorithm=norm_alg,
            slice_id=slice_id,
            duration_s=duration_s,
            bandwidth_kbps=bandwidth_kbps,
            ctx=ctx,
        )

        # Update the audit row with the suite_id when the launch succeeded.
        suite_id = None
        m = _re_apply.search(r"#(\d+)", launch_msg or "")
        if m:
            try:
                suite_id = int(m.group(1))
                conn = _connect()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE pqc_configurations SET qujata_suite_id = %s "
                        "WHERE id = %s",
                        (suite_id, audit_id),
                    )
                conn.commit()
                conn.close()
            except (pymysql.Error, ValueError):
                pass

        lines.append("🚀 Delegated to `launch_qujata_experiment`:")
        lines.append("")
        for ln in (launch_msg or "").splitlines():
            lines.append(f"> {ln}")
        if suite_id:
            lines.append("")
            lines.append(f"`pqc_configurations.qujata_suite_id` updated to "
                         f"`{suite_id}`. Use `query_test_suite("
                         f"test_suite_id={suite_id})` to read the metrics.")
        return "\n".join(lines)

    # tt == "slice"
    # No actual NBI call — Katana NBI doesn't currently parse PQC
    # cipher-suite annotations from the gst, so we'd be lying if we
    # claimed it took effect. Instead show the gst overlay the operator
    # could add when Katana NBI grows PQC support.
    overlay = {
        "gst": {
            "service_descriptor": {
                "pqc_profile": {
                    "algorithm":       alg,
                    "sector":          s,
                    "applied_via":     "apply_pqc_configuration",
                    "audit_row":       audit_id,
                    "rationale":       rationale_clean[:200] if rationale_clean else "",
                }
            }
        }
    }
    import json as _json
    lines.append("📝 Slice-overlay preview (Katana NBI doesn't yet honour "
                 "`pqc_profile`; this is the proposed annotation shape):")
    lines.append("")
    lines.append("```json")
    lines.append(_json.dumps(overlay, indent=2))
    lines.append("```")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="sse")
