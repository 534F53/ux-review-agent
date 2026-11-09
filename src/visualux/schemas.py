"""Validated model decisions. Generated text never becomes executable code."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(Record):
    observation_id: str
    step_id: str | None = None
    measurement_id: str | None = None


class Target(Record):
    observation_id: str
    description: str = Field(min_length=8, max_length=600)


class BrowserAction(Record):
    effect: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,59}$")
    expected: str = Field(min_length=1, max_length=1000)


class Click(BrowserAction):
    kind: Literal["click"]
    target: Target


class Hover(BrowserAction):
    kind: Literal["hover"]
    target: Target


class Scroll(BrowserAction):
    kind: Literal["scroll"]
    delta_x: int = Field(default=0, ge=-1200, le=1200)
    delta_y: int = Field(ge=-1200, le=1200)
    target: Target | None = None


class Type(BrowserAction):
    kind: Literal["type"]
    text: str = Field(min_length=1, max_length=2000)


class Keypress(BrowserAction):
    kind: Literal["keypress"]
    key: Literal["Tab", "Shift+Tab", "Enter", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Backspace", "Delete", "ControlOrMeta+A", "Space", "Home", "End"]


class Wait(BrowserAction):
    kind: Literal["wait"]
    duration_ms: int = Field(ge=100, le=5000)


class Observe(Record):
    kind: Literal["observe"]


class Handoff(Record):
    kind: Literal["request_human"]
    reason: Literal["authentication_required", "authorization_required"]
    explanation: str = Field(max_length=1000)


class Finish(Record):
    kind: Literal["finish"]
    status: Literal["completed", "failed", "blocked", "uncertain"]
    reason: str = Field(min_length=1, max_length=300)
    answer: str | None = Field(default=None, max_length=6000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=40)
    scope: str = Field(default="", max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def completion_has_evidence(self):
        if self.status == "completed" and not self.evidence:
            raise ValueError("Completion requires visual evidence")
        return self


Action = Annotated[Click | Hover | Scroll | Type | Keypress | Wait | Observe | Handoff | Finish, Field(discriminator="kind")]


class Difficulty(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    description: str = Field(min_length=1, max_length=1500)
    evidence: list[Evidence] = Field(min_length=1, max_length=10)
    recovery: str = Field(default="", max_length=1000)


class Decision(Record):
    observation_id: str
    visible_summary: str = Field(max_length=3000)
    preceding_outcome: str = Field(default="", max_length=1500)
    expectation_match: Literal["yes", "no", "uncertain"] = "uncertain"
    rationale: str = Field(min_length=1, max_length=1500)
    memory: str = Field(default="", max_length=6000)
    difficulties: list[Difficulty] = Field(default_factory=list, max_length=12)
    action: Action


class Match(Record):
    matches: list[str] = Field(default_factory=list, max_length=3)


class Preflight(Record):
    background: Literal["red", "green", "blue"]


class Finding(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    kind: Literal["concern", "strength"]
    criterion_ids: list[str] = Field(min_length=1, max_length=8)
    difficulty_ids: list[str] = Field(default_factory=list, max_length=30)
    title: str = Field(min_length=1, max_length=200)
    facts: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    attribution: Literal["ux", "application", "agent", "controller", "unresolved"]
    certainty: Literal["supported", "tentative"]
    severity: Literal["low", "medium", "high"] | None = None
    impact: str = Field(default="", max_length=1500)
    suggestion: str = Field(default="", max_length=2000)
    occurrences: int = Field(default=1, ge=1, le=1000)
    merged_from: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def actionable_supported_concern(self):
        if self.kind == "concern" and self.certainty == "supported" and not self.suggestion:
            raise ValueError("Supported concerns need a specific improvement or investigation suggestion")
        if self.kind == "strength" and self.severity is not None:
            raise ValueError("Strengths have no severity")
        return self


class Disposition(Record):
    difficulty_id: str
    finding_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)


class Evaluation(Record):
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    dispositions: list[Disposition] = Field(default_factory=list, max_length=100)
    covered_observations: list[str] = Field(default_factory=list)
    covered_criteria: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
