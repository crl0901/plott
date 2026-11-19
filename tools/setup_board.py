#!/usr/bin/env python3
"""Create a Plott board: asks a few questions and writes boards/NAME/.

Run it with `./plott.sh setup`. It uses only the Python standard library, so it runs before
anything is installed. Everything it writes can be changed later: profile.yaml and sources.yaml
in the board's config folder (or in the board's Settings and Sources panels), and .env.
"""
import getpass
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARDS = os.path.join(ROOT, "boards")
SENIORITY = re.compile(r"\b(senior|sr\.?|junior|jr\.?|lead|staff|principal|entry[- ]level|i{1,3}|iv)\b", re.I)


# ---------- prompts ----------

def ask(question, default="", required=False, check=None):
    """Ask for text. `check` returns an error message for a bad answer, or None."""
    hint = f" [{default}]" if default else ""
    while True:
        try:
            answer = input(f"{question}{hint}: ").strip()
        except EOFError:
            sys.exit("\nSetup cancelled.")
        answer = answer or default
        if required and not answer:
            print("  An answer is required.")
            continue
        err = check(answer) if (check and answer) else None
        if err:
            print(f"  {err}")
            continue
        return answer


def ask_yes(question, default=False):
    answer = ask(f"{question} (y/n)", "y" if default else "n")
    return answer.lower().startswith("y")


def ask_int(question, default, lo=0, hi=10**9):
    def check(a):
        try:
            v = int(a.replace(",", "").replace("$", ""))
        except ValueError:
            return "Enter a whole number."
        return None if lo <= v <= hi else f"Enter a number from {lo:,} to {hi:,}."
    return int(ask(question, str(default), check=check).replace(",", "").replace("$", ""))


def ask_list(question, default="", required=False):
    answer = ask(question + " (separate with commas)", default, required)
    return [x.strip() for x in answer.split(",") if x.strip()]


def ask_secret(question):
    try:
        return getpass.getpass(f"{question} (input is hidden): ").strip()
    except EOFError:
        sys.exit("\nSetup cancelled.")


def section(title):
    print(f"\n== {title} ==")


# ---------- defaults ----------

def detect_timezone():
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:
        with open("/etc/timezone") as f:
            tz = f.read().strip()
            if tz:
                return tz
    except OSError:
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def check_timezone(tz):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except ImportError:
        return None
    except Exception:
        return "Unknown time zone. Use a name like America/Chicago, Europe/London, or UTC."
    return None


def used_ports():
    ports = set()
    if os.path.isdir(BOARDS):
        for name in os.listdir(BOARDS):
            try:
                with open(os.path.join(BOARDS, name, ".env")) as f:
                    for line in f:
                        if line.startswith("PORT="):
                            ports.add(line.split("=", 1)[1].strip())
            except OSError:
                pass
    return ports


def free_port():
    used, port = used_ports(), 8765
    while str(port) in used:
        port += 1
    return port


# ---------- YAML output (written as text so no packages are needed) ----------

def q(s):
    """A YAML double-quoted string. JSON string syntax is valid YAML."""
    return json.dumps(str(s), ensure_ascii=False)


def flow(items):
    return "[" + ", ".join(q(i) for i in items) + "]"


def block(items, indent):
    pad = " " * indent
    return "\n".join(f"{pad}- {q(i)}" for i in items) if items else f"{pad}[]"


def title_core(title):
    """'Senior Data Analyst II' -> 'data analyst', for a title filter that accepts any level."""
    core = re.sub(r"\s+", " ", SENIORITY.sub(" ", title)).strip(" ,-/").lower()
    return core or title.lower()


def title_pattern(title):
    return r"\s+".join(re.escape(w) for w in title_core(title).split())


def phrase_pattern(text):
    """'Round Rock' -> 'round\\s+rock'."""
    return r"\s+".join(re.escape(w) for w in text.lower().split())


# ---------- the questions ----------

