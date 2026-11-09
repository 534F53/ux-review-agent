import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from visualux.config import Config, CriteriaConfig, Limits, ModelConfig, TaskSpec
from visualux.evaluate import evaluate
from visualux.model import ModelError, solid_png
from visualux.rubric import resolve
from visualux.schemas import Evaluation, Finding
from visualux.trace import TaskTrace


class BatchModel:
    def __init__(self, omit_synthesis=False):
        self.budget = SimpleNamespace(limits=Limits())
        self.calls = []
        self.omit_synthesis = omit_synthesis

    async def ask(self, schema, instruction, payload, images, **kwargs):
        self.calls.append(payload)
        assert "expected_download" not in payload["task"]
        if len(images) > 2:
            raise ModelError("minimum_request_exceeds_context_limit")
        if not images:
            if self.omit_synthesis:
                return Evaluation()
            original = payload["prior_findings"]
            item = dict(original[0])
            item["id"] = "canonical"
            item["merged_from"] = [f["id"] for f in original]
            return Evaluation(findings=[Finding.model_validate(item)])
        oid = payload["observations"][0]["id"]
        finding = Finding(id=payload["finding_id_prefix"] + "small", kind="concern", criterion_ids=["interaction.target_access"], title="Small control", facts="A sourced local control risk", interpretation="Limited activation risk", evidence=[{"observation_id": oid}], attribution="ux", certainty="supported", severity="low", impact="Limited friction", suggestion="Increase the target padding while preserving its label.")
        return Evaluation(findings=[finding], covered_observations=[o["id"] for o in payload["observations"]], covered_criteria=list(payload["rubric"]))


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cfg = Config(url="http://127.0.0.1:8765", model=ModelConfig(name="test/model"), tasks=[TaskSpec(id="task", description="Inspect", expected_download={"extension": ".csv"})])
        self.rubric = resolve(CriteriaConfig())
        self.trace = TaskTrace(self.root, cfg.tasks[0], cfg, self.rubric)
        for _ in range(5):
            self.trace.observation(solid_png("red"), cfg.viewport.model_dump())

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_batch_reduction_and_final_dedup_preserve_evidence(self):
        model = BatchModel()
        await evaluate(model, self.trace, self.rubric, None)
        self.assertEqual(self.trace.data["evaluation"]["status"], "complete")
        self.assertEqual(self.trace.data["penalty"]["total"], 1)
        findings = self.trace.data["evaluation"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertGreater(len(findings[0]["evidence"]), 1)
        self.assertEqual(set(self.trace.data["evaluation"]["covered_observations"]), {f"obs-{i:03}" for i in range(5)})

    async def test_synthesis_cannot_erase_issues_or_double_deduct(self):
        await evaluate(BatchModel(omit_synthesis=True), self.trace, self.rubric, None)
        self.assertEqual(self.trace.data["evaluation"]["status"], "partial")
        self.assertTrue(self.trace.data["evaluation"]["findings"])
        self.assertIsNone(self.trace.data["penalty"]["total"])
        self.assertTrue(all(f["penalty"] is None for f in self.trace.data["evaluation"]["findings"]))
