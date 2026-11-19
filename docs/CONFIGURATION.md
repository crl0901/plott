# Configuration reference

Each board is a folder in `boards/`. `./plott.sh setup` creates it. This page explains every file
in that folder and every setting.

| File | What it holds |
|---|---|
| `.env` | Port, time zone, Claude credentials, and API keys for some job sources. Keep it private. |
| `resume.md` | Your resume in Markdown. Claude scores jobs against it and tailors it. |
| `config/profile.yaml` | Your filters, Claude's scoring rules, skills, schedule, and other settings. |
| `config/sources.yaml` | The job sites and company boards Plott searches. |
| `data/jobs.db` | The board's database (SQLite). Created on first start. |
| `resumes/`, `cover-letters/` | Tailored resumes and cover letters, as Markdown and PDF. |

Restart the board after you change `.env`:

```bash
./plott.sh NAME up -d
```

Changes to `profile.yaml` and `sources.yaml` apply on the next scrape. You can also edit them in
the board: **Settings** (the gear button) changes single values in `profile.yaml`, and **Sources**
edits `sources.yaml`.

## .env

| Variable | Purpose |
|---|---|
| `PORT` | Port for the web page. Default `8765`. Give each board its own port. |
| `TZ` | Time zone for the daily scrape and for dates, for example `America/Chicago`. Default `UTC`. |
| `BIND_ADDRESS` | `127.0.0.1` (default): only this computer can open the board. `0.0.0.0`: other devices on your network can too. The board has no login, so anyone who can reach it can use it. Do not expose it to the internet, and use `0.0.0.0` only on a network you trust. |
| `ALLOWED_HOSTS` | Host names the board answers to, separated by commas, for example `plott.home.lan`. `localhost` and IP addresses always work. Requests that use any other name are refused. |
| `MEM_LIMIT` | The most memory the board can use. Default `2g`. |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Pro or Max subscription token. Create it with `claude setup-token`. Lasts one year. |
| `ANTHROPIC_API_KEY` | Anthropic API key, billed per use. Ignored when `CLAUDE_CODE_OAUTH_TOKEN` is set. |
| `REMOTEROCKETSHIP_API_KEY` | Optional. Remote Rocketship API key. Without it, Plott reads their public search page. |
| `USAJOBS_API_KEY`, `USAJOBS_EMAIL` | Needed for the `usajobs` source. Free at developer.usajobs.gov. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Needed for the `adzuna` source. Free at developer.adzuna.com. |

### Claude

Claude is optional.

| | Without Claude | With Claude |
|---|---|---|
| Find jobs and apply your filters | Yes | Yes |
| Score jobs against your resume | No. Jobs that pass the filters go to Shortlisted. | Yes. Jobs scoring `min_score` or higher go to Shortlisted, with a fit summary and gaps. |
| Sort jobs into categories | No. Jobs get "Other". | Yes |
| `url` sources (any careers page) | No | Yes |
| Tailored resumes and cover letters | No | Yes |

