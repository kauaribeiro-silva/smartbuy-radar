"""SQLite persistent memory for SmartBuy Radar (tracked products + price history)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_INITIALIZED = False


def db_path() -> str:
    if settings.smartbuy_db_path:
        return settings.smartbuy_db_path
    data_dir = Path("/data")
    if data_dir.is_dir() and os.access(data_dir, os.W_OK):
        return str(data_dir / "smartbuy_memory.db")
    return str(Path(__file__).resolve().parents[1] / "smartbuy_memory.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    search_query TEXT NOT NULL,
    mandatory_tokens TEXT NOT NULL DEFAULT '[]',
    forbidden_tokens TEXT NOT NULL DEFAULT '[]',
    first_seen_price REAL,
    lowest_seen_price REAL,
    last_checked_price REAL,
    best_store TEXT DEFAULT '',
    best_url TEXT DEFAULT '',
    active_coupon TEXT DEFAULT '',
    last_checked_at TEXT,
    is_watching INTEGER NOT NULL DEFAULT 0,
    target_price REAL,
    alert_kind TEXT DEFAULT '',
    alert_message TEXT DEFAULT '',
    alert_amount REAL,
    alert_at TEXT,
    alert_dismissed INTEGER NOT NULL DEFAULT 1,
    recommendations_json TEXT DEFAULT '',
    recommendations_at TEXT,
    category TEXT DEFAULT '',
    brand TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    last_searched_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES tracked_products(id) ON DELETE CASCADE,
    price_pix REAL,
    price_installments REAL,
    effective_price REAL,
    store TEXT DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_product ON price_history(product_id, recorded_at);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db() -> None:
    global _INITIALIZED
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
            _INITIALIZED = True
            logger.info("SQLite memory ready at %s", db_path())
        finally:
            conn.close()


def _ensure() -> None:
    if not _INITIALIZED:
        init_db()


def make_product_key(canonical_name: str, mandatory_tokens: list[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", " ", canonical_name.lower()).strip()
    raw = base + "|" + ",".join(sorted(t.lower() for t in mandatory_tokens))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for key in ("mandatory_tokens", "forbidden_tokens"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except json.JSONDecodeError:
            d[key] = []
    d["is_watching"] = bool(d.get("is_watching"))
    d["alert_dismissed"] = bool(d.get("alert_dismissed"))
    return d


def _query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    _ensure()
    with _LOCK:
        conn = _connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


def _execute(sql: str, params: tuple = ()) -> int:
    _ensure()
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()


def get_product(product_id: int) -> Optional[dict[str, Any]]:
    rows = _query("SELECT * FROM tracked_products WHERE id = ?", (product_id,))
    return _row_to_dict(rows[0]) if rows else None


def get_product_by_key(product_key: str) -> Optional[dict[str, Any]]:
    rows = _query("SELECT * FROM tracked_products WHERE product_key = ?", (product_key,))
    return _row_to_dict(rows[0]) if rows else None


def list_recent(limit: int = 8) -> list[dict[str, Any]]:
    rows = _query(
        "SELECT * FROM tracked_products ORDER BY COALESCE(last_searched_at, created_at) DESC LIMIT ?",
        (limit,),
    )
    return [d for d in (_row_to_dict(r) for r in rows) if d]


def list_refresh_candidates(limit: int = 5) -> list[dict[str, Any]]:
    """Watched items first, then the most recently searched ones."""
    rows = _query(
        """
        SELECT * FROM tracked_products
        ORDER BY is_watching DESC, COALESCE(last_searched_at, created_at) DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [d for d in (_row_to_dict(r) for r in rows) if d]


