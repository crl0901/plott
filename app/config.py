"""Paths and YAML config loading. Config files live in CONFIG_DIR (mounted from the board's config/ folder)."""
import os

import yaml

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
RESUME_PATH = os.environ.get("RESUME_PATH", os.path.join(CONFIG_DIR, "resume.md"))
SOURCES_PATH = os.path.join(CONFIG_DIR, "sources.yaml")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.yaml")
DB_PATH = os.path.join(DATA_DIR, "jobs.db")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def profile():
    return load_yaml(PROFILE_PATH)


def sources():
    return load_yaml(SOURCES_PATH).get("sources", []) or []


def resume():
    """The base resume (Markdown), or "" when there is none yet."""
    try:
        with open(RESUME_PATH) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return ""
