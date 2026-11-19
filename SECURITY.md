# Security

## Report a security problem

Do not report security problems in public issues.

Report them privately through GitHub:

1. Open the [Security tab](https://github.com/crl0901/plott/security) of this repository.
2. Click **Report a vulnerability**.
3. Fill in the form.

Only the maintainer can see the report. You will get a reply in the report, and you can
discuss the fix there before anything is made public.

## What to include

- What the problem is, and what an attacker could do with it.
- The steps to reproduce it, or a proof of concept.
- The Plott commit or date of the code you tested.
- Your setup, if it matters: operating system, how the board is reached (only this computer, or
  other devices with `BIND_ADDRESS`), and whether Claude is set up.

Do not include real resumes, API keys, Claude tokens, or job data from your own board. Replace
them with made-up values.

## Supported versions

Only the latest code on the `main` branch gets security fixes. Update with `git pull` and
restart your board with `./plott.sh <board name> up -d --build`.

## How Plott is meant to run

Plott runs on your own computer. Keep these points in mind when you decide whether something is
a security problem:

- **The board has no login.** By default only your own computer can open it. If you set
  `BIND_ADDRESS` so other devices on your network can open it, anyone on that network can use
  it. This is documented and is not a vulnerability by itself. A way for a web page on another
  site to use the board, or a way around the host name check, is a vulnerability.
- **Job postings are untrusted.** Plott reads postings from job sites and sends them to Claude.
  A posting that can read files, reach your local network, run code, change other jobs' scores,
  or get secrets out of the board is a vulnerability.
- **Secrets stay in the board's `.env` file.** A way for secrets to appear in logs, error
  messages, the web page, or generated documents is a vulnerability.

Problems in the job sites themselves, in Claude, or in Claude Code should be reported to those
services.
