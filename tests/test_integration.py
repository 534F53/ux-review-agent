"""End-to-end contracts against a local scripted OpenAI-compatible server.

This checks transport/orchestration, not real-model visual or UX accuracy.
"""

import asyncio
import base64
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from visualux.config import Config, CriteriaConfig, ModelConfig, TaskSpec
from visualux.demo import server
from visualux.model import Budget, Model, ModelError
from visualux.orchestrator import run
from visualux.rubric import resolve
from visualux.schemas import Preflight


class Endpoint(BaseHTTPRequestHandler):
    requests = []
    invalid = False
    denied = False
    actions = {}
    failures = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, request, self.headers.get("Authorization")))
        status = type(self).failures.pop(0) if self.failures else 401 if self.denied else 200
        if status != 200:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"denied","type":"authentication_error"}}')
            return
        parts = request["messages"][-1]["content"]
        payload = json.loads([p["text"] for p in parts if p["type"] == "text"][-1])
        prompt = request["messages"][0]["content"]
        if "dominant background" in prompt:
            png = base64.b64decode(next(p["image_url"]["url"].split(",", 1)[1] for p in parts if p["type"] == "image_url"))
            index, packed = 8, b""
            while index < len(png):
                size = struct.unpack(">I", png[index:index+4])[0]
                if png[index+4:index+8] == b"IDAT":
                    packed += png[index+8:index+8+size]
                index += size + 12
            pixel = zlib.decompress(packed)[1:4]
            value = {"background": {b"\xff\x00\x00": "red", b"\x00\xff\x00": "green", b"\x00\x00\xff": "blue"}[pixel]}
        elif "candidate_refs" in payload:
            wanted = "c004" if "export control" in payload["target_description"] else "c005"
            value = {"matches": [wanted] if wanted in payload["candidate_refs"] else []}
        elif "current_observation_id" in payload:
            oid, tid = payload["current_observation_id"], payload["task_id"]
            count = type(self).actions.get(tid, 0)
            type(self).actions[tid] = count + 1
            if self.invalid:
                action = {"kind": "execute_js", "code": "document.cookie"}
            elif tid == "export" and count < 2:
                action = {"kind": "click", "effect": "download", "expected": "Show format choices" if count == 0 else "Start the CSV download",
                          "target": {"observation_id": oid, "description": "The export control in the top right" if count == 0 else "The CSV option in the open top-right menu"}}
            elif tid == "blocked":
                action = {"kind": "click", "effect": "delete", "expected": "Delete a record", "target": {"observation_id": oid, "description": "A hypothetical destructive control"}}
            else:
                action = {"kind": "finish", "status": "completed", "reason": "visual_answer_available", "answer": "Four report records are visible." if tid != "export" else "The UI reports that the CSV download started.", "evidence": [{"observation_id": oid}], "scope": "Visible report table", "limitations": ["Scripted contract-test endpoint; not a real-model assessment."]}
            value = {"observation_id": oid, "visible_summary": "The report interface is visible.", "rationale": "Inspect the visible interface", "memory": "Report page observed.", "action": action}
        else:
            value = {"findings": [], "dispositions": [], "covered_observations": [o["id"] for o in payload.get("observations", [])], "covered_criteria": list(payload.get("rubric", {})), "limitations": ["Scripted contract test, not real-model evaluation."]}
        body = json.dumps({"id": "contract-test", "object": "chat.completion", "created": 1, "model": "reported-test-deployment", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Integration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.demo = server(0)
        Endpoint.requests, Endpoint.actions, Endpoint.failures = [], {}, []
        Endpoint.invalid, Endpoint.denied = False, False
        self.endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        for httpd in (self.demo, self.endpoint):
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.model = ModelConfig(name="litellm_proxy/contract/vision", api_base=f"http://127.0.0.1:{self.endpoint.server_port}", api_key_env="VISUALUX_CONTRACT_KEY")
        self.env = patch.dict(os.environ, {"VISUALUX_CONTRACT_KEY": "private-contract-key", "OPENAI_API_KEY": "must-not-be-used"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        for httpd in (self.demo, self.endpoint):
            await asyncio.to_thread(httpd.shutdown)
            httpd.server_close()
        self.temp.cleanup()

    def config(self, tasks, mode="isolated"):
        return Config(url=f"http://127.0.0.1:{self.demo.server_port}/reports.html?variant=clear", output=self.root / "reports", model=self.model, tasks=tasks, session_mode=mode, limits={"settle_ms": 10, "active_seconds": 90})

    def artifacts(self):
        root = next((self.root / "reports").iterdir())
        return root, json.loads((root / "manifest.json").read_text())

    async def test_operation_information_transport_and_reports(self):
        cfg = self.config([TaskSpec(id="export", description="Export the report as CSV", expected_download={"extension": ".csv"}), TaskSpec(id="read", description="How many reports are visible?")])
        code = await run(cfg, resolve(CriteriaConfig()), "Sentinel knowledge reference: not a UI fact.")
        self.assertEqual(code, 0)
        root, manifest = self.artifacts()
        self.assertEqual([t["status"] for t in manifest["tasks"]], ["completed", "completed"])
        export = json.loads((root / "tasks/export/trace.json").read_text())
        read = json.loads((root / "tasks/read/trace.json").read_text())
        self.assertEqual(export["metrics"]["actions"], {"click": 2})
        self.assertEqual(export["verification"]["status"], "confirmed")
        self.assertEqual(read["metrics"]["total_browser_steps"], 0)
        self.assertEqual(len(manifest["preflight"]), 1)
        self.assertTrue((root / "index.html").is_file())
        self.assertTrue((root / "tasks/export/report.html").is_file())
        self.assertTrue(list((root / "tasks/export/downloads").glob("*.csv")))
        calls = []
        for _, request, authorization in Endpoint.requests:
            self.assertEqual(request["model"], "contract/vision")
            self.assertEqual(authorization, "Bearer private-contract-key")
            parts = request["messages"][-1]["content"]
            self.assertLessEqual(sum(p["type"] == "image_url" for p in parts), 4)
            payload = json.loads([p["text"] for p in parts if p["type"] == "text"][-1])
            calls.append(payload)
            if "candidate_refs" in payload:
                self.assertNotIn("task", payload)
                self.assertNotIn("knowledge", payload)
                self.assertNotIn("rubric", payload)
                self.assertNotIn("selector", json.dumps(payload))
            if payload.get("task_id") == "read":
                self.assertEqual(payload["recent_decisions"], [])
                self.assertEqual(payload["shared_prior_visual_context"], [])
            if "observations" in payload:
                self.assertNotIn("expected_download", payload["task"])
        self.assertTrue(any("candidate_refs" in p for p in calls))
        all_saved = "".join(p.read_text() for p in root.rglob("*.json"))
        self.assertNotIn("private-contract-key", all_saved)
        self.assertNotIn("must-not-be-used", all_saved)

    async def test_isolated_continues_after_block_and_shared_stops(self):
        cfg = self.config([TaskSpec(id="blocked", description="Delete a record"), TaskSpec(id="read", description="Read the report")], "shared")
        self.assertEqual(await run(cfg, resolve(CriteriaConfig()), None), 0)
        root, manifest = self.artifacts()
        self.assertEqual(manifest["tasks"][0]["status"], "blocked")
        self.assertEqual(manifest["tasks"][1]["status"], "not_started")
        self.assertEqual(manifest["tasks"][1]["reason"], "upstream_task_incomplete")
        self.assertFalse((root / "tasks/read").exists())
        cfg.session_mode = "isolated"
        Endpoint.actions = {}
        self.assertEqual(await run(cfg, resolve(CriteriaConfig()), None), 0)
        manifests = [json.loads(p.read_text()) for p in cfg.output.glob("*/manifest.json")]
        isolated = next(m for m in manifests if m["config"]["session_mode"] == "isolated")
        self.assertEqual(isolated["tasks"][1]["status"], "completed")

    async def test_malformed_decisions_never_execute(self):
        Endpoint.invalid = True
        cfg = self.config([TaskSpec(id="read", description="Read"), TaskSpec(id="later", description="Read again")])
        self.assertEqual(await run(cfg, resolve(CriteriaConfig()), None), 1)
        root, manifest = self.artifacts()
        trace = json.loads((root / "tasks/read/trace.json").read_text())
        self.assertEqual(trace["steps"], [])
        self.assertEqual(trace["result"]["status"], "uncertain")
        self.assertEqual(manifest["tasks"][1]["status"], "not_started")

    async def test_denied_key_is_not_retried_or_rerouted(self):
        Endpoint.denied = True
        calls = []
        model = Model(self.model, Budget(self.config([TaskSpec(id="a", description="Read")]).limits), calls.append)
        with self.assertRaises(ModelError):
            await model.preflight()
        self.assertEqual(len(Endpoint.requests), 1)
        self.assertEqual(len(calls), 1)

    async def test_rate_limit_has_one_bounded_retry(self):
        Endpoint.failures = [429, 200]
        calls = []
        model = Model(self.model, Budget(self.config([TaskSpec(id="a", description="Read")]).limits), calls.append)
        await model.preflight()
        self.assertEqual(len(Endpoint.requests), 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(request["model"] == "contract/vision" for _, request, _ in Endpoint.requests))

    async def test_cancellation_during_evaluation_preserves_task_and_reports(self):
        cfg = self.config([TaskSpec(id="read", description="Read the report"), TaskSpec(id="later", description="Read again")])
        with patch("visualux.orchestrator.evaluate", side_effect=asyncio.CancelledError()):
            code = await run(cfg, resolve(CriteriaConfig()), None)
        self.assertEqual(code, 130)
        root, manifest = self.artifacts()
        trace = json.loads((root / "tasks/read/trace.json").read_text())
        self.assertEqual(trace["result"]["status"], "completed")
        self.assertEqual(trace["execution_status"], "cancelled")
        self.assertIsNone(trace["penalty"]["total"])
        self.assertTrue((root / "tasks/read/report.html").is_file())
        self.assertEqual(manifest["tasks"][1]["status"], "not_started")


if __name__ == "__main__":
    unittest.main()
