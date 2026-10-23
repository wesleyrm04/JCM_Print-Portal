"""SQLite index of orders (fast search/filter/summary for the staff dashboard).
Lives in data/, NOT on the NAS.  If it is ever deleted, reindex() rebuilds it from the
order.json files on the NAS."""
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from . import settings

_lock = threading.Lock()
_inited = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, kind TEXT, category TEXT, date TEXT, created TEXT, updated TEXT,
  name TEXT, phone TEXT, preset TEXT, status TEXT, paid INTEGER DEFAULT 0, pay_method TEXT DEFAULT '',
  total REAL DEFAULT 0, computed_total REAL DEFAULT 0, files INTEGER DEFAULT 0, folder TEXT,
  code TEXT, needs_review INTEGER DEFAULT 0, files_deleted INTEGER DEFAULT 0, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(date);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
"""


def _db_path() -> Path:
    return settings.data_dir() / "portal.db"


def connect():
    c = sqlite3.connect(_db_path(), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)          # cheap (IF NOT EXISTS); survives the file being deleted while running
    return c


def init():
    global _inited
    with _lock:
        with closing(connect()) as c:
            c.executescript(SCHEMA)
            c.commit()
        _inited = True


def _row(o: dict) -> tuple:
    return (o["order_id"], o.get("kind", ""), o.get("category", "print"), o["date"],
            o.get("created", ""), o.get("updated", ""), o.get("customer_name", ""), o.get("phone", ""),
            o.get("preset_request", ""), o.get("status", ""), 1 if o.get("paid") else 0,
            o.get("payment_method", ""), float(o.get("total", 0) or 0),
            float(o.get("computed_total", o.get("total", 0)) or 0), int(o.get("total_files", 0)),
            o.get("folder", ""), o.get("access_code", ""), 1 if o.get("needs_review") else 0,
            1 if o.get("files_deleted") else 0, json.dumps(o, ensure_ascii=False))


def upsert(o: dict):
    if not _inited:
        init()
    with closing(connect()) as c:
        c.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _row(o))
        c.commit()


def get(order_id: str):
    if not _inited:
        init()
    with closing(connect()) as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return _to_dict(r) if r else None


def _to_dict(r, with_data=True):
    d = dict(r)
    d["data"] = json.loads(d["data"]) if with_data and d.get("data") else None
    if not with_data:
        d.pop("data", None)
    d["paid"] = bool(d["paid"])
    d["needs_review"] = bool(d["needs_review"])
    d["files_deleted"] = bool(d["files_deleted"])
    return d


def search(date=None, status=None, category=None, q=None, limit=200, offset=0):
    if not _inited:
        init()
    where, args = [], []
    if date and date != "all":
        where.append("date=?"); args.append(date)
    if status == "active":
        where.append("status NOT IN ('Completed','Cancelled')")
    elif status and status != "all":
        where.append("status=?"); args.append(status)
    if category and category != "all":
        where.append("category=?"); args.append(category)
    if q:
        like = f"%{q.strip()}%"
        where.append("(id LIKE ? OR name LIKE ? OR phone LIKE ? OR preset LIKE ?)")
        args += [like] * 4
    sql = "SELECT * FROM orders" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created DESC LIMIT ? OFFSET ?"
    with closing(connect()) as c:
        rows = c.execute(sql, args + [int(limit), int(offset)]).fetchall()
        counts = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM orders" +
            (" WHERE date=?" if date and date != "all" else "") + " GROUP BY status",
            [date] if date and date != "all" else []).fetchall()}
    return [_to_dict(r, with_data=False) for r in rows], counts


def poll():
    if not _inited:
        init()
    with closing(connect()) as c:
        latest = c.execute("SELECT MAX(created) m FROM orders").fetchone()["m"] or ""
        active = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN "
                           "('Pending','Received','Inquiry Received')").fetchone()["n"]
    return {"latest": latest, "waiting": active}


def summary(date_from, date_to):
    if not _inited:
        init()
    with closing(connect()) as c:
        days = [dict(r) for r in c.execute(
            "SELECT date, COUNT(*) orders, ROUND(SUM(total),2) total, "
            "ROUND(SUM(CASE WHEN paid=1 THEN total ELSE 0 END),2) paid, "
            "ROUND(SUM(CASE WHEN paid=0 THEN total ELSE 0 END),2) unpaid "
            "FROM orders WHERE date BETWEEN ? AND ? AND status!='Cancelled' "
            "GROUP BY date ORDER BY date DESC", (date_from, date_to)).fetchall()]
        kinds = [dict(r) for r in c.execute(
            "SELECT kind, COUNT(*) orders, ROUND(SUM(total),2) total FROM orders "
            "WHERE date BETWEEN ? AND ? AND status!='Cancelled' GROUP BY kind ORDER BY total DESC",
            (date_from, date_to)).fetchall()]
    totals = {"orders": sum(d["orders"] for d in days), "total": round(sum(d["total"] or 0 for d in days), 2),
              "paid": round(sum(d["paid"] or 0 for d in days), 2),
              "unpaid": round(sum(d["unpaid"] or 0 for d in days), 2)}
    return {"days": days, "kinds": kinds, "totals": totals}


def export_rows(date_from, date_to):
    if not _inited:
        init()
    with closing(connect()) as c:
        return [dict(r) for r in c.execute(
            "SELECT id, date, created, kind, name, phone, preset, status, paid, pay_method, total, files "
            "FROM orders WHERE date BETWEEN ? AND ? ORDER BY created", (date_from, date_to)).fetchall()]


def reindex(base: Path) -> int:
    """Add any order.json found on the NAS that the index does not know yet."""
    if not _inited:
        init()
    added = 0
    if not base.is_dir():
        return 0
    with closing(connect()) as c:
        known = {r["id"] for r in c.execute("SELECT id FROM orders").fetchall()}
    for oj in base.glob("*/*/order.json"):
        try:
            o = json.loads(oj.read_text(encoding="utf-8"))
            oid = o["order_id"]
            if oid in known:
                continue
            o.setdefault("category", "print")
            o.setdefault("created", f"{o.get('date', '')}T00:00:00")
            o.setdefault("updated", o["created"])
            o["folder"] = str(oj.parent)
            o.setdefault("total", o.get("estimated_total", 0))
            o.setdefault("status", "Pending")
            upsert(o)
            added += 1
        except Exception:
            continue
    return added


def expired(cutoff_iso: str):
    """Closed orders last touched before cutoff whose files have not been purged yet."""
    if not _inited:
        init()
    with closing(connect()) as c:
        return [dict(r) for r in c.execute(
            "SELECT id, folder FROM orders WHERE status IN ('Completed','Cancelled') "
            "AND files_deleted=0 AND updated < ?", (cutoff_iso,)).fetchall()]
