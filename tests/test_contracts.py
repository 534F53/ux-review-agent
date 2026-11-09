import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from visualux.config import Config, CriteriaConfig, Limits, ModelConfig, TaskSpec, load_config, load_knowledge, safe_url
from visualux.evaluate import score
from visualux.model import Budget, LimitError, Model, ModelError, image_tokens, solid_png
from visualux.report import task_report
from visualux.rubric import parse, resolve
from visualux.schemas import Decision, Preflight
from visualux.trace import TaskTrace, atomic, validate_evidence


def config(root, **kwargs):
    return Config(url="http://127.0.0.1:8765/lab.html", output=root / "out", model=ModelConfig(name="litellm_proxy/test/vision", api_base="http://127.0.0.1:9999"), tasks=[TaskSpec(id="lab", description="Read the overview")], **kwargs)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_config_paths_precedence_and_task_exclusivity(self):
        path = self.root / "run.toml"
        path.write_text('url="http://localhost:8765"\noutput="./artifacts"\nknowledge_file="./info.md"\n[model]\nname="litellm_proxy/path/model"\napi_base="http://localhost:4000"\n[[tasks]]\nid="first"\ndescription="Read"\n')
        loaded = load_config(path, {"url": "http://localhost:8765/lab.html"})
        self.assertEqual(loaded.knowledge_file, self.root / "info.md")
        self.assertEqual(loaded.output, self.root / "artifacts")
        self.assertEqual(loaded.model.name, "litellm_proxy/path/model")
        with self.assertRaises(ValueError):
            load_config(path, {"task": ["Replacement"]})
        with self.assertRaises(ValidationError):
            Config(url="https://host.test", model=ModelConfig(name="x"), tasks=[TaskSpec(id="x", description="a"), TaskSpec(id="x", description="b")])

    def test_secret_validation_and_url_sanitizing(self):
        with self.assertRaises(ValueError):
            ModelConfig(name="litellm_proxy/model")
        model = ModelConfig(name="litellm_proxy/model", api_base="http://localhost:4000", api_key_env="VISUALUX_TEST_MISSING_KEY")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            model.key()
        with self.assertRaises(ValidationError):
            ModelConfig(name="x", api_base="http://localhost:4000?token=secret")
        self.assertEqual(safe_url("https://user:password@host.test/app?token=secret#secret"), "https://host.test/app")

    def test_knowledge_boundary(self):
        file = self.root / "knowledge.md"
        for data in (b"", b" \n", b"\xff", b"a" * 16385):
            file.write_bytes(data)
            with self.assertRaises((ValueError, UnicodeError)):
                load_knowledge(file)
        file.write_text("A lab groups devices.\n")
        self.assertEqual(load_knowledge(file), "A lab groups devices.\n")
        self.assertIsNone(load_knowledge(None))

    def test_criteria_resolution_and_errors(self):
        custom = self.root / "criteria.md"
        custom.write_text("## information.comprehension\n\nReplacement\n\n## lab.details\n\nInspect detail.\n")
        resolved = resolve(CriteriaConfig(criteria_file=custom, disabled_criteria=["semantics.feedback"]))
        self.assertEqual(resolved["actions"]["information.comprehension"], "replaced")
        self.assertEqual(resolved["actions"]["lab.details"], "added")
        self.assertNotIn("semantics.feedback", resolved["criteria"])
        self.assertIn("interaction.target_access", resolved["criteria"])
        for text in ("## a\n\n", "## a\nx\n## a\ny", "## Wrong ID\nx", "no headings"):
            with self.assertRaises(ValueError):
                parse(text)
        with self.assertRaises(ValueError):
            resolve(CriteriaConfig(disabled_criteria=["typo"]))

    def test_invalid_actions_do_not_parse(self):
        base = {"observation_id": "obs-000", "visible_summary": "A page", "rationale": "Read it"}
        for action in (
            {"kind": "evaluate_js", "code": "document.cookie"},
            {"kind": "click", "effect": "inspect", "expected": "Open", "target": {"observation_id": "obs-000", "description": "Export in the top right", "selector": "#secret"}},
            {"kind": "keypress", "key": "Control+L", "effect": "inspect", "expected": "Inspect"},
            {"kind": "wait", "duration_ms": 999999, "effect": "inspect", "expected": "Loaded"},
            {"kind": "finish", "status": "completed", "reason": "Done", "evidence": []},
        ):
            with self.assertRaises(ValidationError):
                Decision.model_validate({**base, "action": action})

    def test_penalties_are_evidence_qualified_and_not_duplicated(self):
        def finding(fid, severity="low", **kwargs):
            return {"id": fid, "kind": "concern", "certainty": "supported", "attribution": "ux", "severity": severity, "impact": "Supported friction", "evidence": [{"observation_id": "obs-000", "step_id": "step-000"}], **kwargs}
        evaluation = {"status": "complete", "findings": [finding("a"), finding("b", "medium"), finding("c", "high"), finding("tentative", certainty="tentative"), finding("controller", attribution="controller"), finding("strength", kind="strength"), finding("geometry", "high", evidence=[{"observation_id": "obs-000", "measurement_id": "measure-000"}])]}
        self.assertEqual(score(evaluation)["total"], 9)
        self.assertIsNone(evaluation["findings"][-1]["penalty"])
        self.assertEqual(score({"status": "partial", "findings": []})["status"], "partial")
        self.assertIsNone(score({"status": "unavailable", "findings": []})["total"])
        with self.assertRaises(ValueError):
            score({"status": "complete", "findings": [finding("a"), finding("a")]})

    def test_atomic_evidence_and_safe_html(self):
        runroot = self.root / "run"
        runroot.mkdir()
        cfg = config(self.root)
        cfg.tasks[0].description = '<script>alert("task")</script>'
        trace = TaskTrace(runroot, cfg.tasks[0], cfg, resolve(CriteriaConfig()))
        observation = trace.observation(solid_png("red"), cfg.viewport.model_dump())
        validate_evidence([{"observation_id": observation["id"]}], trace.data)
        with self.assertRaises(ValueError):
            validate_evidence([{"observation_id": "invented"}], trace.data)
        trace.data["result"] = {"status": "uncertain", "reason": "test", "answer": '<img src="https://example.invalid/tracker" onerror="alert(1)">'}
        task_report(trace)
        output = (trace.root / "report.html").read_text()
        self.assertNotIn('<script>alert', output)
        self.assertNotIn('<img src="https:', output)
        self.assertIn('&lt;script&gt;', output)
        self.assertIn('screenshots/obs-000.png', output)
        self.assertNotIn("No supported issues observed", output)
        atomic(runroot / "test.json", {"complete": True})
        self.assertTrue(json.loads((runroot / "test.json").read_text())["complete"])

    def test_budget_and_image_estimate(self):
        budget = Budget(Limits(interaction_requests=1))
        budget.request("binding")
        with self.assertRaises(LimitError):
            budget.request("planning")
        self.assertGreater(image_tokens(solid_png("blue")), 0)


class ModelBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_mandatory_context_rejected_before_network(self):
        model = Model(ModelConfig(name="test/model", context_tokens=2048, max_output_tokens=1024), Budget(Limits()), lambda item: None)
        with self.assertRaises(ModelError):
            await model.ask(Preflight, "a" * 4096, {}, [("Current", solid_png("red"))])

    async def test_entire_request_image_limit(self):
        model = Model(ModelConfig(name="test/model"), Budget(Limits(max_images=4)), lambda item: None)
        with self.assertRaises(ModelError):
            await model.ask(Preflight, "Read", {}, [(str(i), solid_png("red")) for i in range(5)])


if __name__ == "__main__":
    unittest.main()
