"""Resolve readable criterion blocks once for preview and execution."""

import hashlib
import re
from importlib.resources import files
from pathlib import Path

from .config import CriteriaConfig


def digest(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def parse(text: str) -> dict[str, str]:
    blocks = {}
    current = None
    body = []
    fence = False
    for line in [*text.splitlines(), "## __end__"]:
        if line.startswith("```"):
            fence = not fence
        if line.startswith("## ") and not fence:
            if current:
                if not "\n".join(body).strip():
                    raise ValueError(f"Empty criterion: {current}")
                blocks[current] = "\n".join(body).strip()
            current = line[3:].strip()
            if current == "__end__":
                break
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", current) or current in blocks:
                raise ValueError(f"Invalid or duplicate criterion ID: {current}")
            body = []
        elif current:
            body.append(line)
        elif line.strip():
            raise ValueError("Criteria must begin with ## <criterion-id>")
    if not blocks:
        raise ValueError("Criteria file has no criterion blocks")
    return blocks


def resolve(config: CriteriaConfig) -> dict:
    default = files("visualux").joinpath("rubrics/ux-evaluation.md").read_text()
    criteria = parse(default)
    actions = {key: "default" for key in criteria}
    hashes = {"default": digest(default)}
    if config.criteria_file:
        text = Path(config.criteria_file).read_text(encoding="utf-8")
        hashes["custom"] = digest(text)
        for key, body in parse(text).items():
            actions[key] = "replaced" if key in criteria else "added"
            criteria[key] = body
    for key in config.disabled_criteria:
        if key not in actions:
            raise ValueError(f"Unknown disabled criterion: {key}")
        criteria.pop(key, None)
        actions[key] = "disabled"
    if not criteria:
        raise ValueError("At least one criterion must remain enabled")
    text = "\n\n".join(f"## {key}\n\n{body}" for key, body in criteria.items()) + "\n"
    return {"criteria": criteria, "actions": actions, "hashes": hashes, "text": text, "hash": digest(text)}
