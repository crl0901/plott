"""The Settings panel: profile.yaml values that are on/off or a single number or word.

Each change rewrites just that value's line in profile.yaml, so comments, ordering, and everything
else in the file stay as they were. A missing key is added under its section.
"""
import os
import re
import threading

import yaml

from . import config

# path, label, group, type, default, extra (min/max/unit), help
SPEC = [
    ("name", "Name", "General", "text", "", {},
     "Your name. Shown in the header and given to Claude as the candidate's name when it tailors a "
     "resume or cover letter."),
    ("daily_goal", "Daily goal", "General", "int", 10, {"min": 0, "max": 100, "unit": "applications"},
     "Target for the \"Applied today\" counter in the header."),
    ("no_reply_days", "No-reply tag after", "General", "int", 14, {"min": 1, "max": 365, "unit": "days"},
     "Applied cards older than this get a \"no reply in N days\" tag, a nudge to follow up or move them on."),
    ("cleanup.shortlisted_max_days", "Clear stale Shortlisted after", "General", "int", 30,
     {"min": 0, "max": 365, "unit": "days"},
     "At the start of each scrape, cards that have sat in Shortlisted this long without being moved are "
     "removed and never come back. 0 turns this off."),
    ("cleanup.applied_no_response_days", "Move Applied to No Response after", "General", "int", 60,
     {"min": 0, "max": 365, "unit": "days"},
     "Applied cards whose applied date is this long ago move to the No Response column automatically (when "
     "the board loads and at each scrape). Cards you've moved on to Interviewing, Offer or Rejected aren't "
     "touched. 0 turns this off."),
    ("schedule.enabled", "Daily scrape", "Schedule", "bool", True, {},
     "Scrape automatically once a day at the time below. Off: scrapes only run when you click Scrape now."),
    ("schedule.time", "Time", "Schedule", "time", "07:00", {},
     "When the daily scrape starts, in the board's time zone (TZ in the board's .env). Takes effect right away."),
    ("scoring.use_claude", "Score jobs with Claude", "Claude scoring", "bool", True, {},
     "On: Claude scores each job that passes the filters, when Claude is set up in the board's .env. Off: "
     "jobs that pass the filters go to Shortlisted without a score. Tailored resumes still use Claude."),
    ("scoring.min_score", "Minimum fit score", "Claude scoring", "int", 65, {"min": 0, "max": 100},
     "Claude scores each job 0-100 for fit. Jobs at or above this go to Shortlisted, the rest to "
     "Screened out. Higher means fewer, closer matches."),
    ("scoring.max_scored_per_run", "Jobs reviewed per run", "Claude scoring", "int", 60, {"min": 1, "max": 500},
     "The most jobs reviewed in one scrape (scored by Claude, or added to Shortlisted when Claude is off). The "
     "best skill matches go first; the rest wait for the next run. Lower values use less of your Claude usage."),
    ("discovery.enabled", "Discover company boards", "Sources", "bool", True, {},
     "When a job links to a company's careers board on a supported system (Greenhouse, Lever, Ashby and "
     "others), add that company and scan its whole board on every run. Found companies are listed under "
     "Sources, where you can switch them off."),
    ("filters.min_pay", "Minimum pay", "Filters", "int", 0, {"min": 0, "max": 2_000_000, "step": 5000, "unit": "$ / year"},
     "Skip jobs whose posted pay range tops out below this. Listings that don't show pay aren't affected "
     "by this; see \"Allow listings without pay\". 0 turns it off."),
    ("filters.allow_no_pay", "Allow listings without pay", "Filters", "bool", True, {},
     "Off: skip listings that don't show pay. They're dropped before scoring when the posting has no "
     "amounts at all, and after scoring when Claude finds no pay either."),
    ("filters.allow_contract", "Allow contract and part-time", "Filters", "bool", False, {},
     "Off: skip contract, contract-to-hire, temporary, part-time, and internship listings. On: keep them."),
    ("filters.allow_local_onsite", "Allow local hybrid and onsite", "Filters", "bool", True, {},
     "On: keep hybrid and onsite jobs in the local area (the local_locations list in profile.yaml). "
     "Off: remote jobs only."),
    ("filters.remote_us_only", "Remote jobs must be open to the US", "Filters", "bool", True, {},
     "On: skip remote jobs limited to other countries or regions (for example \"Remote - Europe\"). Turn "
     "this off if you are not searching for jobs in the United States."),
    ("filters.max_age_days", "Max job age", "Filters", "int", 0, {"min": 0, "max": 365, "unit": "days"},
     "Skip jobs whose posted date is older than this. Jobs without a date are kept. 0 turns it off."),
]
BY_PATH = {s[0]: s for s in SPEC}
_lock = threading.Lock()


