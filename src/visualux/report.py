"""Static, escaped English HTML and Markdown from the same recorded data."""

import html
import json
import re
from collections import Counter
from urllib.parse import quote

from .trace import atomic


CSS = """body{font:15px/1.6 system-ui,sans-serif;background:#f3f5f1;color:#203025;margin:0}main{max-width:1080px;margin:auto;padding:48px 28px}h1{font-size:36px;letter-spacing:-.04em;line-height:1.15}h2{margin-top:36px;font-size:21px}h3{font-size:17px}a{color:#226340;outline-offset:4px}header{border-bottom:1px solid #d8dfd2;padding-bottom:24px}section,.finding,details{background:white;border:1px solid #dce2d6;border-radius:8px;padding:18px 22px;margin:14px 0}table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;border-bottom:1px solid #dce2d6;padding:12px;vertical-align:top}th{font-size:12px;color:#61715b;text-transform:uppercase}img{max-width:100%;height:auto;border:1px solid #dce2d6}pre{overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;background:#eef2e8;padding:12px}summary{cursor:pointer;font-weight:600}small,.muted{color:#66765d}.status{display:inline-block;background:#e8eee0;padding:3px 10px;border-radius:5px;font-size:13px}.eyebrow{font-size:11px;letter-spacing:.12em}.finding p{margin:7px 0}nav{display:flex;gap:16px;flex-wrap:wrap}.evidence{font-size:13px}footer{margin-top:40px;color:#728069;font-size:12px}details img{margin:10px 0}@media(max-width:600px){main{padding:24px 12px}td,th{padding:7px}h1{font-size:28px}}"""


def esc(value):
    return html.escape(str(value), quote=True)


def md(value):
    return re.sub(r"([\\`*_{}\[\]()#!|>])", r"\\\1", html.escape(str(value)))