To create a subscription token, install Claude Code (https://claude.com/claude-code) on any
computer and run `claude setup-token`. Copy the token into `CLAUDE_CODE_OAUTH_TOKEN`.

To use Claude only for tailored resumes and not for scoring, turn off **Score jobs with Claude**
in Settings (`scoring.use_claude: false`).

## profile.yaml

| Key | Meaning |
|---|---|
| `name` | Your name. Shown in the header and used on cover letters. |
| `daily_goal` | Target for the "Applied today" counter. |
| `no_reply_days` | Applied cards older than this get a "no reply in N days" tag. |
| `cleanup.shortlisted_max_days` | Cards left in Shortlisted this many days are removed and do not come back. `0` turns this off. |
| `cleanup.applied_no_response_days` | Applied cards move to No Response after this many days. `0` turns this off. |
| `schedule.enabled`, `schedule.hour`, `schedule.minute` | The daily scrape, in the time zone from `TZ`. |
| `scoring.use_claude` | Score jobs with Claude when Claude is set up. |
| `scoring.model` | Claude model: `sonnet`, `opus`, or `haiku`. |
| `scoring.min_score` | Claude's 0-100 fit score needed to reach Shortlisted. |
| `scoring.max_scored_per_run` | The most jobs reviewed in one scrape. The rest wait for the next scrape. |
| `scoring.batch_size` | Jobs per Claude request. A posting with text that looks like instructions to an AI (for example "ignore the criteria") is always scored in a request of its own, so it cannot change other jobs' scores. Set `1` to score every job on its own; this uses more of your Claude usage. |
| `scoring.criteria` | Rules Claude applies to every job, in plain language. A job that breaks one is skipped. |
| `documents.model` | Optional. A different Claude model for tailored resumes and cover letters. |
| `categories` | Groups Claude sorts jobs into. They are also the filter buttons on the board. |
| `tag_categories` | Categories that also get a blue tag on the card. |
| `resume_notes` | Extra rules for tailored resumes, for example "I have not managed people; never imply it." |
| `skills.core`, `skills.working`, `skills.gap` | Your skills by level. See below. |
| `discovery.enabled` | When a job links to a company's board on a supported system, scan that company's whole board on every run. Found companies are listed under Sources, where you can turn them off. |

### Skills

Skills decide which jobs are reviewed first when there are more than `max_scored_per_run`.
Each skill a posting mentions adds to its rank: core +3, working +1, gap -2. Claude also uses the
levels when scoring, and Shortlisted cards show the core skills and gaps a posting mentions.

Write other names for the same skill with `|`, for example `PostgreSQL|Postgres`. The first name
is the one shown. Names of 3 characters or fewer (`SQL`, `AWS`) must match case exactly; longer
names ignore case.

Without skills, Plott ranks jobs by how many terms from the `## Skills` section of your resume
they mention.

### Filters

Filters run before Claude, so jobs they remove cost nothing. Patterns are regular expressions and
ignore case.

| Key | Meaning |
|---|---|
| `filters.min_pay` | Skip jobs whose posted pay range tops out below this (yearly, US dollars). `0` turns it off. |
| `filters.allow_no_pay` | `false`: skip listings that do not show pay. |
| `filters.allow_contract` | `false`: skip contract, temporary, part-time, and internship jobs. |
| `filters.allow_local_onsite` | `true`: keep hybrid and onsite jobs whose location matches `local_locations`. `false`: remote jobs only. |
| `filters.remote_us_only` | `true`: skip remote jobs limited to other countries. Set `false` if you are not searching in the United States. |
| `filters.max_age_days` | Skip jobs posted more than this many days ago. Jobs with no date are kept. `0` turns it off. |
| `filters.title_include` | A job title must match at least one pattern. |
| `filters.title_exclude` | Skip titles that match any pattern. |
| `filters.company_exclude` | Skip companies that match any pattern. |
| `filters.clearance_patterns` | Skip postings that match, for example "active secret clearance required". |
| `filters.contract_patterns` | Treated as contract jobs when `allow_contract` is `false`. |
| `filters.local_locations` | Location text that counts as local. |

## sources.yaml

`sources:` is a list. Every source has a `type`, and can have a `name` and `enabled: false`.

### Job sites

| Type | Options |
|---|---|
| `linkedin` | `keywords`, `location`, `workplace` (`remote`, `hybrid`, `onsite`), `distance` (miles), `days`, `pages` |
| `dice` | `keywords`, `workplace`, `location` and `radius` (miles), `days` (1, 3, or 7), `pages` (30 jobs each), `employer_types` (default `[Direct Hire]`, which leaves out staffing agencies). US only. |
| `remoterocketship` | `job_titles`, `pages`, `seniority` (`junior`, `mid`, `senior`, `expert`), `min_salary`, `exclude_keywords`. Uses the API when `REMOTEROCKETSHIP_API_KEY` is set. |
| `himalayas` | `search` (terms), `pages` (20 jobs each). Skips jobs not open to the US unless `us_only: false`. |
| `jobicy` | `tags` (one request each, up to 100 jobs), `geo` (default `usa`; `anywhere` for no region filter), `industry` |
| `remoteok` | `tags` (optional) |
| `remotive` | `category` and/or `search` |
| `hackernews` | `months`: how many monthly "Ask HN: Who is hiring?" threads to read (default 1) |
| `usajobs` | US federal jobs. `keywords` and/or `series` (job series codes, for example `"2210"` for IT), `days` (up to 60), `public_only` (default `true`). Needs `USAJOBS_API_KEY` and `USAJOBS_EMAIL`. |
| `adzuna` | `titles`, `near: {where, miles}`, `days` (default 3), `pages` (50 jobs each). US only. Needs `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. |
| `weworkremotely` | `feed` or `feeds` (RSS URLs) |
| `url` | `url`, `company` (optional): any careers or search page. Claude reads the page and finds the listings. Needs Claude. Does not work on pages that need JavaScript or a login. |

### Company boards

These types read a company's own job board on an applicant tracking system:
`greenhouse`, `lever`, `ashby`, `smartrecruiters`, `workable`, `recruitee`, `breezy`, `bamboohr`,
`rippling`, `personio`, and `workday`.

List companies by their board name (slug), taken from the board's address:

| Type | Address | Slug |
|---|---|---|
| `greenhouse` | `job-boards.greenhouse.io/gitlab` | `gitlab` |
| `lever` | `jobs.lever.co/<slug>` | |
| `ashby` | `jobs.ashbyhq.com/<slug>` | |
| `smartrecruiters` | `careers.smartrecruiters.com/<slug>` | |
| `workable` | `apply.workable.com/<slug>` | |
| `rippling` | `ats.rippling.com/<slug>` | |
| `recruitee` | `<slug>.recruitee.com` | |
| `breezy` | `<slug>.breezy.hr` | |
| `bamboohr` | `<slug>.bamboohr.com` | |
| `personio` | `<slug>.jobs.personio.de` | |
| `workday` | `<tenant>.wdN.myworkdayjobs.com/<site>` | `tenant.wdN/site` |

```yaml
  - type: greenhouse
    name: Greenhouse companies
    companies:
      - gitlab
      - {slug: cloudflare, name: "Cloudflare"}   # name sets how the company is shown
```

For long lists, use `companies_file: greenhouse-companies.txt`: a text file in the board's
`config/` folder with one slug per line. A name can follow a `#`:

```text
gitlab        # GitLab
cloudflare    # Cloudflare
```

Boards are read 4 at a time. Descriptions are fetched only for jobs that pass the filters.

`workday` searches each board instead of listing it: `search` terms are combined into one query
(they match titles and descriptions), reading up to `pages` pages of 20. Workday requests run one
every 2 seconds. With `boards_per_run`, each run searches the next part of the list.

## Optional: Bitwarden Secrets Manager

Instead of putting secrets in `.env`, you can keep them in Bitwarden Secrets Manager:

1. Add these lines to the board's `.env`, which install the `bws` CLI in the image, and rebuild
   with `./plott.sh NAME up -d --build`:
   ```
   BWS_VERSION=2.1.0
   BWS_SHA256=ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860
   ```
   For another version, use the SHA-256 of `bws-x86_64-unknown-linux-gnu-<version>.zip` from
   that release's checksum file.
2. In Bitwarden, create a project and a machine account that can read only that project. Add each
   secret with the variable name as its key, for example `CLAUDE_CODE_OAUTH_TOKEN`.
3. In `.env`, set `BWS_ACCESS_TOKEN` (the machine account's access token) and, optionally,
   `BWS_PROJECT_ID`.

At startup the board loads every secret the machine account can read. Values already set in
`.env` take priority. Secrets named like system settings (for example `PATH` or names starting
with `LD_` or `PYTHON`) are ignored.

## Data

The database has three main tables. `jobs` is the board. `seen` is every listing that was
filtered out, scored too low, or deleted, so it does not come back. `runs` is the scrape history.
Deleting a card adds it to `seen`. `GET /api/screened` lists recent filtered-out jobs with the
reason.

To render a Markdown resume to PDF with the same style the board uses:

```bash
./plott.sh NAME run --rm plott python -m app.documents /resume/resume.md /out/resumes/resume.pdf
```

## Updating dependencies

Plott pins everything it installs, so a build always gets the same files:

- `Dockerfile` pins the Python base image by digest. To update it, run
  `docker buildx imagetools inspect python:3.12-slim`, and copy the `Digest` value into the
  `FROM` line.
- `Dockerfile` pins Claude Code by version and by the SHA-256 of its binary for each architecture.
  To change the version, open
  `https://downloads.claude.ai/claude-code-releases/<version>/manifest.json` and copy the
  `checksum` values for `linux-x64` and `linux-arm64` into `CLAUDE_CODE_SHA256_X64` and
  `CLAUDE_CODE_SHA256_ARM64`, and the version into `CLAUDE_CODE_VERSION`.
- `requirements.in` lists the Python packages Plott uses. `requirements.txt` is generated from it
  with exact versions and hashes for every package, including dependencies. The build refuses
  any package that does not match its hash. After you change `requirements.in`, regenerate
  `requirements.txt`:

  ```bash
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/w" -w /w python:3.12-slim sh -c \
    "pip install -q --user uv && /tmp/.local/bin/uv pip compile requirements.in --generate-hashes \
     --exclude-newer $(date -d '14 days ago' +%F) --python-version 3.12 \
     --python-platform x86_64-manylinux_2_28 --no-header -o requirements.txt"
  ```

  `--exclude-newer` uses only releases at least 14 days old.
