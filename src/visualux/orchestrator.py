"""Sequential task lifecycle, authorization and durable run artifacts."""

import asyncio
import fnmatch
import importlib.metadata
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright

from .agent import decide
from .auth import HandoffTimeout, handoff
from .browser import AuthenticationRequired, Browser, BrowserBlocked
from .evaluate import evaluate
from .model import Budget, LimitError, Model, ModelError
from .report import summary, task_report
from .rubric import digest
from .trace import TaskTrace, atomic, utc


def verify_downloads(task, data):
    expected = task.expected_download
    if expected is None:
        return data["verification"]
    files = data["downloads"]
    matches = [f for f in files if f["status"] == "completed"
               and (not expected.extension or f["suggested_filename"].lower().endswith(expected.extension.lower()))
               and (not expected.filename_pattern or fnmatch.fnmatchcase(f["suggested_filename"], expected.filename_pattern))]
    return {"method": "download_event_and_declared_filename", "status": "confirmed" if matches else "inconclusive" if any(f["status"] != "completed" for f in files) or not files else "contradicted",
            "artifact_ids": [f["id"] for f in matches], "expectation": expected.model_dump(),
            "limitations": ["Checks receipt, completion and declared filename/extension only; actual file format, contents and freshness are not verified."]}


async def execute_task(browser, trace, planner, knowledge, shared_context):
    config, budget = browser.config, browser.budget
    task = config.tasks[next(i for i, t in enumerate(config.tasks) if t.id == trace.data["task"]["id"])]
    memory = ""
    previous = None
    retries = 0
    ineffective = 0
    last_action = None
    grants = set(config.permissions.allowed_effects)
    assisted = False
    if config.interactive and not browser.auth_setup_done:
        assisted = await handoff(browser, trace, budget) == "assisted"
        browser.auth_setup_done = True
        await browser.page.wait_for_load_state("load", timeout=min(config.limits.navigation_timeout_ms, max(1, budget.remaining() * 1000)))
        await browser.settle()
    while True:
        if budget.remaining() <= 0:
            raise LimitError("interaction_time_limit")
        try:
            observation, png = await browser.observe(trace)
        except AuthenticationRequired:
            if not config.interactive:
                raise
            assisted = (await handoff(browser, trace, budget) == "assisted") or assisted
            await browser.settle()
            continue
        if trace.data["steps"] and trace.data["steps"][-1].get("after") is None:
            trace.data["steps"][-1]["after"] = observation["id"]
            trace.save()
        decision = await decide(planner, task, trace, observation, png, knowledge, memory, shared_context, previous)
        value = decision.model_dump()
        value["id"] = f"decision-{len(trace.data['decisions']):03}"
        trace.data["decisions"].append(value)
        if trace.data["steps"] and trace.data["steps"][-1].get("after") == observation["id"]:
            trace.data["steps"][-1].update(visual_outcome=decision.preceding_outcome, expectation_match=decision.expectation_match, assessment_decision_id=value["id"])
        trace.save()
        memory = decision.memory
        previous = (f"Previous screenshot {observation['id']}", png)
        action = decision.action
        print(f"  {value['id']}: {action.kind} ({len(trace.data['steps'])} browser actions)", flush=True)
        if action.kind == "finish":
            return action.model_dump(exclude={"kind"}) | {"assisted": assisted}
        if action.kind == "request_human":
            if not config.interactive:
                raise BrowserBlocked(action.reason)
            purpose = "authentication" if action.reason == "authentication_required" else "authorization"
            if purpose == "authorization":
                raise BrowserBlocked("authorization_requires_a_concrete_proposed_action")
            assisted = (await handoff(browser, trace, budget) == "assisted") or assisted
            await browser.settle()
            continue
        if action.kind == "observe":
            continue
        if len(trace.data["steps"]) >= config.limits.max_steps:
            raise LimitError("browser_action_limit")
        if action.effect not in grants:
            if not config.interactive:
                raise BrowserBlocked("authorization_required")
            trace.event("approval_requested", effect=action.effect, proposed_action=action.model_dump())
            await handoff(browser, trace, budget, "authorization", action.effect)
            grants.add(action.effect)
            await browser.settle()
            continue
        fingerprint = (action.kind, getattr(action, "text", None), getattr(getattr(action, "target", None), "description", None), getattr(action, "delta_y", None), getattr(action, "key", None))
        ineffective = ineffective + 1 if fingerprint == last_action and decision.expectation_match == "no" else 0
        if ineffective >= 3:
            raise LimitError("repeated_ineffective_action")
        last_action = fingerprint
        target = getattr(action, "target", None)
        selected = binding = measurement = point = None
        try:
            if target:
                selected, binding = await browser.bind(target, png, planner, trace)
                measurement, point = await browser.measure(selected, binding, trace)
                if point is None:
                    raise BrowserBlocked("target_obstructed")
            step = {"id": f"step-{len(trace.data['steps']):03}", "decision_id": value["id"], "before": observation["id"], "after": None,
                    "action": action.model_dump(), "binding_id": binding["id"] if binding else None,
                    "measurement_id": measurement["id"] if measurement else None, "controller_point": point,
                    "at": utc(), "controller_outcome": "unknown", "dispatch_seconds": None, "settle_seconds": None}
            trace.event("action_intent", decision_id=value["id"], action=action.model_dump())
            def dispatched():
                trace.data["steps"].append(step)
                trace.save()
            start = time.monotonic()
            await browser.dispatch(action, selected, point, dispatched)
            step["dispatch_seconds"] = time.monotonic() - start
            step["controller_outcome"] = "dispatched"
            trace.save()
            start = time.monotonic()
            await browser.settle()
            step["settle_seconds"] = time.monotonic() - start
            trace.save()
            retries = 0
        except BrowserBlocked as exc:
            code = str(exc)
            if code not in {"stale_target", "target_binding_unresolved", "target_obstructed"}:
                raise
            trace.event("binding_failure", reason=code, decision_id=value["id"])
            retries += 1
            if retries >= 2:
                raise


