"""Skill tiers from profile.yaml (`skills: {core, working, gap}`).

Used to rank jobs before Claude's per-run budget is spent, to tell Claude how much each skill
counts when scoring, and to show on each card which listed skills the posting mentions.

An entry can list alternatives separated by "|" ("VMware|vSphere|ESXi"); they count as one skill,
shown by the first name. Matching ignores case, except for names of 3 characters or fewer
("AWS", "Git", "k3s"), which must match exactly.
"""
import re

TIERS = ("core", "working", "gap")
WEIGHTS = {"core": 3, "working": 1, "gap": -2}


def compile_skills(prof):
    """[(tier, name, regex)] from the profile; empty if the profile has no skills block."""
    out = []
    for tier in TIERS:
        for entry in (prof.get("skills") or {}).get(tier) or []:
            alts = [a.strip() for a in str(entry).split("|") if a.strip()]
            if not alts:
                continue
            pats = [(r"(?<![\w])" + re.escape(a) + r"(?![\w])", 0 if len(a) <= 3 else re.I) for a in alts]
            rx = [re.compile(p, f) for p, f in pats]
            out.append((tier, alts[0], rx))
    return out


def matches(compiled, text):
    """{"core": [names], "working": [...], "gap": [...]} for the skills the text mentions."""
    hits = {t: [] for t in TIERS}
    for tier, name, rx in compiled:
        if any(r.search(text) for r in rx):
            hits[tier].append(name)
    return hits


def weight(compiled, text):
    return sum(WEIGHTS[t] * len(names) for t, names in matches(compiled, text).items())


def prompt_text(prof):
    """The skill tiers as scoring guidance for Claude, or "" if none are set."""
    sk = prof.get("skills") or {}
    def label(entry):
        alts = [a.strip() for a in str(entry).split("|") if a.strip()]
        return alts[0] + (f" ({', '.join(alts[1:])})" if len(alts) > 1 else "") if alts else ""
    names = {t: [n for n in map(label, sk.get(t) or []) if n] for t in TIERS}
    if not any(names.values()):
        return ""
    lines = ["Candidate's skill levels (weigh these when scoring; they are more reliable than reading "
             "the resume alone):"]
    if names["core"]:
        lines.append("- Core, production experience: " + ", ".join(names["core"]) + ". Postings built "
                     "around these are the strongest matches; score them higher.")
    if names["working"]:
        lines.append("- Working, some real use: " + ", ".join(names["working"]) + ". These count, but "
                     "less than core skills.")
    if names["gap"]:
        lines.append("- Gaps, little or no experience: " + ", ".join(names["gap"]) + ". Lower the score "
                     "when a posting's main requirements are gaps, and name them under gaps. A gap alone "
                     "is not a reason to skip, and a passing mention or 'nice to have' barely matters.")
    return "\n".join(lines)
