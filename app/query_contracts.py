"""Bounded API and model contracts for durable question workflows."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.markdown import iso_date


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Question(StrictModel):
    question: str = Field(min_length=1, max_length=3000)
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    domain: Literal["all", "work", "investment", "personal"] = "all"
    start: str | None = Field(default=None, max_length=10)
    end: str | None = Field(default=None, max_length=10)
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"
    generate: bool = True

    @model_validator(mode="after")
    def dates(self):
        if not self.question.strip():
            raise ValueError("Enter a question.")
        if any(value and iso_date(value) != value for value in (self.start, self.end)):
            raise ValueError("Enter dates in YYYY-MM-DD format.")
        if self.start and self.end and self.start > self.end:
            raise ValueError("The start date is later than the end date.")
        return self


RequestID = Annotated[str, Field(min_length=16, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]


class JobInput(Question):
    request_id: RequestID


class ClarificationInput(StrictModel):
    request_id: RequestID
    question_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    choice: Literal["a", "b", "both", "defer", "explain"]
    explanation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def explanation_required(self):
        if self.choice == "explain" and not self.explanation.strip():
            raise ValueError("Enter your confirmation.")
        return self


class Sentence(StrictModel):
    text: str = Field(min_length=1, max_length=5000)
    citations: list[Annotated[str, Field(pattern=r"^S\d+$")]] = Field(min_length=1, max_length=24)


class AnswerOutput(StrictModel):
    sentences: list[Sentence] = Field(min_length=1, max_length=40)


class ConflictSide(StrictModel):
    citation: str = Field(pattern=r"^S\d+$")
    quote: str = Field(min_length=1, max_length=1000)


class ConflictCandidate(StrictModel):
    subject: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=600)
    classification: Literal["incompatible", "needs_context"]
    a: ConflictSide
    b: ConflictSide


class ConflictOutput(StrictModel):
    conflicts: list[ConflictCandidate] = Field(max_length=3)
