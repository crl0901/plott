"""Source registry. Each module exposes fetch(cfg) -> list[job dict] and may expose
enrich(job) -> job to fill in a full description later (only called for jobs that pass
the cheap filters).

Job dict keys: source, external_id, company, title, url, location, workplace
(remote/hybrid/onsite/unknown), employment_type, pay, pay_min, pay_max, description, posted_at.
"""
from . import (adzuna, ats, dice, generic, hackernews, linkedin, remote_boards, remoterocketship, talent,
               usajobs, wellfound)

FETCHERS = {
    "linkedin": linkedin.fetch,
    "greenhouse": ats.greenhouse,
    "lever": ats.lever,
    "ashby": ats.ashby,
    **{t: ats.fetcher(t) for t in ("smartrecruiters", "workable", "recruitee", "breezy", "bamboohr",
                                   "rippling", "personio")},
    "workday": ats.workday,
    "remoteok": remote_boards.remoteok,
    "remotive": remote_boards.remotive,
    "weworkremotely": remote_boards.weworkremotely,
    "himalayas": remote_boards.himalayas,
    "jobicy": remote_boards.jobicy,
    "hackernews": hackernews.fetch,
    "usajobs": usajobs.fetch,
    "adzuna": adzuna.fetch,
    "remoterocketship": remoterocketship.fetch,
    "dice": dice.fetch,
    "talent": talent.fetch,
    "wellfound": wellfound.fetch,
    "remotejobs": remote_boards.remotejobs,
    "url": generic.fetch,
}

ENRICHERS = {
    "linkedin": linkedin.enrich,
    "dice": dice.enrich,
    "talent": talent.enrich,
    "greenhouse": ats.enrich_greenhouse,
    "smartrecruiters": ats.enrich_smartrecruiters,
    "workable": ats.enrich_workable,
    "bamboohr": ats.enrich_bamboohr,
    "rippling": ats.enrich_rippling,
    "workday": ats.enrich_workday,
    "adzuna": generic.enrich_from_url,   # the API only gives a 500-character excerpt
}


def enrich(job):
    """Make sure the job has a usable description before it's scored."""
    fn = ENRICHERS.get(job["source"])
    if fn:
        return fn(job)
    if len(job.get("description") or "") < 400 and job.get("url"):
        return generic.enrich_from_url(job)
    return job
