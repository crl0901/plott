"""Auto-discovered company boards (Greenhouse, Ashby, Lever, and the other ATSs in sources/ats.py).

Every job the board sees is checked for ATS links (its URL, its description, and LinkedIn's
"apply on company site" link). Each new company slug is remembered in `ats_companies`, and its
whole board is scanned on every run from then on. Boards that 404 are switched off.
"""
from . import db
from .sources.ats import find_slugs, pretty

SCHEMA = """
CREATE TABLE IF NOT EXISTS ats_companies (
  ats TEXT NOT NULL, slug TEXT NOT NULL, name TEXT,
  discovered_at TEXT, discovered_from TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_checked TEXT, last_status TEXT, last_jobs INTEGER,
  PRIMARY KEY (ats, slug)
);
"""


def init():
    db.conn().executescript(SCHEMA)


def _job_texts(job):
    yield job.get("url") or ""
    yield from job.get("ats_links") or []
    yield job.get("description") or ""


def note(job, exclude_company=None):
    """Record any ATS boards linked from this job. Returns the number of new companies."""
    new = 0
    for text in _job_texts(job):
        for ats, slug in find_slugs(text):
            # The job's own company name is only trustworthy when the job URL itself is on that board
            name = job.get("company") if slug in (job.get("url") or "").lower() else None
            if exclude_company and name and exclude_company.search(name):
                continue
            if db.q("SELECT 1 FROM ats_companies WHERE ats=? AND slug=?", (ats, slug)):
                continue
            db.ex("INSERT INTO ats_companies (ats, slug, name, discovered_at, discovered_from) VALUES (?,?,?,?,?)",
                  (ats, slug, name or pretty(slug), db.now(), f"{job.get('company')} – {job.get('title')}"))
            new += 1
    return new


def backfill(exclude_company=None):
    """Pick up boards linked from every job already on the board or in the seen list."""
    new = 0
    for row in db.q("SELECT company, title, url FROM jobs UNION ALL SELECT company, title, url FROM seen"):
        new += note(row, exclude_company)
    return new


def enabled():
    return db.q("SELECT * FROM ats_companies WHERE enabled=1 ORDER BY ats, slug")


def record(ats, slug, status, jobs=None, disable=False, name=None):
    db.ex("UPDATE ats_companies SET last_checked=?, last_status=?, last_jobs=?, "
          "enabled=CASE WHEN ? THEN 0 ELSE enabled END, name=COALESCE(?, name) WHERE ats=? AND slug=?",
          (db.now(), status, jobs, 1 if disable else 0, name, ats, slug))


def all_companies():
    return db.q("SELECT * FROM ats_companies ORDER BY enabled DESC, ats, name")


def set_enabled(ats, slug, on):
    db.ex("UPDATE ats_companies SET enabled=? WHERE ats=? AND slug=?", (1 if on else 0, ats, slug))
