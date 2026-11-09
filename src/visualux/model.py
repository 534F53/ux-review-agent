"""One bounded LiteLLM client boundary, shared by planning/matching/evaluation."""

import asyncio
import base64
import json
import logging
import math
import os
import secrets
import struct
import time
import zlib

from pydantic import ValidationError

from .config import safe_url
from .rubric import digest
from .schemas import Preflight
from .trace import utc


class LimitError(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


class Budget:
    def __init__(self, limits):
        self.limits = limits
        self.started = time.monotonic()
        self.paused = 0.0
        self.eval_started = None
        self.eval_setup_seconds = 0
        self.requests = {"interaction": 0, "evaluation": 0}

    def remaining(self, phase="interaction"):
        if phase == "evaluation":
            if self.eval_started is None:
                self.eval_started = time.monotonic()
            return self.limits.evaluation_seconds - self.eval_setup_seconds - (time.monotonic() - self.eval_started)
        return self.limits.active_seconds - (time.monotonic() - self.started - self.paused)

    def request(self, phase):
        group = "evaluation" if phase == "evaluation" else "interaction"
        maximum = self.limits.evaluation_requests if group == "evaluation" else self.limits.interaction_requests
        if self.remaining(group) <= 0:
            raise LimitError(f"{group}_time_limit")
        if self.requests[group] >= maximum:
            raise LimitError(f"{group}_request_limit")
        self.requests[group] += 1
        return min(self.limits.request_timeout_seconds, self.remaining(group))


def solid_png(color: str) -> bytes:
    rgb = {"red": b"\xff\x00\x00", "green": b"\x00\xff\x00", "blue": b"\x00\x00\xff"}[color]
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\0" + rgb * 128) * 128)) + chunk(b"IEND", b"")


def image_tokens(png: bytes) -> int:
    width, height = struct.unpack(">II", png[16:24])
    # shortcut: conservative patch estimate, use model-specific accounting after real-endpoint calibration.
    return math.ceil(width / 16) * math.ceil(height / 16)


class Model:
    def __init__(self, config, budget: Budget, record):
        self.config, self.budget, self.record = config, budget, record

    async def ask(self, schema, instruction: str, payload: dict, images: list, phase="planning", required_images=1):
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import litellm

        litellm.suppress_debug_info = True
        logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        system = instruction + "\nReturn exactly one JSON object matching this schema. No Markdown fences, code, or private reasoning.\n" + schema_text
        images = list(images)
        if len(images) > self.budget.limits.max_images:
            raise ModelError("image_request_limit")
        payload = dict(payload)
        text = json.dumps(payload, ensure_ascii=False)
        context = self.config.context_tokens
        estimate = lambda: len((system + text).encode()) + sum(image_tokens(png) for _, png in images) + self.config.max_output_tokens + 1024
        while context and estimate() > context and len(images) > required_images:
            images.pop()
        for field in ("recent_decisions", "recent_controller_steps"):
            if field in payload:
                payload[field] = list(payload[field])
                while context and estimate() > context and payload[field]:
                    payload[field].pop(0)
                    text = json.dumps(payload, ensure_ascii=False)
        if context and estimate() > context:
            raise ModelError("minimum_request_exceeds_context_limit")
        corrected = transient = reduced = False
        while True:
            timeout = self.budget.request(phase)
            content = []
            for label, png in images:
                content.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}])
            content.append({"type": "text", "text": text})
            messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
            kwargs = dict(model=self.config.name, messages=messages, max_tokens=self.config.max_output_tokens,
                          timeout=timeout, num_retries=0, max_retries=0, caching=False)
            if self.config.api_base:
                kwargs["api_base"] = self.config.api_base
            key = self.config.key()
            if key is not None:
                kwargs["api_key"] = key
            elif self.config.name.startswith("litellm_proxy/"):
                kwargs["api_key"] = "visualux-local-no-auth"
            if self.config.response_mode == "json":
                kwargs["response_format"] = {"type": "json_object"}
            elif self.config.response_mode == "schema":
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
            start = time.monotonic()
            item = {"at": utc(), "phase": phase, "requested_model": self.config.name,
                    "endpoint": safe_url(self.config.api_base) if self.config.api_base else "provider-default",
                    "prompt_hash": digest(system), "image_count": len(images), "returned_model": None,
                    "estimated_total_tokens": estimate() if context else None,
                    "token_estimate_method": "conservative_utf8_bytes_and_16px_patches" if context else "unknown"}
            item["status"] = "interrupted"
            try:
                response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
                item["returned_model"] = getattr(response, "model", None)
                usage = getattr(response, "usage", None)
                item["usage"] = usage.model_dump() if usage and hasattr(usage, "model_dump") else None
                raw = response.choices[0].message.content
                if not isinstance(raw, str) or len(raw) > 150000:
                    raise ValueError("Missing or oversized JSON response")
                result = schema.model_validate_json(raw)
                item["status"] = "ok"
                return result
            except (ValidationError, ValueError, IndexError, AttributeError) as exc:
                item["status"] = "invalid_response"
                if corrected:
                    raise ModelError("invalid_structured_response") from None
                corrected = True
                system += "\nPrevious response was invalid. Follow the exact JSON schema and all field constraints."
            except Exception as exc:
                name = type(exc).__name__
                item["status"] = name
                status = getattr(exc, "status_code", None)
                if "ContextWindow" in name and not reduced and len(images) > required_images:
                    images.pop()
                    reduced = True
                elif not transient and (status in {429, 500, 502, 503, 504} or name in {"TimeoutError", "Timeout", "APIConnectionError"}):
                    transient = True
                    await asyncio.sleep(min(1, max(0, self.budget.remaining("evaluation" if phase == "evaluation" else "interaction"))))
                else:
                    raise ModelError(f"model_request_failed:{name}") from None
            finally:
                item["seconds"] = time.monotonic() - start
                self.record(item)

    async def preflight(self, phase="preflight"):
        color = secrets.choice(["red", "green", "blue"])
        result = await self.ask(Preflight, "Read the supplied synthetic image. Report its dominant background color.", {}, [("Synthetic image", solid_png(color))], phase=phase)
        if result.background != color:
            raise ModelError("visual_preflight_failed")
