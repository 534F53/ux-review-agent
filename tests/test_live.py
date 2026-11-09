"""Opt-in real-model validation. Expected facts never enter model requests."""

import asyncio
import json
import os
import threading
import unittest
from pathlib import Path

from visualux.config import Config, TaskSpec, load_config, load_knowledge
from visualux.demo import server
from visualux.orchestrator import run
from visualux.rubric import resolve


@unittest.skipUnless(os.environ.get("VISUALUX_LIVE_CONFIG"), "Real-model environment not supplied; set VISUALUX_LIVE_CONFIG to opt in")
class LiveDemoValidation(unittest.IsolatedAsyncioTestCase):
    async def test_real_model_demo_matrix(self):
        cfg = load_config(Path(os.environ["VISUALUX_LIVE_CONFIG"]), {})
        cases = json.loads((Path(__file__).parent / "cases/demos.json").read_text())
        demo = server(0)
        threading.Thread(target=demo.serve_forever, daemon=True).start()
        try:
            for case in cases:
                for variant in ("clear", "confusing"):
                    with self.subTest(app=case["app"], variant=variant):
                        data = cfg.model_dump()
                        data.update(url=f"http://127.0.0.1:{demo.server_port}/{case['app']}.html?variant={variant}", allowed_origins=[], login_origins=[], interactive=False, session_mode="isolated", auth_state=None)
                        data["tasks"] = [TaskSpec(id=f"{case['task_id']}-{variant}", description=case["task"], expected_download=case.get("expected_download")).model_dump()]
                        data["permissions"] = {"allowed_effects": case.get("allowed_effects", ["inspect", "navigate", "filter", "download"])}
                        current = Config.model_validate(data)
                        before = set(current.output.glob("*/manifest.json"))
                        code = await run(current, resolve(current.evaluation), load_knowledge(current.knowledge_file))
                        new = set(current.output.glob("*/manifest.json")) - before
                        self.assertEqual(len(new), 1)
                        manifest = json.loads(next(iter(new)).read_text())
                        trace_path = next(iter(new)).parent / "tasks" / current.tasks[0].id / "trace.json"
                        trace = json.loads(trace_path.read_text())
                        self.assertEqual(code, 0, "Inspect retained artifacts for model/infrastructure/evaluation errors")
                        if variant == "clear":
                            self.assertEqual(trace["result"]["status"], "completed")
                        if trace["result"]["status"] == "completed":
                            answer = (trace["result"]["answer"] or "").lower()
                            for term in case.get("expected_answer_terms", []):
                                self.assertIn(term, answer)
                            if case.get("expected_download"):
                                self.assertEqual(trace["verification"]["status"], "confirmed")
                        self.assertNotEqual(trace["evaluation"]["status"], "unavailable")
                        self.assertTrue(manifest["tasks"][0].get("artifacts"))
        finally:
            await asyncio.to_thread(demo.shutdown)
            demo.server_close()
