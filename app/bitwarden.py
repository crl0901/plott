"""Optional: load secrets from Bitwarden Secrets Manager into the environment at startup.

Used only when BWS_ACCESS_TOKEN is set (and the image was built with BWS_VERSION). Then the
board's .env needs only BWS_ACCESS_TOKEN (a machine-account token). Every secret the machine
account can read is exported as an env var named after the secret's key, e.g. a secret with key
CLAUDE_CODE_OAUTH_TOKEN. Variables already set in the environment win.
"""
import json
import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)


def load_secrets():
    if not os.environ.get("BWS_ACCESS_TOKEN"):
        return []   # Bitwarden is optional; secrets come from the board's .env
    cmd = ["bws", "secret", "list"]
    if os.environ.get("BWS_PROJECT_ID"):
        cmd.append(os.environ["BWS_PROJECT_ID"])
    cmd += ["--output", "json", "--color", "no"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("bws failed to run: %s", e)
        return []
    if out.returncode:
        log.error("bws failed: %s", out.stderr.strip()[:500])
        return []
    loaded = []
    for s in json.loads(out.stdout):
        key = s.get("key", "")
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) and key not in os.environ:
            os.environ[key] = s.get("value", "")
            loaded.append(key)
    log.info("Loaded %d secret(s) from Bitwarden: %s", len(loaded), ", ".join(loaded))
    return loaded
