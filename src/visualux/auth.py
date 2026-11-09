"""Human handoffs suspend all capture and model activity."""

import asyncio
import json
import queue
import sys
import threading
import time
from pathlib import Path

from .trace import atomic, utc


class HandoffTimeout(RuntimeError):
    pass


async def terminal_line(prompt: str, timeout: float) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError("Human handoff requires an interactive terminal")
    print(prompt, flush=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(sys.stdin.readline()), daemon=True).start()
    deadline = time.monotonic() + timeout
    while lines.empty():
        if time.monotonic() >= deadline:
            raise TimeoutError("handoff_timeout")
        await asyncio.sleep(0.1)
    value = lines.get()
    if not value:
        raise RuntimeError("Terminal input closed")
    return value.strip().lower()


async def handoff(browser, trace, budget, purpose="authentication", effect=None):
    browser.human = True
    start = time.monotonic()
    event = {"start": utc(), "purpose": purpose, "result": "pending"}
    trace.data["handoffs"].append(event)
    trace.save()
    try:
        remaining = budget.limits.total_handoff_seconds - budget.paused
        if remaining <= 0:
            raise TimeoutError("handoff_total_limit")
        if purpose == "authorization":
            proposal = next((e.get("proposed_action") for e in reversed(trace.data["events"]) if e["kind"] == "approval_requested"), None)
            prompt = f"Action approval required for effect '{effect}' in this task.\nProposed action: {json.dumps(proposal, ensure_ascii=False)}\nType approve to authorize this effect for this task, or cancel."
            choices = {"approve", "cancel"}
        else:
            prompt = "Sign in in the browser, return to the application page, then type resume (authentication only), assisted (you also supplied task guidance/operated the task), or cancel."
            choices = {"resume", "assisted", "cancel"}
        answer = await terminal_line(prompt, min(budget.limits.handoff_seconds, remaining))
        if answer not in choices:
            raise RuntimeError("Unrecognized handoff response; control was not resumed")
        event["result"] = answer
        if answer == "cancel":
            raise asyncio.CancelledError()
        return answer
    except TimeoutError:
        event["result"] = "timed_out"
        raise HandoffTimeout("human_handoff_timeout") from None
    finally:
        budget.paused += time.monotonic() - start
        event["end"] = utc()
        event["seconds"] = time.monotonic() - start
        if event["result"] == "pending":
            event["result"] = "interrupted_or_timed_out"
        browser.human = False
        browser.invalidate()
        trace.save()


async def save_state(context, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic(path, await context.storage_state())
