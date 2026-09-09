"""Ask AI: natural-language questions answered against the active dataset.

Claude gets a compact data brief plus a read-only SQL tool over the dataset
database, and returns prose with optional charts and tables.  Conversation
state is kept per session so follow-up questions reuse the same context.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .config import ANTHROPIC_API_KEY, DATA_DIR, MODEL

# Reasoning effort for Ask AI. Lower effort consolidates tool calls, which is
# what keeps question latency down - each extra round is a separate API call.
EFFORT = os.environ.get("ESP_AI_EFFORT", "medium")
from .reports import ai_context

MAX_ROWS = 200
MAX_TOOL_ROUNDS = 12
MAX_HISTORY_TURNS = 12
SESSION_TTL_SECONDS = 6 * 3600

# Chat state is stored on disk rather than in this process: Passenger and other
# multi-worker servers spread a conversation's turns across processes, and an
# in-memory dict would silently drop the history on every other question.
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def _session_path(dataset_id: str, session_id: str):
    key = hashlib.sha256(f"{dataset_id}::{session_id}".encode()).hexdigest()[:32]
    return SESSION_DIR / f"{key}.json"


def _read_session(path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_session(path, data: Dict[str, Any]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _prune_session_files() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for path in SESSION_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate|begin|commit)\b",
    re.I,
)

SCHEMA_BRIEF = """
Read-only SQLite schema (one database per uploaded weblog):

requests(id, ip, ts INTEGER unix-seconds-UTC, host, method, path, query, ref_host, ua_id,
         category, product, industry, campaign, uid, source)
  - one row per web request. category is one of:
    demo, contact, pricing, product, industry, proof, blog, career, investor, support, partner, admin, other
  - product/industry are NULL unless the page maps to one
  - source classifies the referrer: 'Direct / Unknown', 'Internal (egain.com)', 'Organic Search',
    'AI Assistant', 'Social', 'LinkedIn', 'Email / Marketing', 'Job Boards', 'Other Referral'

user_agents(id, ua, is_bot, bot_name)

ip_stats(ip PRIMARY KEY, first_ts, last_ts, active_days, sessions, page_views, unique_pages,
         contact_views, demo_views, pricing_views, product_views, industry_views, proof_views,
         blog_views, career_views, investor_views, support_views, marketing_email_views,
         organic_ref_views, linkedin_ref_views, ai_ref_views, external_ref_views, max_req_per_min,
         is_bot_ua, bot_name, crawler_risk, risk_reason, career_share, investor_share, support_share,
         products, industries, campaigns, uid, top_referrer, top_intent_pages, source,
         score, score_components, tier, eligible, why, next_action)
  - the prospect record. tier: 'A - Immediate', 'A - High', 'B - Warm', 'C - Nurture', 'Not eligible'
  - eligible = 1 means the IP showed at least one intent signal and is not a declared bot.
    ALWAYS filter on eligible = 1 when answering questions about prospects.

ip_product(ip, product, views)    ip_industry(ip, industry, views)    ip_campaign(ip, campaign, views)
page_stats(path PRIMARY KEY, requests, unique_ips, category, product, industry)
sources(ref_host PRIMARY KEY, source, requests, unique_ips, intent_ips, demo_views, contact_views)
campaigns(campaign PRIMARY KEY, requests, unique_ips, uids, contact_views, demo_views, utm_source, utm_medium)
ip_map(ip PRIMARY KEY, company, domain, extra)   -- sales-supplied IP -> account mapping; may be empty
meta(key, value)  -- value is JSON
"""

SYSTEM_PROMPT = """You are the analyst inside ESP (eGain Sales Prospects), a web-log prospecting tool used by
eGain sales reps.

Answer questions about the loaded website-visitor log using the query_sql tool. Do not guess numbers -
query for them.

Query in parallel. Every round trip costs the rep several seconds of waiting, so issue all the
queries you already know you need as multiple query_sql calls in the SAME turn, rather than one at a
time. Only run a follow-up round when the next query genuinely depends on what the last one returned.
Three well-chosen queries in one turn beat ten sequential ones; aim to answer within two or three
rounds.

Rules:
- Always restrict prospect questions to ip_stats.eligible = 1 unless the user explicitly asks about raw
  traffic or bots.
- Timestamps are unix seconds UTC: use datetime(ts, 'unixepoch') for readable dates.
- When a result is comparative or has more than three categories, call emit_chart so the rep sees it
  visually. Pie for share-of-total, bar for rankings, line for anything over time.
- When the useful answer is a list of accounts or pages, call emit_table.
- Keep prose short and commercial: what the rep should do, not how you queried it. Use markdown
  (headings, bullets, bold) - it is rendered.