def main():
    print("Plott setup\n"
          "This creates a job board for one person. Press Enter to accept the value in [brackets].\n"
          "You can change every answer later.")

    section("Board")
    def check_name(n):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", n):
            return "Use lowercase letters, numbers, - and _ (for example: alex)."
        if os.path.exists(os.path.join(BOARDS, n)):
            return f"boards/{n} already exists. Pick another name, or delete that folder first."
        return None
    board = ask("Board name (one word, used for the folder)", "main", check=check_name)
    name = ask("Your full name (shown on the board and used on cover letters)", required=True,
               check=lambda a: "Use 80 characters or fewer." if len(a) > 80 else None)
    tz = ask("Your time zone", detect_timezone(), check=check_timezone)
    port = ask_int("Port for the board's web page", free_port(), 1024, 65535)

    section("Jobs")
    titles = ask_list("Job titles you are looking for", required=True)
    us = ask_yes("Are you looking for jobs in the United States?", True)
    country = "United States" if us else ask("Country to search on LinkedIn", required=True)
    local = ask_yes("Besides remote jobs, do you want hybrid or onsite jobs near you?", False)
    city, miles, towns = "", 0, []
    if local:
        city = ask("Your city and state or region (for example: Austin, TX)", required=True)
        miles = ask_int("How many miles from there?", 30, 1, 500)
        towns = ask_list("Town names that count as local, for the location filter",
                         city.split(",")[0].strip())
    min_pay = ask_int("Minimum yearly pay in US dollars (0 for no minimum)" if us else
                      "Minimum yearly pay in US dollars, for listings that state it (0 for no minimum)", 0, 0, 10**7)
    contract = ask_yes("Include contract, temporary, and part-time jobs?", False)
    no_clearance = us and not ask_yes("Do you hold an active US security clearance?", False)
    core_skills = ask_list("Your strongest skills, used to rank jobs (optional)")

    section("Resume")
    print("Plott reads your resume as a Markdown (.md) or plain text file. If yours is a PDF or Word\n"
          "file, save a copy as plain text first. Press Enter to skip; a template is created for you.")
    def check_resume(path):
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            return "File not found."
        if not path.lower().endswith((".md", ".markdown", ".txt")):
            return "Use a .md or .txt file."
        return None
    resume_src = ask("Path to your resume", check=check_resume)

    section("Claude (optional)")
    print("Without Claude, Plott finds jobs and filters them with your rules. With Claude, it also\n"
          "scores each job against your resume and writes tailored resumes and cover letters.\n"
          "  1) Claude Pro or Max subscription. Run `claude setup-token` and paste the token.\n"
          "  2) Anthropic API key (billed per use).\n"
          "  3) No Claude for now.")
    choice = ask("Choose 1, 2, or 3", "3", check=lambda a: None if a in ("1", "2", "3") else "Enter 1, 2, or 3.")
    claude_env = {}
    if choice == "1":
        claude_env["CLAUDE_CODE_OAUTH_TOKEN"] = ask_secret("Claude token")
    elif choice == "2":
        claude_env["ANTHROPIC_API_KEY"] = ask_secret("Anthropic API key")
    claude_env = {k: v for k, v in claude_env.items() if v}
    for v in claude_env.values():
        if re.search(r"\s", v):
            sys.exit("The value contains spaces or line breaks. Setup cancelled; nothing was written.")

    # ---------- write the board ----------
    d = os.path.join(BOARDS, board)
    os.makedirs(os.path.join(d, "config"))
    write(os.path.join(d, ".env"), env_text(name, tz, port, claude_env), mode=0o600)
    write(os.path.join(d, "config", "profile.yaml"),
          profile_text(name, titles, country, local, city, miles, towns, min_pay, contract, no_clearance, core_skills))
    write(os.path.join(d, "config", "sources.yaml"), sources_text(titles, us, country, local, city, miles, min_pay))
    if resume_src:
        shutil.copyfile(os.path.expanduser(resume_src), os.path.join(d, "resume.md"))
    else:
        write(os.path.join(d, "resume.md"), RESUME_TEMPLATE.format(name=name))

    print(f"\nCreated boards/{board}/:\n"
          f"  .env                  time zone, port{', Claude' if claude_env else ''} (keep this file private)\n"
          f"  resume.md             your resume{'' if resume_src else ' (template: fill it in)'}\n"
          f"  config/profile.yaml   your filters and criteria\n"
          f"  config/sources.yaml   where Plott looks for jobs\n\n"
          f"Start the board:\n"
          f"  ./plott.sh {board} up -d --build\n"
          f"Then open http://localhost:{port} and click \"Scrape now\".")


def write(path, text, mode=0o644):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def env_text(name, tz, port, claude_env):
    lines = [
        "# Settings and secrets for this board. Keep this file private; it is not committed to git.",
        "",
        "# Web page port, and the time zone for the daily scrape and dates",
        f"PORT={port}",
        f"TZ={tz}",
        "",
        "# Claude (optional). Set one of these, then restart the board.",
        "#   CLAUDE_CODE_OAUTH_TOKEN: Claude Pro or Max subscription (create it with `claude setup-token`)",
        "#   ANTHROPIC_API_KEY: Anthropic API key, billed per use (ignored when the token is set)",
        f"CLAUDE_CODE_OAUTH_TOKEN={claude_env.get('CLAUDE_CODE_OAUTH_TOKEN', '')}",
        f"ANTHROPIC_API_KEY={claude_env.get('ANTHROPIC_API_KEY', '')}",
        "",
        "# Optional job source keys (see docs/CONFIGURATION.md)",
        "REMOTEROCKETSHIP_API_KEY=",
        "USAJOBS_API_KEY=",
        "USAJOBS_EMAIL=",
        "ADZUNA_APP_ID=",
        "ADZUNA_APP_KEY=",
        "",
        "# Let other devices on your network open the board (it has no login): BIND_ADDRESS=0.0.0.0",
        "BIND_ADDRESS=127.0.0.1",
        "",
    ]
    return "\n".join(lines)


