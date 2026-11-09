"""Real Chromium fixture checks; these do not claim real-model quality."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from visualux.browser import AuthenticationRequired, Browser, BrowserBlocked
from visualux.config import Config, CriteriaConfig, ModelConfig, TaskSpec
from visualux.demo import server
from visualux.model import Budget
from visualux.auth import handoff
from visualux.config import origin
from visualux.rubric import resolve
from visualux.schemas import Click, Target, Type
from visualux.trace import TaskTrace


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = server(0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.pw = await async_playwright().start()
        self.engine = await self.pw.chromium.launch()
        self.context = await self.engine.new_context(viewport={"width": 1440, "height": 900})
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.engine.close()
        await self.pw.stop()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.temp.cleanup()

    async def test_six_demo_variants_function(self):
        for variant in ("clear", "confusing"):
            with self.subTest(variant=variant):
                await self.page.goto(f"{self.url}/lab.html?variant={variant}")
                self.assertEqual(await self.page.locator("tbody tr").count(), 8)
                await self.page.locator('[data-device="6"]').click()
                self.assertIn("Cooling fan blocked", await self.page.locator("#detail").inner_text())
                await self.page.goto(f"{self.url}/reports.html?variant={variant}")
                await self.page.locator("#active").click()
                self.assertEqual(await self.page.locator("tbody tr").count(), 3)
                await self.page.locator("#export").click()
                async with self.page.expect_download() as info:
                    await self.page.locator("#csv").click()
                download = await info.value
                self.assertEqual(download.suggested_filename, "lab-report.csv")
                text = (await download.path()).read_text()
                self.assertNotIn("Archived", text)
                await self.page.goto(f"{self.url}/device.html?variant={variant}")
                await self.page.locator("#name").fill("review-device-01")
                await self.page.locator("button[type=submit]").click()
                retained = await self.page.locator("#name").input_value()
                self.assertEqual(retained, "" if variant == "confusing" else "review-device-01")
                for field, value in {"name": "review-device-01", "serial": "TEST-0001", "threshold": "80"}.items():
                    await self.page.locator(f"#{field}").fill(value)
                await self.page.locator("button[type=submit]").click()
                self.assertIn("review-device-01", await self.page.locator("#created").inner_text())
                await self.page.locator("#reset").click()
                self.assertEqual(await self.page.locator("#created").inner_text(), "No devices created yet.")

    async def controlled_browser(self):
        await self.page.close()
        cfg = Config(url=self.url + "/lab.html", model=ModelConfig(name="test/model"), tasks=[TaskSpec(id="lab", description="Inspect")], limits={"settle_ms": 0})
        budget = Budget(cfg.limits)
        browser = Browser(self.context, cfg, budget)
        await browser.start()
        trace = TaskTrace(self.root, cfg.tasks[0], cfg, resolve(CriteriaConfig()))
        browser.trace = trace
        await browser.navigate(cfg.url)
        observation, png = await browser.observe(trace)
        return browser, trace, observation

    async def test_geometry_and_stale_node_not_retargeted(self):
        browser, trace, observation = await self.controlled_browser()
        selected = [c for c in browser.candidates if await c[1].get_attribute("data-device") == "6"][0]
        binding = {"id": "test", "observation_id": observation["id"]}
        measure, point = await browser.measure(selected, binding, trace)
        self.assertGreaterEqual(measure["bounds_css_px"]["width"], 40)
        self.assertIsNotNone(point)
        await selected[1].evaluate("el=>el.replaceWith(el.cloneNode(true))")
        action = Click(kind="click", target=Target(observation_id=observation["id"], description="Warning detail button in the seventh row"), effect="inspect", expected="Detail opens")
        with self.assertRaises(BrowserBlocked):
            await browser.dispatch(action, selected, point)

    async def test_capture_suspended_and_password_typing_rejected(self):
        browser, trace, observation = await self.controlled_browser()
        browser.human = True
        count = len(trace.data["observations"])
        with self.assertRaises(BrowserBlocked):
            await browser.observe(trace)
        self.assertEqual(len(trace.data["observations"]), count)
        browser.human = False
        await browser.page.set_content('<input type="password" id="password" autofocus>')
        await browser.page.locator("#password").focus()
        with self.assertRaises(BrowserBlocked):
            await browser.dispatch(Type(kind="type", effect="inspect", expected="Input", text="must-not-enter"))
        self.assertEqual(await browser.page.locator("#password").input_value(), "")

    async def test_demo_server_does_not_serve_repository(self):
        response = await self.page.request.get(self.url + "/../design.md")
        self.assertEqual(response.status, 404)
        response = await self.page.request.get(self.url + "/")
        self.assertEqual(response.status, 200)

    async def test_known_auth_origin_pauses_before_capture(self):
        browser, trace, observation = await self.controlled_browser()
        login_url = f"http://localhost:{self.server.server_port}/lab.html"
        browser.config.login_origins = [origin(login_url)]
        await browser.page.goto(login_url)
        count = len(trace.data["observations"])
        with self.assertRaises(AuthenticationRequired):
            await browser.observe(trace)
        self.assertEqual(len(trace.data["observations"]), count)

    async def test_handoff_preserves_context_and_does_not_capture(self):
        from unittest.mock import patch
        browser, trace, observation = await self.controlled_browser()
        before = len(trace.data["observations"])
        context = browser.context
        async def resume(prompt, timeout):
            self.assertTrue(browser.human)
            with self.assertRaises(BrowserBlocked):
                await browser.observe(trace)
            await asyncio.sleep(0.02)
            return "resume"
        with patch("visualux.auth.terminal_line", side_effect=resume):
            result = await handoff(browser, trace, browser.budget)
        self.assertEqual(result, "resume")
        self.assertIs(browser.context, context)
        self.assertEqual(len(trace.data["observations"]), before)
        self.assertFalse(browser.human)
        self.assertGreater(browser.budget.paused, 0)

    async def test_small_native_target_and_obstruction_are_measurements(self):
        browser, trace, observation = await self.controlled_browser()
        await browser.page.set_content('<style>button{box-sizing:border-box;padding:0;width:44px;height:44px}#small{width:14px;height:14px}#cover{position:fixed;inset:0;background:#fff9;z-index:10;pointer-events:none}</style><button>+</button><button id="small">·</button><div id="cover"></div>')
        observation, _ = await browser.observe(trace)
        large, small = browser.candidates
        first, point = await browser.measure(large, {"id": "one", "observation_id": observation["id"]}, trace)
        second, _ = await browser.measure(small, {"id": "two", "observation_id": observation["id"]}, trace)
        self.assertEqual(first["bounds_css_px"]["width"], 44)
        self.assertEqual(second["bounds_css_px"]["width"], 14)
        await browser.page.locator("#cover").evaluate("el=>el.style.pointerEvents='auto'")
        blocked, point = await browser.measure(large, {"id": "three", "observation_id": observation["id"]}, trace)
        self.assertIsNone(point)
        self.assertFalse(any(s["unobstructed"] for s in blocked["pointer_samples"]))

    async def test_slow_document_load_is_bounded_and_partial_is_labeled(self):
        from visualux.model import LimitError
        browser, trace, _ = await self.controlled_browser()
        browser.config.limits.navigation_timeout_ms = 100
        async def delayed(route):
            await asyncio.sleep(0.2)
            try:
                await route.fulfill(body="<h1>Delayed document</h1>", content_type="text/html")
            except Exception:
                pass
        await browser.page.route("**/slow", delayed)
        with self.assertRaisesRegex(LimitError, "navigation_load_timeout"):
            await browser.navigate(self.url + "/slow")
        await asyncio.sleep(0.25)
        observation, _ = await browser.observe(trace, partial=True)
        self.assertTrue(observation["partial"])
        self.assertTrue(any(e["kind"] == "navigation_wait" and e["outcome"] == "incomplete" for e in trace.data["events"]))


if __name__ == "__main__":
    unittest.main()
