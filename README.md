# UX Review Agent

UX Review Agent that assesses web app usability/UX by trying to use an app as a user would: looking at the screen, pursuing a goal, and working through the interface. Its task decisions are grounded in screenshots, rather than DOM text or structure, so the review centers on what a user can see and understand.

Run it locally as a CLI: supply a URL and tasks; receive answers, chronological evidence, supported UX findings, and English HTML/Markdown reports with JSON traces.

**Status: initial MVP implementation.** The CLI, three bundled mock apps, task execution, human authentication handoff, evaluation, and static reports are implemented. Automated Chromium and scripted gateway tests cover engineering contracts. **Real-model usability accuracy and the LiteLLM + Gemma 4 31B deployment have not been validated.** The first live trial and actual SSO/manual-login smoke test remain pending.

[Design and architecture](design.md) defines the product contract, evidence boundaries, and acceptance requirements.

## Setup

Use Python 3.11+ in an isolated environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
playwright install chromium
```

If you use uv, `uv sync --locked` creates the environment from the checked-in lockfile; then run `.venv/bin/playwright install chromium`. No system Python changes, model-server deployment, or model-weight download are required. Linux hosts may need Playwright's browser system libraries; follow the error's installation guidance if Chromium cannot start.

## Start with the mock apps

```bash
visualux demo
```

Open the printed URL, normally `http://127.0.0.1:8765`. The gallery links to a **Setup guide & walkthroughs** at `/guide.html`, covering installation, two-terminal setup, model configuration, task commands, reset behavior, and manual verification. Use `--port` for another loopback port. Stop with Ctrl+C. Browsing the apps requires no model or credentials.

| App | Workflow (two document navigations) | Deliberately confusing variant |
| --- | --- | --- |
| Lab Monitor | Laboratories → paginated/searchable devices → warning or calibration details | Ambiguous units/status codes, tiny detail links, weak warning salience |
| Report Center | Report catalogue → filtered records → export configuration and CSV download | Abbreviated filter, vague Process controls, weak download feedback |
| Device Setup | Inventory → registration form and validation recovery → saved record | Unexplained fields, late requirements, lost input after validation errors |

Each representative task requires multiple interactions across three screens. Examples start at the app entry page, without issue annotations or good/bad labels. Both variants have equivalent data and core operations. All data is synthetic. Created devices survive navigation and refresh; **Reset demo data** clears them and returns to the app entry. Variants share storage within a browser context, so reset between manual comparisons. The same assets are reused in browser tests; private checks are outside the served directory.

## Configure your model and run

Copy [examples/lab.toml](examples/lab.toml), [examples/reports.toml](examples/reports.toml), or [examples/device.toml](examples/device.toml) to a neighboring `*.local.toml` file (ignored by Git). Keeping it in the same directory preserves relative knowledge/output paths. Installed wheels also include copies under the environment's `share/visualux/examples` directory; copy that directory to a writable workspace first. Replace the gateway alias and endpoint with your deployment's actual values. Supply its inference key through the named environment variable.

An existing LiteLLM gateway uses:

```toml
[model]
name = "litellm_proxy/YOUR_EXISTING_ALIAS"
api_base = "http://127.0.0.1:4000"
api_key_env = "LITELLM_PROXY_API_KEY"
response_mode = "prompted"
# context_tokens = 32768  # use the actual limit of your deployment
```

The `litellm_proxy/` prefix selects LiteLLM's client route; the remaining alias, including slashes, is forwarded unchanged. No second gateway, admin key, upstream provider credentials, or server reconfiguration is needed. Direct providers and local OpenAI-compatible servers can use their LiteLLM provider/model identifier and explicit endpoint instead.

```bash
cp examples/lab.toml examples/lab.local.toml
# Edit examples/lab.local.toml and export LITELLM_PROXY_API_KEY first.
visualux run --config examples/lab.local.toml
```

To compare demo variants, change `variant=clear` to `variant=confusing` in the URL and run again with the same task/configuration. Each run gets its own artifact directory. There is no automatic product-wide ranking.

The examples allow longer initial time budgets for local-model trials. These are engineering allowances, not measured Gemma latency guarantees. A synthetic-image preflight checks each distinct route once; runtime schema validation remains mandatory. A passed preflight does not establish dense-table reading or UI accuracy. The first intended real trial is **LiteLLM + Gemma 4 31B**, currently unverified.

