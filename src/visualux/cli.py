"""A small argparse interface; demo and rubric never call a model."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .config import Config, CriteriaConfig, ModelConfig, TaskSpec, load_config, load_knowledge, read_toml
from .rubric import resolve


def parser():
    cli = argparse.ArgumentParser(prog="visualux", description="Visual, task-driven usability evaluation with screenshot evidence.")
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Evaluate a task list and write HTML/Markdown/JSON artifacts")
    run.add_argument("--config", type=Path)
    run.add_argument("--url")
    run.add_argument("--task", action="append")
    run.add_argument("--model")
    run.add_argument("--evaluator-model")
    run.add_argument("--interactive", action="store_true", default=None)
    run.add_argument("--auth-state", type=Path)
    run.add_argument("--knowledge", dest="knowledge_file", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--session-mode", choices=["isolated", "shared"])
    rubric = commands.add_parser("rubric", help="Preview effective criteria without model credentials")
    rubric.add_argument("--config", type=Path)
    demo = commands.add_parser("demo", help="Serve the three bundled demo apps on loopback")
    demo.add_argument("--port", type=int, default=8765)
    auth = commands.add_parser("auth", help="Save human-authenticated browser state without model calls")
    auth.add_argument("--url", required=True)
    auth.add_argument("--save-state", required=True, type=Path)
    return cli


async def authenticate(url, path):
    from playwright.async_api import async_playwright
    from .auth import save_state, terminal_line

    Config(url=url, model=ModelConfig(name="auth-only"), tasks=[TaskSpec(id="auth", description="Human authentication setup")])
    if path.resolve().is_relative_to(Path("reports").resolve()):
        raise ValueError("Save sensitive auth state outside reports")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            answer = await terminal_line("Complete authentication, return to the app, then type save to save state or cancel.", 600)
            if answer != "save":
                return 130
            await save_state(context, path.resolve())
            print(f"Authentication state saved to {path.resolve()}. Keep it private and out of reports/version control.")
            return 0
        finally:
            await browser.close()


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        if command == "demo":
            from .demo import serve
            if not 0 <= args["port"] <= 65535:
                raise ValueError("Port must be between 0 and 65535")
            serve(args["port"])
            return
        if command == "rubric":
            path = args["config"]
            data = read_toml(path).get("evaluation", {})
            if path and data.get("criteria_file"):
                data["criteria_file"] = path.resolve().parent / data["criteria_file"]
            rubric = resolve(CriteriaConfig.model_validate(data))
            for key, action in rubric["actions"].items():
                print(f"{key}: {action}")
            print("\n" + rubric["text"], end="")
            return
        if command == "auth":
            raise SystemExit(asyncio.run(authenticate(args["url"], args["save_state"])))
        path = args.pop("config")
        config = load_config(path, args)
        rubric = resolve(config.evaluation)
        knowledge = load_knowledge(config.knowledge_file)
    except TimeoutError:
        print("Runtime error: human_handoff_timeout.", file=sys.stderr)
        raise SystemExit(1)
    except (ValueError, OSError, UnicodeError) as exc:
        if isinstance(exc, ValidationError):
            message = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False, include_url=False))
        else:
            message = str(exc)
        print(f"Configuration error: {message}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"Runtime error: {type(exc).__name__}.", file=sys.stderr)
        raise SystemExit(1)
    try:
        from .orchestrator import run
        raise SystemExit(asyncio.run(run(config, rubric, knowledge)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"Runtime error: {type(exc).__name__}. Check retained run artifacts.", file=sys.stderr)
        raise SystemExit(1)