def _get(data, path):
    for k in path.split("."):
        if not isinstance(data, dict) or k not in data:
            return None
        data = data[k]
    return data


def current():
    prof = config.profile()
    out = []
    for path, label, group, typ, default, extra, help_ in SPEC:
        if typ == "time":
            sch = prof.get("schedule") or {}
            value = f"{int(sch.get('hour', 7)):02d}:{int(sch.get('minute', 0)):02d}"
        else:
            value = _get(prof, path)
            value = default if value is None else value
        out.append({"path": path, "label": label, "group": group, "type": typ, "value": value, "help": help_, **extra})
    return out


def _clean(path, value):
    """The value as the right type, or ValueError with a message for the user."""
    _, label, _, typ, _, extra, _ = BY_PATH[path]
    if typ == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be on or off")
        return value
    if typ == "int":
        try:
            v = int(str(value).replace(",", "").replace("$", "").strip())
        except ValueError:
            raise ValueError(f"{label} must be a whole number") from None
        lo, hi = extra.get("min", 0), extra.get("max", 10**9)
        if not lo <= v <= hi:
            raise ValueError(f"{label} must be between {lo:,} and {hi:,}")
        return v
    if typ == "time":
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("Time must be HH:MM, 00:00-23:59")
        return int(m.group(1)), int(m.group(2))
    v = str(value).strip()
    if not v or len(v) > 80 or "\n" in v:
        raise ValueError(f"{label} must be 1-80 characters")
    return v


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip()
    return s[:-3].strip() if s.endswith("...") else s


KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][\w-]*):(?P<rest>.*)$")
VALUE = re.compile(r"^(?P<lead>\s*)(?P<val>.*?)(?P<comment>\s+#.*)?$")


def _set_line(lines, path, value):
    """Replace path's value in the YAML lines (keeping any trailing comment where it was), or add it."""
    target = path.split(".")
    stack, block = [], None   # block: indent of a key whose value is a | or > block scalar
    parents = {}              # partial path -> index of its line
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if block is not None:
            if indent > block:
                continue
            block = None
        m = KEY.match(line)
        if not m:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, m["key"]))
        keys = [k for _, k in stack]
        parents[tuple(keys)] = i
        rest = m["rest"]
        if re.match(r"^\s*[|>][-+0-9]*\s*(#.*)?$", rest):
            block = indent
        if keys == target:
            v = VALUE.match(rest)
            head = f"{m['indent']}{m['key']}: {_scalar(value)}"
            if v["comment"]:
                col = len(line.rstrip("\n")) - len(v["comment"].lstrip())
                head += " " * max(1, col - len(head)) + v["comment"].lstrip()
            lines[i] = head + "\n"
            return lines
    # Not there: add it under its parent section (creating the section at the end if needed)
    parent, key = tuple(target[:-1]), target[-1]
    if parent and parent in parents:
        i = parents[parent]
        indent = len(lines[i]) - len(lines[i].lstrip(" "))
        child = next((len(l) - len(l.lstrip(" ")) for l in lines[i + 1:]
                      if l.strip() and not l.lstrip().startswith("#")), indent + 2)
        child = child if child > indent else indent + 2
        lines.insert(i + 1, f"{' ' * child}{key}: {_scalar(value)}\n")
        return lines
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if parent:
        lines += ["\n"] + [f"{'  ' * n}{k}:\n" for n, k in enumerate(parent)] + [f"{'  ' * len(parent)}{key}: {_scalar(value)}\n"]
    else:
        lines.append(f"{key}: {_scalar(value)}\n")
    return lines


def update(path, value):
    """Validate and write one setting. Returns the stored value. Raises ValueError for bad input."""
    if path not in BY_PATH:
        raise ValueError(f"Unknown setting: {path}")
    clean = _clean(path, value)
    edits = ([("schedule.hour", clean[0]), ("schedule.minute", clean[1])] if path == "schedule.time"
             else [(path, clean)])
    with _lock:
        with open(config.PROFILE_PATH) as f:
            lines = f.readlines()
        for p, v in edits:
            lines = _set_line(lines, p, v)
        text = "".join(lines)
        parsed = yaml.safe_load(text) or {}
        for p, v in edits:   # never write a file that doesn't read back as intended
            if _get(parsed, p) != v:
                raise ValueError(f"Couldn't update {p} in profile.yaml safely; edit it by hand")
        tmp = config.PROFILE_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, config.PROFILE_PATH)
    return f"{clean[0]:02d}:{clean[1]:02d}" if path == "schedule.time" else clean
