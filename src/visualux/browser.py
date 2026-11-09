"""Playwright pixels and bounded input; DOM semantics stay inside this module."""

import asyncio
import math
import shutil
import time
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout

from .config import origin
from .model import LimitError, ModelError
from .schemas import Match
from .trace import utc


class BrowserBlocked(RuntimeError):
    pass


class AuthenticationRequired(BrowserBlocked):
    pass


CONTROL_SELECTOR = 'button,a[href],input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[tabindex="0"]'
GEOMETRY = """el => {
 const r=el.getBoundingClientRect(),s=getComputedStyle(el);
 if(!el.isConnected||!r.width||!r.height||s.visibility==='hidden'||s.display==='none'||Number(s.opacity)===0)return null;
 return {x:r.x,y:r.y,width:r.width,height:r.height};
}"""


def moved(before, after):
    return not after or any(abs(before[k] - after[k]) > 2 for k in ("x", "y", "width", "height"))


class Browser:
    def __init__(self, context, config, budget):
        self.context, self.config, self.budget = context, config, budget
        self.page = None
        self.human = False
        self.auth_setup_done = False
        self.pending = False
        self.loaded = asyncio.Event()
        self.loaded.set()
        self.problem = None
        self.generation = 0
        self.candidates = []
        self.observation = None
        self.download_jobs = set()
        self.trace = None

    async def start(self):
        self.page = await self.context.new_page()
        self.page.on("request", self._request)
        self.page.on("load", self._loaded)
        self.page.on("framenavigated", self._navigated)
        self.page.on("download", self._download)
        self.page.on("dialog", self._dialog)
        self.page.on("filechooser", lambda _: setattr(self, "problem", "unsupported_file_upload"))
        self.context.on("page", self._popup)
        await self.context.route("**/*", self._route)
        self.page.set_default_timeout(self.config.limits.navigation_timeout_ms)

    def invalidate(self):
        self.observation = None
        self.generation += 1

    def _request(self, request):
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            self.pending = True
            self.loaded.clear()

    def _loaded(self):
        self.pending = False
        self.loaded.set()

    def _navigated(self, frame):
        if frame == self.page.main_frame:
            self.generation += 1
            if self.trace and not self.human and self.pending:
                step_id = self.trace.data["steps"][-1]["id"] if self.trace.data["steps"] else None
                self.trace.event("document_navigation", step_id=step_id)

    def _popup(self, page):
        if page != self.page and not self.human:
            self.problem = "unsupported_application_popup"

    async def _dialog(self, dialog):
        if not self.human:
            self.problem = "unsupported_browser_dialog"
            await dialog.dismiss()

    async def _route(self, route):
        request = route.request
        if request.is_navigation_request() and not self.human:
            try:
                destination = origin(request.url)
            except ValueError:
                destination = None
            if destination not in self.config.allowed_origins + self.config.login_origins:
                self.problem = "navigation_origin_blocked"
                await route.abort()
                return
        await route.continue_()

    def guard(self):
        if self.human:
            raise BrowserBlocked("capture_suspended_for_human")
        if self.problem:
            raise BrowserBlocked(self.problem)
        current = origin(self.page.url)
        if current in self.config.login_origins:
            raise AuthenticationRequired("authentication_required")
        if current not in self.config.allowed_origins:
            raise BrowserBlocked("navigation_origin_blocked")

    async def navigate(self, url):
        timeout = min(self.config.limits.navigation_timeout_ms, self.budget.remaining() * 1000)
        if timeout <= 0:
            raise LimitError("interaction_time_limit")
        start = time.monotonic()
        outcome = "incomplete"
        try:
            await self.page.goto(url, wait_until="load", timeout=timeout)
            outcome = "loaded"
        except PlaywrightTimeout:
            raise LimitError("navigation_load_timeout") from None
        finally:
            if self.trace:
                self.trace.event("navigation_wait", seconds=time.monotonic() - start, outcome=outcome)
        await self.settle()

    async def settle(self):
        start = time.monotonic()
        remaining = self.budget.remaining()
        if remaining <= 0:
            raise LimitError("interaction_time_limit")
        await asyncio.sleep(min(self.config.limits.settle_ms / 1000, remaining))
        if self.pending:
            try:
                await asyncio.wait_for(self.loaded.wait(), min(self.config.limits.navigation_timeout_ms / 1000, max(0.001, self.budget.remaining())))
            except TimeoutError:
                raise LimitError("navigation_load_timeout") from None
            await asyncio.sleep(min(self.config.limits.settle_ms / 1000, max(0, self.budget.remaining())))
        if self.trace:
            self.trace.event("render_wait", seconds=time.monotonic() - start)

    async def observe(self, trace, partial=False):
        self.guard()
        if self.pending and not partial:
            await self.settle()
        self.guard()
        png = await self.page.screenshot(scale="css", timeout=max(1, min(10000, self.budget.remaining() * 1000)))
        self.guard()
        observation = trace.observation(png, self.config.viewport.model_dump(), partial=partial)
        for _, handle, _ in self.candidates:
            await handle.dispose()
        self.candidates = []
        for handle in await self.page.query_selector_all(CONTROL_SELECTOR):
            rect = await handle.evaluate(GEOMETRY)
            if rect and rect["x"] < self.config.viewport.width and rect["y"] < self.config.viewport.height and rect["x"] + rect["width"] > 0 and rect["y"] + rect["height"] > 0:
                self.candidates.append((f"c{len(self.candidates):03}", handle, rect))
            else:
                await handle.dispose()
        self.observation = (observation["id"], self.generation)
        return observation, png

    async def bind(self, target, png, model, trace):
        self.guard()
        if self.observation != (target.observation_id, self.generation):
            raise BrowserBlocked("stale_target")
        binding = {"id": f"binding-{len(trace.data['bindings']):03}", "observation_id": target.observation_id,
                   "description": target.description, "method": "anonymous_visual_match", "captures": [],
                   "candidate_total": len(self.candidates), "candidate_coverage": "incomplete", "status": "pending", "at": utc()}
        trace.data["bindings"].append(binding)
        trace.save()
        # shortcut: at most 80 visible candidates, revisit discovery if real apps exhaust matching budgets.
        if not self.candidates or len(self.candidates) > 80:
            binding["status"] = "candidate_coverage_limit"
            trace.save()
            raise BrowserBlocked("target_binding_unresolved")
        matches = []
        width = self.config.viewport.width
        height = self.config.viewport.height
        chunk_size = self.config.limits.max_images - 1
        index = 0
        while index < len(self.candidates):
            chunk = self.candidates[index:index + chunk_size]
            images = [(f"Original observation {target.observation_id}", png)]
            for cid, handle, rect in chunk:
                self.guard()
                if moved(rect, await handle.evaluate(GEOMETRY)) or self.observation[1] != self.generation:
                    binding["status"] = "stale"
                    trace.save()
                    raise BrowserBlocked("stale_target")
                x, y = max(0, rect["x"] - 6), max(0, rect["y"] - 6)
                clip = {"x": x, "y": y, "width": min(width - x, rect["width"] + 12), "height": min(height - y, rect["height"] + 12)}
                crop = await self.page.screenshot(clip=clip, scale="css", timeout=max(1, min(10000, self.budget.remaining() * 1000)))
                images.append((f"Candidate {cid}", crop))
                binding["captures"].append({"candidate": cid, "at": utc(), "bounds": clip})
            while True:
                try:
                    answer = await model.ask(Match, "You are a private visual target matcher, not a task planner. Select ALL candidates in this batch that uniquely depict the committed target in the original screenshot. Return their opaque references, or an empty matches list. Use only the images and visual description; never infer a different intended control. Multiple matches mean ambiguity.",
                                             {"target_description": target.description, "candidate_refs": [c[0] for c in chunk]}, images, phase="binding", required_images=len(images))
                    break
                except ModelError as exc:
                    if str(exc) != "minimum_request_exceeds_context_limit" or len(chunk) <= 1:
                        raise
                    chunk.pop()
                    images.pop()
            if not set(answer.matches) <= {c[0] for c in chunk}:
                raise BrowserBlocked("invalid_match_reference")
            matches.extend(answer.matches)
            index += len(chunk)
        binding["candidate_coverage"] = "complete"
        binding["status"] = "bound" if len(matches) == 1 else "unresolved"
        binding["selected"] = matches[0] if len(matches) == 1 else None
        trace.save()
        if len(matches) != 1:
            raise BrowserBlocked("target_binding_unresolved")
        selected = next(c for c in self.candidates if c[0] == matches[0])
        await self.recheck(selected)
        return selected, binding

    async def recheck(self, selected):
        self.guard()
        if not self.observation or self.observation[1] != self.generation or moved(selected[2], await selected[1].evaluate(GEOMETRY)):
            raise BrowserBlocked("stale_target")

    async def measure(self, selected, binding, trace):
        await self.recheck(selected)
        _, handle, rect = selected
        width, height = self.config.viewport.width, self.config.viewport.height
        clipped = {"x": max(0, rect["x"]), "y": max(0, rect["y"]),
                   "right": min(width, rect["x"] + rect["width"]), "bottom": min(height, rect["y"] + rect["height"])}
        points = [(clipped["x"] + (clipped["right"] - clipped["x"]) * x, clipped["y"] + (clipped["bottom"] - clipped["y"]) * y) for x, y in [(0.5, 0.5), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)]]
        samples = []
        for x, y in points:
            hit = await handle.evaluate("(el,p)=>{const top=document.elementFromPoint(p.x,p.y);return !!top&&(el===top||el.contains(top));}", {"x": x, "y": y})
            samples.append({"x": x, "y": y, "unobstructed": hit})
        distances = []
        for cid, _, other in self.candidates:
            if cid != selected[0]:
                dx = max(other["x"] - (rect["x"] + rect["width"]), rect["x"] - (other["x"] + other["width"]), 0)
                dy = max(other["y"] - (rect["y"] + rect["height"]), rect["y"] - (other["y"] + other["height"]), 0)
                distances.append(math.hypot(dx, dy))
        item = {"id": f"measure-{len(trace.data['measurements']):03}", "binding_id": binding["id"], "observation_id": binding["observation_id"],
                "at": utc(), "method": "DOM border box and five pointer-hit samples", "bounds_css_px": rect,
                "viewport_clipped": rect["x"] < 0 or rect["y"] < 0 or rect["x"] + rect["width"] > width or rect["y"] + rect["height"] > height,
                "pointer_samples": samples, "nearest_candidate_gap_css_px": min(distances) if distances else None,
                "neighbor_coverage": f"{len(self.candidates)-1} rendered supported controls in viewport; nested/overlapping controls may have zero gap",
                "effective_region": "unverified", "limitations": ["Border boxes and five samples do not prove the effective activation region or human error rates."]}
        trace.data["measurements"].append(item)
        trace.save()
        return item, next((p for p in samples if p["unobstructed"]), None)

    async def dispatch(self, action, selected=None, point=None, on_dispatch=None):
        self.guard()
        if selected:
            await self.recheck(selected)
            if not point or not await selected[1].evaluate("(el,p)=>{const hit=document.elementFromPoint(p.x,p.y);return !!hit&&(el===hit||el.contains(hit));}", point):
                raise BrowserBlocked("target_obstructed")
        if action.kind == "click":
            forbidden = await selected[1].evaluate("el=>el.tagName==='INPUT'&&['file','password'].includes(el.type)")
            if forbidden:
                raise BrowserBlocked("unsupported_credential_or_file_input")
        elif action.kind == "type":
            editable = await self.page.evaluate("()=>{const el=document.activeElement;return el&&((el.tagName==='INPUT'&&!['password','file','hidden','submit','button','checkbox','radio'].includes(el.type))||el.tagName==='TEXTAREA'||el.isContentEditable)&&!el.disabled&&!el.readOnly;}")
            if not editable:
                raise BrowserBlocked("typing_requires_safe_focused_control")
        elif action.kind == "keypress":
            credential = await self.page.evaluate("()=>document.activeElement?.type==='password'")
            if credential:
                raise BrowserBlocked("credential_entry_requires_human")
        if on_dispatch:
            on_dispatch()
        if action.kind == "click":
            await self.page.mouse.click(point["x"], point["y"])
        elif action.kind == "hover":
            await self.page.mouse.move(point["x"], point["y"])
        elif action.kind == "scroll":
            if point:
                await self.page.mouse.move(point["x"], point["y"])
            else:
                await self.page.mouse.move(self.config.viewport.width / 2, self.config.viewport.height / 2)
            await self.page.mouse.wheel(action.delta_x, action.delta_y)
        elif action.kind == "type":
            await self.page.keyboard.insert_text(action.text)
        elif action.kind == "keypress":
            await self.page.keyboard.press(action.key)
        elif action.kind == "wait":
            await asyncio.sleep(min(action.duration_ms / 1000, max(0, self.budget.remaining())))

    def _download(self, download):
        self.pending = False
        self.loaded.set()
        if self.human or self.trace is None:
            return
        job = asyncio.create_task(self._save_download(download, self.trace))
        self.download_jobs.add(job)

    async def _save_download(self, download, trace):
        name = Path(download.suggested_filename).name[:200]
        item = {"id": f"download-{len(trace.data['downloads']):03}", "suggested_filename": name, "status": "pending", "at": utc()}
        trace.data["downloads"].append(item)
        trace.save()
        try:
            source = await download.path()
            used = sum(d.get("bytes", 0) for d in trace.data["downloads"])
            if not source or source.stat().st_size + used > self.config.limits.download_bytes:
                item["status"] = "retention_limit_exceeded"
                await download.delete()
            else:
                folder = trace.root / "downloads"
                folder.mkdir(exist_ok=True, mode=0o700)
                suffix = Path(name).suffix
                suffix = suffix if suffix[1:].isalnum() and len(suffix) < 14 else ".bin"
                dest = folder / f"{item['id']}{suffix}"
                shutil.copyfile(source, dest)
                item.update(status="completed", bytes=dest.stat().st_size, path=f"downloads/{dest.name}")
                await download.delete()
        except asyncio.CancelledError:
            item["status"] = "download_incomplete"
            raise
        except Exception:
            item["status"] = "download_failed"
        finally:
            trace.save()

    async def finish_downloads(self):
        if self.download_jobs:
            try:
                await asyncio.wait_for(asyncio.gather(*self.download_jobs), max(0.1, min(10, self.budget.remaining())))
            except TimeoutError:
                pass
            finally:
                self.download_jobs.clear()