async def run(config, rubric, knowledge):
    config.output.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = config.output / run_id
    root.mkdir(mode=0o700)
    atomic(root / "rubric.md", rubric["text"])
    if knowledge is not None:
        atomic(root / "knowledge.md", knowledge)
    manifest = {"schema_version": 1, "id": run_id, "started_at": utc(), "config": config.public(),
                "rubric": {key: value for key, value in rubric.items() if key not in {"text", "criteria"}},
                "knowledge": {"provided": knowledge is not None, "hash": digest(knowledge) if knowledge is not None else None},
                "penalty_rule": "penalty-v1", "tasks": [{"id": t.id, "description": t.description, "status": "pending"} for t in config.tasks],
                "versions": {key: importlib.metadata.version(key) for key in ("visualux-agent", "playwright", "litellm", "pydantic")}, "errors": [], "preflight": [], "usage": {}}
    traces = []
    preflight_passed = set()
    shared_context = []
    context = browser = None
    exit_code = 0
    stop_reason = None

    def checkpoint():
        manifest["usage"] = {"client_requests": sum(len(t.data["model_calls"]) for t in traces),
                             "by_phase": dict(Counter(c["phase"] for t in traces for c in t.data["model_calls"]))}
        atomic(root / "manifest.json", manifest)
        summary(root, manifest)

    checkpoint()
    print(f"Run artifacts: {root.resolve()}", flush=True)
    try:
        async with async_playwright() as playwright:
            engine = await playwright.chromium.launch(headless=not config.interactive)
            try:
                for index, task in enumerate(config.tasks):
                    item = manifest["tasks"][index]
                    if stop_reason:
                        item.update(status="not_started", reason=stop_reason)
                        continue
                    trace = TaskTrace(root, task, config, rubric)
                    traces.append(trace)
                    item.update(status="running", artifacts=f"tasks/{task.id}")
                    checkpoint()
                    budget = Budget(config.limits)
                    planner = Model(config.model, budget, trace.model_call)
                    evaluator = Model(config.evaluator_model or config.model, budget, trace.model_call)
                    result = None
                    cancelled = False
                    eval_start = None
                    print(f"[{index+1}/{len(config.tasks)}] {task.id}: {task.description}", flush=True)
                    try:
                        for model in (planner, evaluator):
                            identity = (model.config.name, model.config.api_base, model.config.key(), model.config.response_mode)
                            if identity not in preflight_passed:
                                setup_start = time.monotonic()
                                await model.preflight(phase="evaluation" if model is evaluator else "preflight")
                                if model is evaluator:
                                    budget.eval_setup_seconds += time.monotonic() - setup_start
                                    budget.eval_started = None
                                preflight_passed.add(identity)
                                manifest["preflight"].append({"requested_model": model.config.name, "task_id": task.id, "status": "passed"})
                        if config.session_mode == "isolated" or context is None:
                            if context:
                                await context.close()
                            context = await engine.new_context(viewport=config.viewport.model_dump(), device_scale_factor=1,
                                                               storage_state=str(config.auth_state) if config.auth_state else None, accept_downloads=True)
                            browser = Browser(context, config, budget)
                            await browser.start()
                            browser.trace = trace
                            if config.interactive:
                                browser.human = True
                            await browser.navigate(config.url)
                            browser.human = False
                        else:
                            browser.budget = budget
                            browser.trace = trace
                            browser.invalidate()
                        result = await execute_task(browser, trace, planner, knowledge, shared_context if config.session_mode == "shared" else [])
                    except asyncio.CancelledError:
                        result = {"status": "uncertain", "reason": "cancelled"}
                        cancelled, exit_code, stop_reason = True, 130, "cancelled"
                        trace.data["execution_status"] = "cancelled"
                    except HandoffTimeout:
                        result = {"status": "blocked", "reason": "human_handoff_timeout"}
                        exit_code, stop_reason = 1, "human_handoff_timeout"
                    except (LimitError, PlaywrightTimeout, TimeoutError) as exc:
                        reason = str(exc) if isinstance(exc, LimitError) else "browser_operation_timeout"
                        result = {"status": "uncertain", "reason": reason}
                        trace.event("limit", reason=reason)
                        if browser and browser.trace == trace:
                            try:
                                await browser.observe(trace, partial=True)
                            except Exception:
                                pass
                    except BrowserBlocked as exc:
                        result = {"status": "blocked", "reason": str(exc)}
                        trace.event("blocked", reason=str(exc))
                    except Exception as exc:
                        result = {"status": "uncertain", "reason": str(exc) if isinstance(exc, ModelError) else "infrastructure_error"}
                        trace.data["errors"].append({"phase": "execution", "code": type(exc).__name__})
                        trace.data["execution_status"] = "error"
                        exit_code, stop_reason = 1, "infrastructure_error"
                    finally:
                        trace.data["result"] = {"answer": None, "scope": "", "limitations": [], "evidence": [], "assisted": any(h.get("result") == "assisted" for h in trace.data["handoffs"]), **(result or {"status": "uncertain", "reason": "interrupted"}),
                                                "task_id": task.id, "knowledge_provided": knowledge is not None, "knowledge_hash": manifest["knowledge"]["hash"],
                                                "guidance": "Domain knowledge supplied; unaided first-use discoverability was not established. Explicit navigation hints, if present, are supplied guidance." if knowledge else "No supplied domain knowledge."}
                        if browser and browser.trace == trace:
                            await browser.finish_downloads()
                        trace.data["verification"] = verify_downloads(task, trace.data)
                        trace.save()
                    if not cancelled and trace.data["observations"]:
                        eval_start = time.monotonic()
                        budget.eval_started = eval_start
                        try:
                            await evaluate(evaluator, trace, rubric, knowledge)
                        except asyncio.CancelledError:
                            cancelled, exit_code, stop_reason = True, 130, "cancelled"
                            trace.data["execution_status"] = "cancelled"
                            trace.data["evaluation"]["limitations"].append("Evaluation cancelled; only checkpointed evidence was reviewed.")
                        if any(e.get("phase") == "evaluation" for e in trace.data["errors"]):
                            exit_code = 130 if cancelled else 1
                    trace.data["finished_at"] = utc()
                    if trace.data["execution_status"] == "running":
                        trace.data["execution_status"] = "finished"
                    finished = time.monotonic()
                    trace.data["metrics"] = {
                        "wall_seconds": finished - budget.started,
                        "active_execution_seconds": (eval_start or finished) - budget.started - budget.paused,
                        "human_pause_seconds": budget.paused, "evaluation_seconds": finished - eval_start if eval_start else 0,
                        "actions": dict(Counter(s["action"]["kind"] for s in trace.data["steps"])), "total_browser_steps": len(trace.data["steps"]),
                        "observation_only_turns": sum(d["action"]["kind"] == "observe" for d in trace.data["decisions"]),
                        "binding_attempts": len(trace.data["bindings"]), "binding_failures": sum(b["status"] != "bound" for b in trace.data["bindings"]),
                        "browser_dispatch_seconds": sum(s.get("dispatch_seconds") or 0 for s in trace.data["steps"]),
                        "browser_settling_seconds": sum(event["seconds"] for event in trace.data["events"] if event["kind"] == "render_wait"),
                        "navigation_wait_seconds": sum(event["seconds"] for event in trace.data["events"] if event["kind"] == "navigation_wait"),
                        "model_seconds_by_phase": {phase: sum(c["seconds"] for c in trace.data["model_calls"] if c["phase"] == phase) for phase in {c["phase"] for c in trace.data["model_calls"]}},
                        "requests": budget.requests,
                    }
                    trace.save()
                    task_report(trace)
                    outcome = trace.data["result"]
                    item.update(status=outcome["status"], reason=outcome["reason"], execution_status=trace.data["execution_status"],
                                assisted=outcome["assisted"], verification=trace.data["verification"]["status"], penalty=trace.data["penalty"])
                    print(f"  → {outcome['status']}: {outcome['reason']} | evaluation {trace.data['evaluation']['status']}", flush=True)
                    checkpoint()
                    if config.session_mode == "shared":
                        if outcome["status"] != "completed" or (task.expected_download and trace.data["verification"]["status"] != "confirmed"):
                            stop_reason = stop_reason or "upstream_task_incomplete"
                        shared_context.append({"task_id": task.id, "outcome": outcome["answer"], "scope": outcome["scope"], "evidence": [{"task_id": task.id, **ref} for ref in outcome["evidence"]]})
                        shared_context = shared_context[-3:]
            finally:
                await engine.close()
    except asyncio.CancelledError:
        exit_code, stop_reason = 130, "cancelled"
    except Exception as exc:
        manifest["errors"].append({"phase": "run", "code": type(exc).__name__})
        exit_code, stop_reason = 1, "infrastructure_error"
    finally:
        for item in manifest["tasks"]:
            if item["status"] in {"pending", "running"}:
                item.update(status="not_started" if item["status"] == "pending" else "uncertain", reason=stop_reason or "run_interrupted")
        manifest["finished_at"] = utc()
        manifest["exit_code"] = exit_code
        checkpoint()
    print(f"HTML report: {(root / 'index.html').resolve()}", flush=True)
    return exit_code
