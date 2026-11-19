"""SQLite storage. `jobs` holds everything on the board; `seen` holds listings that were
screened out or deleted, so they never come back."""
import datetime
import json
import os
import re
import sqlite3
import threading

from .config import DB_PATH

_lock = threading.RLock()
_conn = None

STATUSES = ["shortlisted", "applied", "interviewing", "offer", "rejected", "no_response", "dead"]  # dead = "Skipped"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  company TEXT, title TEXT, url TEXT, location TEXT,
  workplace TEXT, employment_type TEXT,
  pay TEXT, pay_min INTEGER, pay_max INTEGER,
  category TEXT, description TEXT,
  posted_at TEXT, found_at TEXT, applied_at TEXT,
  status TEXT NOT NULL, status_changed_at TEXT,
  score INTEGER, fit_summary TEXT, gaps TEXT, notes TEXT,
  UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe_key);
CREATE TABLE IF NOT EXISTS seen (
  source TEXT NOT NULL, external_id TEXT NOT NULL, dedupe_key TEXT,
  company TEXT, title TEXT, url TEXT, reason TEXT, score INTEGER, at TEXT,
  PRIMARY KEY(source, external_id)
);
CREATE INDEX IF NOT EXISTS seen_dedupe ON seen(dedupe_key);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT, trigger TEXT, summary TEXT
);
"""

JOB_FIELDS = ["source", "external_id", "dedupe_key", "company", "title", "url", "location", "workplace",
              "employment_type", "pay", "pay_min", "pay_max", "category", "description", "posted_at",
              "found_at", "applied_at", "status", "status_changed_at", "score", "fit_summary", "gaps", "notes",
              "documents"]


def safe_url(url):
    """The URL if it is http(s), else "". Stops javascript: and other schemes from reaching the board."""
    u = (url or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def dedupe_key(company, title):
    return norm(company) + "|" + norm(title)


def conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        try:  # migration: tailored resume / cover letter file paths (JSON)
            _conn.execute("ALTER TABLE jobs ADD COLUMN documents TEXT")
        except sqlite3.OperationalError:
            pass
        from . import discovery
        discovery.init()
    return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def ex(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def is_known(source, external_id, key):
    with _lock:
        c = conn()
        for t in ("jobs", "seen"):
            if c.execute(f"SELECT 1 FROM {t} WHERE (source=? AND external_id=?) OR dedupe_key=? LIMIT 1",
                         (source, external_id, key)).fetchone():
                return True
    return False


def add_job(job):
    job = {**job}
    if "url" in job:
        job["url"] = safe_url(job["url"])
    job.setdefault("dedupe_key", dedupe_key(job.get("company"), job.get("title")))
    job.setdefault("found_at", now())
    job.setdefault("status_changed_at", job["found_at"])
    cols = [f for f in JOB_FIELDS if f in job]
    return ex(f"INSERT OR IGNORE INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
              [job[c] for c in cols])


def mark_seen(job, reason, score=None):
    ex("INSERT OR REPLACE INTO seen (source, external_id, dedupe_key, company, title, url, reason, score, at) "
       "VALUES (?,?,?,?,?,?,?,?,?)",
       (job["source"], str(job["external_id"]), job.get("dedupe_key") or dedupe_key(job.get("company"), job.get("title")),
        job.get("company"), job.get("title"), job.get("url"), reason, score, now()))


def update_job(job_id, fields):
    allowed = {k: v for k, v in fields.items() if k in JOB_FIELDS and k not in ("source", "external_id")}
    if "url" in allowed:
        allowed["url"] = safe_url(allowed["url"])
    if "status" in allowed:
        if allowed["status"] not in STATUSES:
            raise ValueError(f"bad status {allowed['status']}")
        cur = q("SELECT status, applied_at FROM jobs WHERE id=?", (job_id,))
        if cur and cur[0]["status"] != allowed["status"]:
            allowed["status_changed_at"] = now()
            if allowed["status"] in ("applied", "interviewing", "offer", "no_response") and not cur[0]["applied_at"]:
                allowed.setdefault("applied_at", now())
    if "company" in allowed or "title" in allowed:
        cur = q("SELECT company, title FROM jobs WHERE id=?", (job_id,))[0]
        allowed["dedupe_key"] = dedupe_key(allowed.get("company", cur["company"]), allowed.get("title", cur["title"]))
    if not allowed:
        return
    sets = ",".join(f"{k}=?" for k in allowed)
    ex(f"UPDATE jobs SET {sets} WHERE id=?", [*allowed.values(), job_id])


def delete_job(job_id, reason="deleted from board"):
    rows = q("SELECT * FROM jobs WHERE id=?", (job_id,))
    if rows:
        mark_seen(rows[0], reason)
        ex("DELETE FROM jobs WHERE id=?", (job_id,))


def prune_shortlisted(max_days):
    """Delete cards that have sat in Shortlisted for max_days or more without being acted on.
    They're recorded in `seen`, so the same listing never comes back. Returns the removed cards."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=max_days)).isoformat(timespec="seconds")
    stale = q("SELECT id, company, title FROM jobs WHERE status='shortlisted' "
              "AND COALESCE(status_changed_at, found_at) < ?", (cutoff,))
    for r in stale:
        delete_job(r["id"], f"removed: {max_days}+ days in Shortlisted")
    return stale


def expire_applied(max_days):
    """Move Applied cards whose applied date is max_days or more ago to No Response. Returns them."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=max_days)).isoformat(timespec="seconds")
    stale = q("SELECT id, company, title FROM jobs WHERE status='applied' "
              "AND COALESCE(applied_at, status_changed_at, found_at) < ?", (cutoff,))
    if stale:
        ex(f"UPDATE jobs SET status='no_response', status_changed_at=? WHERE id IN ({','.join('?' * len(stale))})",
           (now(), *[r["id"] for r in stale]))
    return stale


def record_run(started, trigger, summary):
    return ex("INSERT INTO runs (started_at, finished_at, trigger, summary) VALUES (?,?,?,?)",
              (started, now(), trigger, json.dumps(summary)))
