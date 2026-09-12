"""Reading `.env`, which the project has promised since step 1 and never did.

`.env.example`, the README and the missing-key error all told the user to put
their key in `.env`. Nothing read it. Anyone following the instructions got
"OPENAI_API_KEY is not set" while looking at a file containing exactly that.

Implemented here rather than with python-dotenv: the format we need is
`KEY=VALUE` with comments, which is twenty lines, and the dependency would exist
solely to parse it.

**Values are never logged, echoed or included in an error.** Parse failures name
the line number and nothing else.
"""

import os
from pathlib import Path

from cra_assistant.paths import REPO_ROOT

DEFAULT_ENV_PATH = REPO_ROOT / ".env"


def parse_env(text: str) -> dict[str, str]:
    """Parse `.env` content. Malformed lines are skipped, not guessed at."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = stripped.removeprefix("export ").lstrip()
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Read `.env`. A missing file is normal, not an error."""
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def apply_dotenv(path: Path = DEFAULT_ENV_PATH) -> list[str]:
    """Put `.env` values into the environment and return the names set.

    **The real environment always wins.** A variable already exported is a
    deliberate act — a CI secret, a one-off override on the command line — and a
    file on disk must not silently replace it.

    Returns names only. Returning or logging values would defeat the point.
    """
    applied = []
    for key, value in load_dotenv(path).items():
        if key not in os.environ and value:
            os.environ[key] = value
            applied.append(key)
    return applied