`response_mode` can explicitly be `prompted` (default), `json`, or `schema`. Prompted mode sends the schema in instructions and validates the response. Choose the others only when your serving stack supports them; there is no silent mode/provider fallback. Only final response content is parsed, not provider reasoning fields.

## Tasks, sessions, and authentication

For CLI-defined tasks, use a configuration without `[[tasks]]`, then repeat `--task`:

```bash
visualux run --config model-only.toml --url http://localhost:4200 \
  --task "Is the selected lab healthy? Identify any exception." \
  --task "Which device has the highest temperature, and what is it?" \
  --interactive --output ./reports
```

Use either CLI tasks or file-defined tasks; mixing them is rejected. Explicit CLI options override file values. TOML paths resolve relative to the configuration file; CLI paths resolve relative to the working directory.

Independent tasks start at the supplied URL in fresh browser contexts, with fresh agent memory and the same initial auth/knowledge inputs. Browser isolation does **not** undo backend writes. Use suitable test accounts/reset data when server state must match. Set `session_mode = "shared"` for intentionally dependent tasks; it preserves the current page and bounded prior visual context. Shared sequences stop after an incomplete task or a failed configured verification.

`--interactive` opens a headed browser and pauses before the first application screenshot or application model call. Complete authentication, return to the application, and type `resume`; use `assisted` if you also guided or operated the task. Handoffs suspend capture, binding and model decisions. Expired sessions can use the same path; unattended auth gates produce a blocked result. Interactive use requires a real terminal and local display.

To save a session without model calls:

```bash
visualux auth --url http://localhost:4200 --save-state ./.auth/app.json
```

Then use `auth_state = "./.auth/app.json"` in TOML, or `--auth-state`. Saved states are sensitive and may not work for every SSO system. Keep them out of version control and reports. The tool does not enter credentials.

## Domain knowledge and permissions

Optionally supply concepts, business rules, field explanations and non-secret test values in a UTF-8 Markdown file (initial limit 16 KiB):

```bash
visualux run --config examples/device.toml --knowledge examples/knowledge.md
```

Or set top-level `knowledge_file`. The exact text is snapshotted as `knowledge.md` and sent as labeled reference context to the planner and evaluator, never the private target matcher. Application state and outcomes still need screenshot evidence. Knowledge-conditioned runs do not establish unaided first-use discoverability; supplied navigation guidance must be disclosed. Avoid credentials, hidden selectors, correct click paths and answer oracles.

Default allowed effects are `inspect`, `navigate`, `filter`, and `download`. Additional operations need explicit task-scoped permission. The device example grants `create` for its synthetic task. Interactive mode can show a concrete proposed action and obtain approval; unattended unauthorized actions are blocked. Knowledge and custom criteria do not authorize actions. This guard relies on model effect classification and is not a universal production safety barrier.

Default navigation scope is the target origin. `allowed_origins` adds application origins; `login_origins` identifies human-only authentication origins. Origin checks do not sandbox every page subresource.

## Customize evaluation

Override or add readable criterion blocks in a Markdown file:

```markdown
## information.comprehension

Assess visible scope, units and freshness. A clear all-green summary is valid.

## lab.warning_details

Assess whether a visible warning identifies the device and explains its cause.
```

Configure it with:

```toml
[evaluation]
criteria_file = "./ux-criteria.md"
disabled_criteria = ["semantics.feedback"]
```

Matching IDs replace default guidance; new IDs add rules; unmentioned defaults stay enabled. Disabling takes precedence. Invalid IDs, empty/duplicate blocks, unknown disabled IDs, and an empty effective rubric are rejected before browsing.

```bash
visualux rubric --config visualux.toml
```

