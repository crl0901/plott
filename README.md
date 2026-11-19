# Plott

Plott is a job search board that runs on your own computer. It searches job sites every day,
removes jobs that do not match your rules, and puts the rest on a board where you track each
application from "Shortlisted" to "Offer".

With Claude (optional), Plott also scores each job against your resume and writes a tailored
resume and cover letter for the jobs you apply to.

## What Plott does

- Searches LinkedIn, Dice, Himalayas, Jobicy, Remote Rocketship, Hacker News, USAJOBS, Adzuna, and
  company job boards on Greenhouse, Lever, Ashby, Workday, and other systems.
- Runs once a day at a time you choose, or when you click **Scrape now**.
- Skips jobs you have already seen, and filters by job title, location, pay, job type, company,
  and posting age.
- Shows jobs on a board with these columns: Shortlisted, Applied, Interviewing, Offer, Rejected,
  No Response, and Skipped.
- With Claude: scores each job from 0 to 100, explains why it fits and what is missing, and writes
  a tailored resume and cover letter as PDF files.

Your data stays on your computer. Plott sends your resume and job postings only to Claude, and
only when you set Claude up.

## What you need

- **Docker**: Docker Desktop on macOS or Windows, or Docker Engine with the Compose plugin on
  Linux. On Windows, run Plott inside WSL 2.
- **Python 3**: used once, by the setup step. macOS and Linux usually have it already.
- **Git**: to download Plott.
- **Your resume as a Markdown (.md) or plain text file.** If your resume is a PDF or Word file,
  save a copy as plain text. You can also start from the template that setup creates.
- **Optional: Claude.** A Claude Pro or Max subscription, or an Anthropic API key. You can add
  this later.

## Quick start

### 1. Download Plott

```bash
git clone <repository-url> plott
cd plott
```

### 2. Create your board

```bash
./plott.sh setup
```

Setup asks for:

- a name for the board and your full name
- your time zone and a port for the web page (press Enter to accept the default)
- the job titles you want
- whether you want jobs near you, and where you live
- your minimum yearly pay, and whether to include contract jobs
- the path to your resume
- whether to use Claude, and your Claude token or API key

It creates the folder `boards/<board name>/` with your settings. To use Claude with a
subscription, run `claude setup-token` in a terminal where Claude Code is installed, and paste
the token when setup asks for it.

### 3. Start the board

```bash
./plott.sh <board name> up -d --build
```

The first start takes a few minutes while Docker builds Plott.

### 4. Open the board

Open http://localhost:8765 in your browser. If you chose a different port during setup, use
that port.

### 5. Find jobs

Click **Scrape now**. The first run takes a few minutes. New jobs appear in **Shortlisted**.
After this, Plott runs every day at 7:00 in your time zone.

## Using the board

- **Move a job**: drag the card to another column, or use the menu at the bottom of the card.
- **See details**: click **Details** on a card to read the job description, edit notes, pay, and
  status, or delete the job. A deleted job does not come back.
- **Add a job yourself**: click **Add an application**.
- **Filter**: use the category buttons, the source buttons, and the search box at the top.
- **Change settings**: click the gear button. Changes save right away.
- **Change where Plott looks**: click **Sources**. This also shows the log of the last run.
- **Tailored resume and cover letter** (needs Claude): when you move a card from Shortlisted to
  Applied, Plott asks whether to create them. You can also create them from **Details**. They are
  saved as PDF and Markdown in `boards/<board name>/resumes/` and `cover-letters/`.

## Improve your results

Setup gives you a working start. These changes make the results closer to what you want. Each
one is in `boards/<board name>/config/`:

1. **Check your resume.** Open `resume.md` and make sure it is complete. Claude uses only facts
   from this file.
2. **Add your skills** in `profile.yaml` under `skills`: `core` for skills you use every day,
   `working` for skills you have used some, and `gap` for skills you do not have. Plott looks at
   jobs that mention your core skills first.
3. **Adjust the title filters** in `profile.yaml` under `filters.title_include` and
   `filters.title_exclude` if you see jobs you do not want, or miss jobs you do want.
4. **Edit Claude's rules** in `profile.yaml` under `scoring.criteria` (with Claude). Write them in
   plain language, for example "- Skip roles that require travel more than 25% of the time."
5. **Add companies** you want to follow in `sources.yaml`. See
   [docs/CONFIGURATION.md](docs/CONFIGURATION.md#company-boards).
6. **Check what was filtered out.** Open **Sources** to see the last run's log, including why each
   job was skipped.

[docs/CONFIGURATION.md](docs/CONFIGURATION.md) explains every setting and every job source.

## Add Claude later

1. Open `boards/<board name>/.env` in a text editor.
2. Set `CLAUDE_CODE_OAUTH_TOKEN` (Claude Pro or Max, from `claude setup-token`) or
   `ANTHROPIC_API_KEY` (billed per use).
3. Restart the board: `./plott.sh <board name> up -d`

When both are set, Plott uses the subscription token.

## Commands

Run these from the `plott` folder.

| Task | Command |
|---|---|
| Create a board | `./plott.sh setup` |
| Start a board | `./plott.sh <board name> up -d --build` |
| Stop a board | `./plott.sh <board name> down` |
| Show the board's log | `./plott.sh <board name> logs -f` |
| Restart after changing `.env` | `./plott.sh <board name> up -d` |
| Update Plott | `git pull`, then start the board with `--build` |

## More than one board

Each person can have their own board. Run `./plott.sh setup` again and choose a different board
name. Setup picks a free port for each board. Each board has its own settings, resume, Claude
account, and data.

## Limits

- Plott is built mainly for job searches in the United States. Dice, USAJOBS, and Adzuna search
  only US jobs, and pay filters use US dollars. For other countries, answer "n" to the United
  States question during setup.
- Job sites change their pages. When a site changes, its source can stop working until Plott is
  updated. Errors appear in the run log under **Sources**.
- Some sites limit automated access. Check the terms of each site you search, and keep the number
  of pages low.
- The board has no login. By default, only your own computer can open it. If you let other
  devices on your network open it (`BIND_ADDRESS`), anyone on that network can use it. Do not
  expose it to the internet.

## Troubleshooting

| Problem | What to do |
|---|---|
| `There is no board named ...` | Check the name with `ls boards`, or run `./plott.sh setup`. |
| `Missing boards/<name>/resume.md` | Put your resume at that path as a Markdown file. |
| The page does not open | Check that the board is running: `./plott.sh <board name> ps`. Check the port in `boards/<board name>/.env`. |
| `port is already allocated` | Change `PORT` in `boards/<board name>/.env` and start the board again. |
| No jobs appear | Open **Sources** and read the last run's log. Most often the title filters are too narrow. |
| `Claude usage limit` in the log | Your subscription's limit was reached. The remaining jobs are scored on the next run. Lower **Jobs reviewed per run** in Settings. |
| `Permission denied` on Linux | Add your user to the `docker` group, then log out and back in. |

## License

Plott is free software under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