def page(title, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style><body><main>{body}<footer>VisualUX · Evidence-based observations, not calibrated human-usability scores.</footer></main></body></html>'


def evidence_html(refs, data):
    observations = {o["id"]: o for o in data["observations"]}
    links = []
    for ref in refs:
        oid = ref["observation_id"]
        if oid in observations:
            links.append(f'<a href="{quote(observations[oid]["path"])}">{esc(oid)}</a>' + (f' / {esc(ref["step_id"])}' if ref.get("step_id") else "") + (f' / {esc(ref["measurement_id"])}' if ref.get("measurement_id") else ""))
    return " · ".join(links)


def task_report(trace):
    data = trace.data
    task, result = data["task"], data["result"] or {}
    evaluation, penalty = data["evaluation"], data["penalty"]
    title = f"{task['id']} — {result.get('status', 'unavailable')}"
    lines = [f"# {md(title)}", "", md(task["description"]), "", f"Status: **{md(result.get('status', 'unavailable'))}** — {md(result.get('reason', 'unknown'))}", ""]
    body = f'<header><div class="eyebrow">VISUALUX / TASK REPORT</div><h1>{esc(task["id"])}</h1><p>{esc(task["description"])}</p><span class="status">{esc(result.get("status", "unavailable"))}</span> {esc(result.get("reason", "unknown"))}<p><a href="../../index.html">← Run summary</a></p></header>'

    def section(heading, text):
        nonlocal body
        lines.extend([f"## {heading}", "", md(text), ""])
        body += f'<h2>{esc(heading)}</h2><section>{esc(text).replace(chr(10), "<br>")}</section>'

    section("Task outcome", f"Answer: {result.get('answer') or 'No answer recorded.'}\nScope: {result.get('scope') or 'Not established'}\nAssisted: {result.get('assisted', False)}\nKnowledge supplied: {result.get('knowledge_provided', False)}\nKnowledge hash: {result.get('knowledge_hash') or 'None'}\nGuidance/context: {result.get('guidance', 'No supplied knowledge.')}\nLimitations: {'; '.join(result.get('limitations', [])) or 'None recorded'}")
    lines.append("Evidence: " + ", ".join(f'[{ref["observation_id"]}]({ref["observation_id"].replace("obs-", "screenshots/obs-")}.png)' for ref in result.get("evidence", [])))
    body += '<p class="evidence">' + evidence_html(result.get("evidence", []), data) + '</p>'
    section("Test information", json.dumps({"target": data["config"]["url"], "viewport": data["config"]["viewport"], "session_mode": data["config"]["session_mode"], "model": data["config"]["model"], "rubric_hash": data["rubric_hash"], "penalty_rule": penalty["rule"]}, indent=2))
    section("Verification", json.dumps(data["verification"], ensure_ascii=False, indent=2))
    body += '<nav><a href="trace.json">JSON trace</a><a href="../../rubric.md">Effective rubric</a><a href="../../manifest.json">Run configuration and provenance</a></nav>'
    if result.get("knowledge_provided"):
        body += '<p><a href="../../knowledge.md">Supplied knowledge snapshot</a></p>'
    section("Interaction summary", "\n".join(f'{d["id"]}: {d["visible_summary"]}\n{d["rationale"]}\nOutcome of preceding action: {d["preceding_outcome"] or "Not assessed"}' for d in data["decisions"]) or "No model decisions were recorded.")
    body += '<h2>Concerns and effective design</h2>'
    lines += ["## Concerns and effective design", ""]
    for finding in evaluation["findings"]:
        scoring = f'{finding.get("penalty")} penalty points' if finding.get("penalty") is not None else f'Unscored: {finding.get("unscored_reason", "Not assessed")}'
        text = f'{finding["kind"]} · {finding["certainty"]} · {finding["attribution"]} · {finding.get("severity") or "No severity"}\nCriteria: {", ".join(finding["criterion_ids"])}\nFacts: {finding["facts"]}\nInterpretation: {finding["interpretation"]}\nImpact: {finding["impact"]}\nSuggestion: {finding["suggestion"]}\n{scoring}\nOccurrences: {finding["occurrences"]}'
        lines += [f'### {md(finding["id"])}: {md(finding["title"])}', "", md(text), ""]
        for ref in finding["evidence"]:
            lines.append(f'- [{md(ref["observation_id"])}](screenshots/{quote(ref["observation_id"])}.png)')
        body += f'<article class="finding"><h3>{esc(finding["id"])}: {esc(finding["title"])}</h3><p>{esc(text).replace(chr(10), "<br>")}</p><p class="evidence">{evidence_html(finding["evidence"],data)}</p></article>'
    if not evaluation["findings"]:
        text = "No supported issues observed within the evaluated coverage; this does not certify usability." if evaluation["status"] == "complete" else "Evaluation is incomplete or unavailable. An empty list does not mean no issues."
        lines.extend([text, ""])
        body += f'<p>{esc(text)}</p>'
    section("Penalty breakdown", f"Rule: {penalty['rule']}\nEvaluation status: {penalty['status']}\n{'Observed subtotal' if penalty['status']=='partial' else 'Total'}: {penalty['total'] if penalty['total'] is not None else 'Unavailable'} penalty points\nEach distinct supported UX issue counts once per task. Heuristic weights: low 1, medium 3, high 5. No 100-point grade or product-wide score. Tentative concerns may remain even with zero points.")
    section("Difficulty dispositions", json.dumps(evaluation.get("dispositions", []), ensure_ascii=False, indent=2))
    section("Metrics", json.dumps(data["metrics"], ensure_ascii=False, indent=2) + "\nPhase totals include their model/browser components; do not add these components again. Durations are not human task times.")
    section("Measurements", json.dumps(data["measurements"], ensure_ascii=False, indent=2))
    lines += ["## Trace and screenshots", ""]
    body += '<h2>Trace and screenshots</h2>'
    for step in data["steps"]:
        detail = json.dumps(step, ensure_ascii=False, indent=2)
        lines += [f'### {md(step["id"])} — {md(step["action"]["kind"])}', "", md(detail), ""]
        body += f'<details><summary>{esc(step["id"])} — {esc(step["action"]["kind"])} — {esc(step["controller_outcome"])}</summary><pre>{esc(detail)}</pre>'
        for oid in dict.fromkeys([step.get("before"), step.get("after")]):
            if oid:
                body += f'<p><a href="screenshots/{quote(oid)}.png">{esc(oid)}</a></p><img src="screenshots/{quote(oid)}.png" alt="Recorded screenshot {esc(oid)} for {esc(step["id"])}">'
                lines += [f'[{md(oid)}](screenshots/{quote(oid)}.png)', ""]
        body += '</details>'
    if not data["steps"]:
        for observation in data["observations"]:
            body += f'<section><a href="{quote(observation["path"])}">{esc(observation["id"])}</a><img src="{quote(observation["path"])}" alt="Recorded screenshot {esc(observation["id"])}"></section>'
            lines.append(f'[{md(observation["id"])}]({quote(observation["path"])})')
    section("Limitations and coverage", json.dumps({key: value for key, value in evaluation.items() if key not in {"findings", "dispositions"}}, indent=2) + "\n" + json.dumps(data["errors"], indent=2))
    for download in data["downloads"]:
        if download.get("path"):
            body += f'<p><a href="{quote(download["path"])}" download>Download artifact {esc(download["id"])}</a></p>'
    atomic(trace.root / "report.md", "\n".join(lines) + "\n")
    atomic(trace.root / "report.html", page(title, body))


def summary(root, manifest):
    body = '<header><div class="eyebrow">VISUALUX / RUN SUMMARY</div><h1>Visual task review</h1><p>' + esc(manifest["id"]) + '</p><p>Session mode: ' + esc(manifest["config"]["session_mode"]) + '</p></header>'
    body += '<nav><a href="summary.md">Markdown summary</a><a href="manifest.json">Manifest</a><a href="rubric.md">Effective rubric</a></nav><h2>Tasks</h2><table><thead><tr><th>Task</th><th>Outcome</th><th>Verification / assistance</th><th>Penalty points</th><th>Evaluation</th></tr></thead><tbody>'
    lines = ["# VisualUX run summary", "", f"Run: {md(manifest['id'])}", f"Session mode: {md(manifest['config']['session_mode'])}", "", "| Task | Status | Reason | Penalty points | Evaluation |", "| --- | --- | --- | --- | --- |"]
    for item in manifest["tasks"]:
        status, reason = item.get("status", "pending"), item.get("reason", "")
        points = item.get("penalty", {}).get("total")
        evaluation = item.get("penalty", {}).get("status", "unavailable")
        label = esc(item["id"])
        mdlabel = md(item["id"])
        if item.get("artifacts"):
            label = f'<a href="tasks/{quote(item["id"])}/report.html">{label}</a>'
            mdlabel = f'[{mdlabel}](tasks/{quote(item["id"])}/report.md)'
        body += f'<tr><td>{label}<br><small>{esc(item["description"])}</small></td><td>{esc(status)}<br><small>{esc(reason)}</small></td><td>{esc(item.get("verification", "not-run"))}<br>Assisted: {esc(item.get("assisted", "unknown"))}</td><td>{points if points is not None else "Unavailable"}</td><td>{esc(evaluation)}</td></tr>'
        lines.append(f'| {mdlabel} | {md(status)} | {md(reason)} | {points if points is not None else "Unavailable"} | {md(evaluation)} |')
    body += '</tbody></table><p>Penalty points are per-task heuristics, not an overall usability grade. An unstarted or unevaluated task has no score.</p>'
    body += '<h2>Coverage and provenance</h2><section><pre>' + esc(json.dumps({"rubric": manifest["rubric"], "knowledge": manifest.get("knowledge"), "errors": manifest.get("errors", []), "model_usage": manifest.get("usage", {})}, indent=2)) + '</pre></section>'
    lines += ["", "Penalty points are heuristic and are not summed across tasks. See per-task reports for coverage, assistance, verification, and evidence.", "", "[Effective rubric](rubric.md) · [Manifest](manifest.json)"]
    atomic(root / "summary.md", "\n".join(lines) + "\n")
    atomic(root / "index.html", page("VisualUX run summary", body))