def history(product_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = _query(
        "SELECT * FROM price_history WHERE product_id = ? ORDER BY recorded_at DESC LIMIT ?",
        (product_id, limit),
    )
    return [dict(r) for r in rows][::-1]


def upsert_tracked_after_search(
    *,
    canonical_name: str,
    search_query: str,
    mandatory_tokens: list[str],
    forbidden_tokens: list[str],
    category: str,
    brand: str,
    image_url: str | None,
    best_pix: float | None,
    best_store: str,
    best_url: str,
    active_coupon: str,
) -> int:
    """Create or update a tracked product after a full interactive search. Returns product id."""
    _ensure()
    key = make_product_key(canonical_name, mandatory_tokens)
    now = now_iso()
    existing = get_product_by_key(key)
    with _LOCK:
        conn = _connect()
        try:
            if existing is None:
                cur = conn.execute(
                    """
                    INSERT INTO tracked_products (
                        product_key, canonical_name, search_query, mandatory_tokens, forbidden_tokens,
                        first_seen_price, lowest_seen_price, last_checked_price, best_store, best_url,
                        active_coupon, last_checked_at, is_watching, category, brand, image_url,
                        last_searched_at, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)
                    """,
                    (
                        key, canonical_name, search_query,
                        json.dumps(mandatory_tokens), json.dumps(forbidden_tokens),
                        best_pix, best_pix, best_pix, best_store, best_url,
                        active_coupon, now, category, brand, image_url or "", now, now,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)

            lowest = existing.get("lowest_seen_price")
            if best_pix is not None and (lowest is None or best_pix < lowest):
                lowest = best_pix
            conn.execute(
                """
                UPDATE tracked_products SET
                    search_query = ?, mandatory_tokens = ?, forbidden_tokens = ?,
                    lowest_seen_price = ?, last_checked_price = COALESCE(?, last_checked_price),
                    best_store = ?, best_url = ?, active_coupon = ?, last_checked_at = ?,
                    category = ?, brand = ?, image_url = COALESCE(NULLIF(?, ''), image_url),
                    last_searched_at = ?, alert_dismissed = 1,
                    first_seen_price = COALESCE(first_seen_price, ?)
                WHERE id = ?
                """,
                (
                    search_query, json.dumps(mandatory_tokens), json.dumps(forbidden_tokens),
                    lowest, best_pix, best_store, best_url, active_coupon, now,
                    category, brand, image_url or "", now, best_pix, existing["id"],
                ),
            )
            conn.commit()
            return int(existing["id"])
        finally:
            conn.close()


def record_price(
    product_id: int,
    *,
    price_pix: float | None,
    price_installments: float | None,
    effective_price: float | None,
    store: str,
) -> None:
    _execute(
        """
        INSERT INTO price_history (product_id, price_pix, price_installments, effective_price, store, recorded_at)
        VALUES (?,?,?,?,?,?)
        """,
        (product_id, price_pix, price_installments, effective_price, store, now_iso()),
    )


def update_after_refresh(
    product_id: int,
    *,
    new_price: float,
    best_store: str,
    best_url: str,
    active_coupon: str,
    alert_kind: str = "",
    alert_message: str = "",
    alert_amount: float | None = None,
) -> None:
    prod = get_product(product_id)
    if not prod:
        return
    lowest = prod.get("lowest_seen_price")
    if lowest is None or new_price < lowest:
        lowest = new_price
    now = now_iso()
    if alert_kind:
        _execute(
            """
            UPDATE tracked_products SET
                lowest_seen_price = ?, last_checked_price = ?, best_store = ?, best_url = ?,
                active_coupon = ?, last_checked_at = ?, alert_kind = ?, alert_message = ?,
                alert_amount = ?, alert_at = ?, alert_dismissed = 0
            WHERE id = ?
            """,
            (lowest, new_price, best_store, best_url, active_coupon, now,
             alert_kind, alert_message, alert_amount, now, product_id),
        )
    else:
        _execute(
            """
            UPDATE tracked_products SET
                lowest_seen_price = ?, last_checked_price = ?, best_store = ?, best_url = ?,
                active_coupon = ?, last_checked_at = ?
            WHERE id = ?
            """,
            (lowest, new_price, best_store, best_url, active_coupon, now, product_id),
        )


def touch_checked(product_id: int) -> None:
    _execute("UPDATE tracked_products SET last_checked_at = ? WHERE id = ?", (now_iso(), product_id))


def set_target_price(product_id: int, target: float | None) -> None:
    _execute("UPDATE tracked_products SET target_price = ? WHERE id = ?", (target, product_id))


def set_watching(product_id: int, watching: bool) -> None:
    _execute("UPDATE tracked_products SET is_watching = ? WHERE id = ?", (1 if watching else 0, product_id))


def dismiss_alert(product_id: int) -> None:
    _execute("UPDATE tracked_products SET alert_dismissed = 1 WHERE id = ?", (product_id,))


def delete_product(product_id: int) -> None:
    _execute("DELETE FROM price_history WHERE product_id = ?", (product_id,))
    _execute("DELETE FROM tracked_products WHERE id = ?", (product_id,))


def save_recommendations(product_id: int, recs: list[dict[str, Any]]) -> None:
    _execute(
        "UPDATE tracked_products SET recommendations_json = ?, recommendations_at = ? WHERE id = ?",
        (json.dumps(recs, ensure_ascii=False), now_iso(), product_id),
    )


def load_recommendations(product_id: int, max_age_hours: float = 6.0) -> tuple[list[dict[str, Any]], bool]:
    """Returns (recommendations, is_fresh)."""
    prod = get_product(product_id)
    if not prod or not prod.get("recommendations_json"):
        return [], False
    try:
        recs = json.loads(prod["recommendations_json"])
    except json.JSONDecodeError:
        return [], False
    fresh = False
    at = prod.get("recommendations_at")
    if at:
        try:
            ts = datetime.fromisoformat(at)
            fresh = datetime.now(timezone.utc) - ts < timedelta(hours=max_age_hours)
        except ValueError:
            fresh = False
    return recs, fresh


def active_alert(prod: dict[str, Any], max_age_hours: float = 48.0) -> Optional[dict[str, Any]]:
    if not prod.get("alert_message") or prod.get("alert_dismissed"):
        return None
    at = prod.get("alert_at")
    if at:
        try:
            ts = datetime.fromisoformat(at)
            if datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours):
                return None
        except ValueError:
            return None
    return {
        "product_id": prod["id"],
        "kind": prod.get("alert_kind") or "queda",
        "message": prod["alert_message"],
        "amount": prod.get("alert_amount"),
        "at": at,
    }


def get_setting(key: str, default: str = "") -> str:
    rows = _query("SELECT value FROM app_settings WHERE key = ?", (key,))
    return str(rows[0]["value"]) if rows else default


def set_setting(key: str, value: str) -> None:
    _execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value.strip()),
    )


def get_telegram_config() -> dict[str, str]:
    token = get_setting("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = get_setting("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")
    return {"bot_token": token, "chat_id": chat_id}


async def send_telegram_alert(message: str) -> bool:
    """Send a real-time alert message to the user's Telegram chat if configured."""
    import httpx

    cfg = get_telegram_config()
    token = cfg.get("bot_token", "").strip()
    chat_id = cfg.get("chat_id", "").strip()
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": False,
                },
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Falha ao enviar alerta Telegram: %s", exc)
        return False

