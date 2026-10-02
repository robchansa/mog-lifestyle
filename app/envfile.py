"""Load settings from a private `KEY=VALUE` file before configuration is read.

On shared hosting the code folder is replaced on every deploy, so secrets
cannot live there -- and must never live in git.  They go in one file outside
the app, by default `~/mog-data/mog.env` (override with `MOG_ENV_FILE`),
readable only by the account (`chmod 600`).  Both the website (via
`passenger_wsgi.py`) and command-line tasks (`run.py`) load it, so they always
agree on which database and keys they use.

Variables already present in the environment win, so a host's own settings
panel can still override the file.  This module must not import `app.config`:
it runs before configuration exists.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping

DEFAULT_PATH = "~/mog-data/mog.env"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(text: str) -> dict[str, str]:
    """`KEY=value` lines; blank lines, `# comments` and `export ` are allowed.

    A value may be wrapped in matching single or double quotes.  Anything after
    the first `=` is the value, verbatim -- so a password may contain `#` or `=`.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _NAME.fullmatch(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str | os.PathLike | None = None, *,
                  environ: MutableMapping[str, str] | None = None) -> Path | None:
    """Apply the env file if it exists.  Returns its path, or None."""
    environ = os.environ if environ is None else environ
    candidate = Path(str(path or environ.get("MOG_ENV_FILE") or DEFAULT_PATH)).expanduser()
    if not candidate.is_file():
        return None
    for key, value in parse(candidate.read_text(encoding="utf-8")).items():
        environ.setdefault(key, value)
    return candidate