def profile_text(name, titles, country, local, city, miles, towns, min_pay, contract, no_clearance, core_skills):
    us = country == "United States"
    where = "anywhere in the US" if us else f"anywhere in {country}"
    loc = f"remote {where}" + (f", or hybrid/onsite within {miles} miles of {city}" if local else "")
    criteria = [f"- Role: {', '.join(titles)}, or closely related roles at a similar level.",
                "- Full-time only. No contract, contract-to-hire, temporary, or part-time." if not contract
                else "- Full-time, contract, temporary, and part-time roles are all fine."]
    if min_pay:
        criteria.append(f"- Pay: the top of the posted range must reach ${min_pay:,} a year. No posted pay is fine.")
    criteria.append(f"- Location: {loc}. Remote roles limited to other countries fail.")
    if no_clearance:
        criteria.append("- Clearance: skip jobs that require an active security clearance at hire. "
                        "'Ability to obtain' a clearance after hire is fine.")
    categories = titles[:6]
    return f"""# Who this board is for and what counts as a match. Changes apply on the next scrape.
# Single values can also be changed in the board's Settings panel.

name: {q(name)}
daily_goal: 5           # "Applied today" target in the header
no_reply_days: 14       # Applied cards older than this get a "no reply in N days" tag

cleanup:
  shortlisted_max_days: 30       # remove cards left in Shortlisted this many days; 0 = off
  applied_no_response_days: 60   # move Applied cards to No Response after this many days; 0 = off

schedule:
  enabled: true
  hour: 7               # daily scrape time, in the TZ set in the board's .env
  minute: 0

scoring:
  use_claude: true       # score jobs with Claude when Claude is set up in .env
  model: sonnet          # Claude Code model alias: sonnet, opus, haiku
  min_score: 65          # Claude's 0-100 fit score needed to land in Shortlisted
  max_scored_per_run: 60 # most jobs reviewed per scrape; the rest wait for the next one
  batch_size: 5          # jobs per Claude call
  # Hard rules Claude applies when scoring. Write them in plain language.
  criteria: |
{chr(10).join('    ' + c for c in criteria)}

# Groups Claude sorts shortlisted jobs into. They are also the filter buttons at the top of the
# board. "Other" is always added. Jobs in tag_categories get a blue tag on their card.
categories: {flow(categories)}
tag_categories: []

# Extra rules for tailored resumes and cover letters: things your resume could be stretched to
# imply but must not. For example: "- I have not managed people; never imply it."
resume_notes: |

# How much each skill counts. Jobs are ranked by these before the per-run limit (core +3,
# working +1, gap -2 for each one a posting mentions), Claude weighs them when scoring, and cards
# show which core skills and gaps a posting mentions. "A|B|C" is one skill with other names.
# Names of 3 characters or fewer (AWS, SQL) must match case exactly.
skills:
  core:          # skills you use every day
{block(core_skills, 4)}
  working:       # skills you have used some
    []
  gap:           # skills you have little or no experience with
    []

# Find more companies: when a job links to a company's careers board on a supported system
# (Greenhouse, Lever, Ashby, and others), scan that company's whole board on every run.
discovery:
  enabled: true

# Rule filters, applied before anything is sent to Claude. Patterns are regular expressions and
# ignore case.
filters:
  min_pay: {min_pay}             # skip jobs whose posted pay tops out below this; 0 = off
  allow_contract: {str(contract).lower()}
  allow_no_pay: true             # false: skip listings that don't show pay
  allow_local_onsite: {str(local).lower()}       # keep hybrid/onsite jobs in local_locations
  remote_us_only: {str(us).lower()}          # skip remote jobs limited to other countries
  max_age_days: 30               # skip jobs posted more than this many days ago; 0 = off
  title_include:                 # a job title must match one of these
{block([title_pattern(t) for t in titles], 4)}
  title_exclude:                 # skip titles that match any of these
    - "\\\\bintern\\\\b|internship"
  company_exclude:               # skip companies that match any of these (staffing agencies here)
    - "jobot|\\\\bdice\\\\b|robert half|teksystems|insight global|cybercoders|lensa|jobs via|clearancejobs"
  clearance_patterns:{'' if no_clearance else ' []          # add patterns here to skip jobs that require a clearance'}
{CLEARANCE if no_clearance else ''}  contract_patterns:             # used when allow_contract is false
    - "contract[- ]to[- ]hire|\\\\bC2H\\\\b|\\\\bcontract (role|position|assignment)\\\\b|\\\\b1099\\\\b|corp[- ]to[- ]corp"
    - "duration:\\\\s*\\\\d+\\\\s*months|\\\\d+\\\\+?\\\\s*month contract"
  local_locations:               # location text that counts as local
{block([phrase_pattern(t) for t in towns], 4) if towns else '    []'}
"""