The preview uses the same resolver as execution and needs no model credentials. Every run snapshots the effective rubric and its provenance. See [rubric contract](design.md#91-rubric).

## Reports and evidence

```text
reports/<run-id>/
  index.html
  summary.md
  manifest.json
  rubric.md
  knowledge.md          # only when supplied
  tasks/<task-id>/
    report.html
    report.md
    trace.json
    screenshots/
    downloads/          # when received
```

Open `index.html` directly. HTML uses no JavaScript, external assets, or server; move the whole folder to preserve evidence links. Generated prose and labels are English; original tasks and quoted UI evidence retain their wording. HTML and Markdown share the same validated results, metrics, findings and penalty arithmetic.

Reports distinguish task outcome, independent verification, assistance, tool failure and evaluation coverage. Zero-click answers and zero supported findings are valid. Every noticed difficulty is retained with evidence and a disposition. Supported UX concerns receive low/medium/high penalties of **1/3/5 points**, once per distinct issue per task. Tentative and non-UX concerns remain unscored. Partial evaluation shows a partial subtotal; failed evaluation or incomplete issue deduplication has no total. These are heuristic penalty points, not a 100-point grade or calibrated human-usability score.

To configure an independent download check, add this under a task:

```toml
[tasks.expected_download]
extension = ".csv"
filename_pattern = "lab-report.csv"
```

This verifies receipt, completion, and declared filename/extension only. It does not certify the actual file format, contents, or freshness, and downloaded contents never guide the agent.

## Limits and data handling

- **Unsupported interactions:** canvas-internal clicks, file uploads, drag-and-drop, general multi-tab application workflows, and native dialogs. Visible canvas content can be read; downloads are supported. An unsupported interaction blocks the task with a tool limitation, without a UX deduction for that limitation.
- **Binding coverage:** main-document DOM controls with recognizable native/role/focus semantics; iframe controls, arbitrary click handlers and more than 80 visible candidates can block matching. The task planner receives no DOM labels or selectors. Private matching checks the already chosen target and never selects a different intended feature.
- **Loading:** initial navigation waits for `load` plus a default 500-ms settling allowance. SPA data may arrive later; visible loading leads to bounded waits. `networkidle` is not a universal readiness gate. A load timeout is recorded with safely available partial evidence.
- **Working context:** one logical task memory, normally current/preceding screenshots and compact recent history. `[limits].max_images` defaults to 4 across the complete request (configurable 2–32). Old images stay in artifacts and post-run evaluation; automatic retrieval of arbitrary old images into planning is not implemented. `context_tokens` enables conservative text/image/output budgeting, not exact provider accounting. Unknown capacity stays unknown.
- **Budgets:** defaults are 30 dispatched actions, 180 active seconds, 90 interaction requests, 16 evaluation requests, 180 evaluation seconds, and 45 seconds per request. Model retries and matching consume these budgets. Human pauses are separate. See [complete limits](design.md#63-budgets-and-termination).
- **Data flow:** screenshots, tasks, and optional knowledge go to the configured endpoint and any gateway backends/logging it uses. Localhost alone does not guarantee local inference. Use sanitized test data or suitable infrastructure. No automatic PII redaction is provided; the exact knowledge snapshot and screenshots remain in local artifacts.
- **Interpretation:** model errors and missed state changes remain possible. Target rectangles and sampled obstruction do not prove effective click regions, human error rates, or WCAG compliance. Reports support investigation, not professional usability testing or general functional testing.

Exit codes: `0` means execution/reporting completed, including ordinary failed/blocked/uncertain task outcomes; `1` means runtime or evaluation failure; `2` invalid configuration; `130` cancellation. Available evidence is checkpointed atomically, and interrupted actions are never replayed automatically.

## Development and validation

Runtime dependencies are pinned Playwright, LiteLLM, and Pydantic. The CLI uses `argparse`; demos, HTML rendering, configuration, and persistence use standard-library facilities. There is no Typer, Pillow, frontend build, dashboard service, or additional gateway.

Run deterministic tests (includes real headless Chromium against the local demos):

```bash
python -m unittest discover -s tests -v
```

These walk all six demo variants across two document navigations, check filtering, CSV contents, validation recovery, and saved records after refresh, and cover action/schema validation, evidence references, permissions, session continuation, target geometry/staleness, handoff capture suspension, escaping, transport, context limits, and evaluation/deduplication. The scripted endpoint checks orchestration, **not real-model visual or UX quality**.

To opt into the real-model matrix, point `VISUALUX_LIVE_CONFIG` to a configured TOML file with your endpoint/alias and run `python -m unittest discover -s tests -p test_live.py -v`. This makes real model requests and retains run artifacts. Private expected facts stay in `tests/cases/`; the harness never gives them to the planner, matcher or evaluator. Review findings and missed issues manually as well as inspecting deterministic answer checks.

The Gemma gateway trial, real SSO/manual headed login, model accuracy/latency and provider-specific token calibration remain unverified. [Design milestones and acceptance](design.md#13-implementation-milestones-and-acceptance) describe the retained requirements and validation boundaries.

## License

This project is licensed under the [MIT License](LICENSE).
