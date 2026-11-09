"""TOML and CLI configuration; no credential values are persisted."""

import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator

from .schemas import Record


def origin(value: str) -> str:
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials")
    port = p.port or (443 if p.scheme == "https" else 80)
    host = f"[{p.hostname.lower()}]" if ":" in p.hostname else p.hostname.lower()
    return f"{p.scheme}://{host}:{port}"


def safe_url(value: str) -> str:
    p = urlsplit(value)
    return urlunsplit((p.scheme, p.netloc.rsplit("@", 1)[-1], p.path, "", ""))


class ModelConfig(Record):
    name: str = Field(min_length=1)
    api_base: str | None = None
    api_key_env: str | None = None
    response_mode: str = "prompted"
    context_tokens: int | None = Field(default=None, ge=2048)
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)

    @model_validator(mode="after")
    def valid_connection(self):
        if self.name.startswith("litellm_proxy/") and (not self.api_base or not self.name.removeprefix("litellm_proxy/")):
            raise ValueError("litellm_proxy/ requires an explicit api_base and an existing alias")
        if self.api_base:
            origin(self.api_base)
            if urlsplit(self.api_base).query or urlsplit(self.api_base).fragment:
                raise ValueError("api_base cannot contain query strings or fragments")
        if self.response_mode not in {"prompted", "json", "schema"}:
            raise ValueError("response_mode must be prompted, json, or schema")
        if self.context_tokens and self.max_output_tokens >= self.context_tokens:
            raise ValueError("Output allowance must be smaller than context_tokens")
        return self

    def key(self) -> str | None:
        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if not value:
                raise ValueError(f"Environment variable {self.api_key_env} is unset or empty")
            return value
        return None


class Limits(Record):
    max_steps: int = Field(default=30, ge=1, le=1000)
    active_seconds: float = Field(default=180, gt=0)
    interaction_requests: int = Field(default=90, ge=1)
    evaluation_requests: int = Field(default=16, ge=1)
    request_timeout_seconds: float = Field(default=45, gt=0)
    evaluation_seconds: float = Field(default=180, gt=0)
    navigation_timeout_ms: int = Field(default=30000, ge=100)
    settle_ms: int = Field(default=500, ge=0, le=10000)
    handoff_seconds: float = Field(default=600, gt=0)
    total_handoff_seconds: float = Field(default=1200, gt=0)
    max_images: int = Field(default=4, ge=2, le=32)
    download_bytes: int = Field(default=25 * 1024 * 1024, ge=1)


class Viewport(Record):
    width: int = Field(default=1440, ge=320, le=3840)
    height: int = Field(default=900, ge=240, le=2160)


class CriteriaConfig(Record):
    criteria_file: Path | None = None
    disabled_criteria: list[str] = Field(default_factory=list)


class Permissions(Record):
    allowed_effects: list[str] = Field(default_factory=lambda: ["inspect", "navigate", "filter", "download"])


class DownloadExpectation(Record):
    filename_pattern: str | None = None
    extension: str | None = None

    @field_validator("extension")
    @classmethod
    def extension_only(cls, value):
        if value is not None and not re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", value):
            raise ValueError("Expected a file extension such as .csv")
        return value


class TaskSpec(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=6000)
    expected_download: DownloadExpectation | None = None


class Config(Record):
    url: str
    output: Path = Path("reports")
    session_mode: str = "isolated"
    interactive: bool = False
    auth_state: Path | None = None
    knowledge_file: Path | None = None
    allowed_origins: list[str] = Field(default_factory=list)
    login_origins: list[str] = Field(default_factory=list)
    model: ModelConfig
    evaluator_model: ModelConfig | None = None
    viewport: Viewport = Field(default_factory=Viewport)
    limits: Limits = Field(default_factory=Limits)
    permissions: Permissions = Field(default_factory=Permissions)
    evaluation: CriteriaConfig = Field(default_factory=CriteriaConfig)
    tasks: list[TaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_run(self):
        start = origin(self.url)
        if self.session_mode not in {"isolated", "shared"}:
            raise ValueError("session_mode must be isolated or shared")
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Task IDs must be unique")
        self.allowed_origins = [origin(v) for v in self.allowed_origins] or [start]
        self.login_origins = [origin(v) for v in self.login_origins]
        if start not in self.allowed_origins:
            raise ValueError("Starting URL must belong to allowed_origins")
        if self.auth_state and not self.auth_state.is_file():
            raise ValueError("auth_state must be a readable saved-state file")
        if self.auth_state and self.auth_state.resolve().is_relative_to(self.output.resolve()):
            raise ValueError("Keep authentication state outside report directories")
        return self

    def public(self) -> dict:
        data = self.model_dump(mode="json")
        data["url"] = safe_url(self.url)
        if self.auth_state:
            data["auth_state"] = "[supplied separately]"
        return data


def read_toml(path: Path | None) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path else {}


def load_config(path: Path | None, overrides: dict) -> Config:
    data = read_toml(path)
    base = path.resolve().parent if path else Path.cwd()
    for field in ("output", "auth_state", "knowledge_file"):
        if data.get(field):
            data[field] = (base / data[field]).resolve()
    if data.get("evaluation", {}).get("criteria_file"):
        data["evaluation"]["criteria_file"] = (base / data["evaluation"]["criteria_file"]).resolve()
    tasks = overrides.pop("task", None)
    if tasks:
        if data.get("tasks"):
            raise ValueError("--task and file-defined tasks are mutually exclusive")
        data["tasks"] = [{"id": f"task-{i:03}", "description": text} for i, text in enumerate(tasks, 1)]
    for field, value in overrides.items():
        if value is None:
            continue
        if field in {"model", "evaluator_model"}:
            if field == "evaluator_model" and field not in data:
                data[field] = dict(data.get("model", {}))
            data.setdefault(field, {})["name"] = value
        else:
            data[field] = value
    config = Config.model_validate(data)
    config.model.key()
    (config.evaluator_model or config.model).key()
    return config


def load_knowledge(path: Path | None) -> str | None:
    if path is None:
        return None
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Knowledge file exceeds 16 KiB")
    text = raw.decode("utf-8")
    if not text.strip():
        raise ValueError("Knowledge file is empty")
    return text