CLEARANCE = r"""    - "active (dod |us |u\\.s\\. |government |federal )?(security |secret |top secret |ts/sci |ts )?clearance(?!\\s*(is\\s*)?(preferred|a plus|desired|nice))"
    - "(must|required to|need to) (currently )?(hold|have|possess) (an? )?(active |current )?(dod |us |government )?(secret|top secret|ts/sci|ts|security) clearance"
    - "(current|existing) (secret|top secret|ts/sci|ts) clearance"
    - "currently cleared|must be cleared|clearance (is )?required (at|prior to|before) (start|hire|time of hire)"
    - "(full[- ]scope|ci|counterintelligence) poly(graph)?|ts/sci (with|w/) poly"
"""


def sources_text(titles, us, country, local, city, miles, min_pay):
    t = block(titles, 6)
    out = [SOURCES_HEADER, "sources:"]
    out.append(f"""  - type: linkedin
    name: LinkedIn - remote
    location: {q(country)}
    workplace: [remote]
    days: 7
    pages: 2
    keywords:
{t}
""")
    if local:
        out.append(f"""  - type: linkedin
    name: LinkedIn - near me
    location: {q(city)}
    distance: {miles}
    workplace: [hybrid, onsite]
    days: 7
    pages: 2
    keywords:
{t}
""")
    if us:
        out.append(f"""  - type: dice
    name: Dice - remote
    workplace: [remote]
    days: 7
    pages: 1
    keywords:
{t}
""")
        if local:
            out.append(f"""  - type: dice
    name: Dice - near me
    workplace: [hybrid, onsite]
    location: {q(city)}
    radius: {miles}
    days: 7
    pages: 1
    keywords:
{t}
""")
    out.append(f"""  - type: himalayas
    name: Himalayas
    us_only: {str(us).lower()}
    search: {flow(titles)}

  - type: jobicy
    name: Jobicy
    geo: {'usa' if us else 'anywhere'}
    tags: {flow(titles)}

  # Works without a key (about 20 jobs per title); REMOTEROCKETSHIP_API_KEY in .env gives more.
  - type: remoterocketship
    name: Remote Rocketship
    pages: 2{f'''
    min_salary: {min_pay}''' if min_pay else ''}
    job_titles:
{block(titles, 6)}

  # Tech jobs from the monthly "Ask HN: Who is hiring?" thread.
  - type: hackernews
    name: Hacker News
    enabled: false
""")
    if us:
        out.append(f"""  # Needs USAJOBS_API_KEY and USAJOBS_EMAIL in .env (free: developer.usajobs.gov).
  - type: usajobs
    name: USAJOBS
    enabled: false
    days: 14
    keywords: {flow(titles)}

  # Needs ADZUNA_APP_ID and ADZUNA_APP_KEY in .env (free: developer.adzuna.com).
  - type: adzuna
    name: Adzuna
    enabled: false
    days: 3
    titles: {flow(titles)}{f'''
    near: {{where: {q(city)}, miles: {miles}}}''' if local else ''}
""")
    out.append("""  # Company career boards. Add the board name (slug) of each company you want to follow, from its
  # job board address, for example "gitlab" in job-boards.greenhouse.io/gitlab.
  # - type: greenhouse
  #   name: Greenhouse companies
  #   companies: [gitlab, cloudflare]
  # - type: lever
  #   companies: [example]
  # - type: ashby
  #   companies: [example]
""")
    return "\n".join(out)


SOURCES_HEADER = """# Where this board looks for jobs. Edit it here or in the board's Sources panel.
# Every source takes an optional `name:` and `enabled: false`.
# docs/CONFIGURATION.md lists every source type and its options.
"""

RESUME_TEMPLATE = """# {name}

**Your Job Title** | City, State

email@example.com | (555) 555-5555 | linkedin.com/in/your-profile

## Summary

Two or three sentences about your experience.

## Skills

- **Area:** Skill, Skill, Skill
- **Area:** Skill, Skill, Skill

## Experience

### Job Title
**Company** | Jan 2020 - Present

- What you did and the result.
- What you did and the result.

## Education

**Degree**, School | Year
"""


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nSetup cancelled.")