- Never present an IP as a person. Offices, VPNs, NAT gateways and cloud hosts aggregate many users.
- If the data cannot answer the question, say so plainly and say what would be needed.
"""

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "query_sql",
        "description": (
            "Run one read-only SELECT (or WITH ... SELECT) against the dataset database and get the rows "
            f"back as JSON. At most {MAX_ROWS} rows are returned, so aggregate and LIMIT in SQL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT statement. No semicolon needed."},
                "purpose": {"type": "string", "description": "One short line on what this query establishes."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "emit_chart",
        "description": "Render a chart in the answer. Call once per chart, before or after your prose.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["pie", "doughnut", "bar", "horizontalBar", "line"]},
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "series": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "data": {"type": "array", "items": {"type": "number"}},
                        },
                        "required": ["label", "data"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["type", "title", "labels", "series"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "emit_table",
        "description": "Render a table in the answer. Use for account/page/campaign lists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            "required": ["title", "columns", "rows"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class AskAIError(RuntimeError):
    pass


def available() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _validate_sql(sql: str) -> str:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError("Empty SQL.")
    if ";" in sql:
        raise ValueError("Only one statement per query.")
    low = sql.lstrip().lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(sql):
        raise ValueError("Only read-only queries are allowed.")
    return sql


def run_sql(dataset_id: str, sql: str) -> Dict[str, Any]:
    sql = _validate_sql(sql)
    conn = db.connect(dataset_id, readonly=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute(sql)
        cols = [c[0] for c in (cur.description or [])]
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns": cols,
            "row_count": len(rows),
            "truncated": truncated,
            "rows": [[r[c] for c in cols] for r in rows],
        }
    finally:
        conn.close()


def _session(session_id: str, dataset_id: str, dataset_name: str) -> Dict[str, Any]:
    path = _session_path(dataset_id, session_id)
    with _lock:
        _prune_session_files()
        sess = _read_session(path)
        if sess is None or sess.get("dataset_id") != dataset_id:
            conn = db.connect(dataset_id, readonly=True)
            try:
                brief = ai_context(conn, dataset_name)
            finally:
                conn.close()
            sess = {
                "dataset_id": dataset_id,
                "brief": brief,
                "messages": [],
                "updated": time.time(),
            }
            _write_session(path, sess)
        return sess


def reset_session(session_id: str, dataset_id: str) -> None:
    with _lock:
        try:
            _session_path(dataset_id, session_id).unlink()
        except OSError:
            pass


def ask(question: str, dataset_id: str, dataset_name: str, session_id: str = "default",
        on_progress: Optional[Any] = None) -> Dict[str, Any]:
    if not available():
        raise AskAIError(
            "Ask AI is not configured. Add ANTHROPIC_API_KEY to the .env file next to run.sh and restart ESP."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise AskAIError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from exc

    sess = _session(session_id, dataset_id, dataset_name)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": SCHEMA_BRIEF},
        # The data brief is stable for the life of the dataset, so cache it.
        {"type": "text", "text": sess["brief"], "cache_control": {"type": "ephemeral"}},
    ]

    messages: List[Dict[str, Any]] = list(sess["messages"])
    messages.append({"role": "user", "content": question})

    charts: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    queries: List[Dict[str, Any]] = []
    answer_parts: List[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    def note(text: str) -> None:
        if on_progress:
            try:
                on_progress(text)
            except Exception:
                pass

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        note(f"Thinking (step {round_no})..." if round_no > 1 else "Reading the dataset...")
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
            )
        except anthropic.AuthenticationError as exc:
            raise AskAIError("ANTHROPIC_API_KEY was rejected. Check the key in .env.") from exc
        except anthropic.RateLimitError as exc:
            raise AskAIError("Rate limited by the Claude API. Try again in a moment.") from exc
        except anthropic.APIStatusError as exc:
            raise AskAIError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AskAIError("Could not reach the Claude API. Check network connectivity.") from exc

        u = response.usage
        usage["input_tokens"] += u.input_tokens or 0
        usage["output_tokens"] += u.output_tokens or 0
        usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0

        if response.stop_reason == "refusal":
            raise AskAIError("Claude declined to answer that question.")

        for block in response.content:
            if block.type == "text" and block.text.strip():
                answer_parts.append(block.text)

        if response.stop_reason != "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            break

        messages.append({"role": "assistant", "content": response.content})
        results: List[Dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            payload = block.input if isinstance(block.input, dict) else json.loads(block.input)
            if block.name == "query_sql":
                sql = payload.get("sql", "")
                note(f"Query {len(queries) + 1}: {payload.get('purpose') or 'running'}")
                try:
                    result = run_sql(dataset_id, sql)
                    queries.append({"sql": sql, "purpose": payload.get("purpose", ""),
                                    "row_count": result["row_count"]})
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(result, default=str)})
                except (ValueError, sqlite3.Error) as exc:
                    queries.append({"sql": sql, "purpose": payload.get("purpose", ""), "error": str(exc)})
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Query failed: {exc}", "is_error": True})
            elif block.name == "emit_chart":
                note(f"Building chart: {payload.get('title', '')[:60]}")
                charts.append(payload)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": "chart rendered"})
            elif block.name == "emit_table":
                tables.append(payload)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": "table rendered"})
            else:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Unknown tool {block.name}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        answer_parts.append("\n\n_(Stopped after the maximum number of query rounds.)_")

    note("Writing the answer...")
    answer = "\n\n".join(p.strip() for p in answer_parts if p.strip()) or \
             "I could not produce an answer for that question."

    with _lock:
        # Store the conversation as plain question/answer turns.  Keeping the raw
        # tool_use/tool_result blocks would risk trimming a tool_use away from its
        # result, which the API rejects on the next request.
        history = sess["messages"] + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        sess["messages"] = history[-(MAX_HISTORY_TURNS * 2):]
        sess["updated"] = time.time()
        _write_session(_session_path(dataset_id, session_id), sess)

    return {
        "answer": answer,
        "charts": charts,
        "tables": tables,
        "queries": queries,
        "usage": usage,
        "session_id": session_id,
        "model": MODEL,
    }


SUGGESTIONS = [
    "Which 10 accounts should I call first this week, and why?",
    "Show product interest as a pie chart for A-tier prospects only.",
    "Which marketing campaign produced the most Contact Us visits, and how many CRM UIDs can I resolve?",
    "Compare Banking, Insurance and Government prospects by tier.",
    "Which pages do demo requesters look at just before hitting the demo form?",
    "How much of the traffic is automation, and which IPs are the worst offenders?",
    "Plot daily request volume and flag any gaps in coverage.",
    "Which organic search visitors reached a product page and then a contact page?",
]
