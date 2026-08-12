"""Typed API and provider-neutral structured response models."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    """Provider-neutral message sent to a chat-completions model."""

    role: Literal["system", "user", "assistant"]
    content: str


class ExerciseOut(BaseModel):
    """Public representation of one catalog-owned exercise."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str
    body_part: str
    equipment: str
    muscle_group: str
    secondary_muscles: list[str]
    target: str
    instructions: list[str]
    media_id: str
    image: str
    gif_url: str
    attribution: str
    created_at: datetime


class ExercisePage(BaseModel):
    """Paginated exercise catalog response."""

    model_config = ConfigDict(populate_by_name=True)

    data: list[ExerciseOut]
    page: int
    limit: int
    total: int
    total_pages: int = Field(serialization_alias="totalPages")


class RetrievalPlan(BaseModel):
    """Structured catalog constraints and terms extracted from a user request."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["exercise_search", "workout"]
    category: str | None = None
    body_part: str | None = None
    equipment: str | None = None
    muscle_group: str | None = None
    target: str | None = None
    search_terms: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """One conversational request, optionally continuing a durable session."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID | None = None
    message: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class ExerciseSelection(BaseModel):
    """A model-selected catalog ID for an exercise-search response."""

    model_config = ConfigDict(extra="forbid")

    id: str


class WorkoutSelection(BaseModel):
    """LLM-owned prescription attached to one selected catalog exercise."""

    model_config = ConfigDict(extra="forbid")

    id: str
    sets: Annotated[int, Field(ge=1)]
    reps: Annotated[str, Field(min_length=1)]
    rest_seconds: Annotated[int, Field(ge=0)]
    notes: str | None = None


class ExerciseSearchDecision(BaseModel):
    """Validated structured output for grounded catalog search."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["exercise_search"] = "exercise_search"
    answer: str
    selections: list[ExerciseSelection]
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkoutDecision(BaseModel):
    """Validated structured output for grounded workout generation."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["workout"] = "workout"
    answer: str
    name: str
    estimated_duration_minutes: Annotated[int, Field(ge=1)]
    selections: list[WorkoutSelection]
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


GroundedDecision = Annotated[
    ExerciseSearchDecision | WorkoutDecision, Field(discriminator="intent")
]


class WorkoutExercise(ExerciseOut):
    """A catalog-owned exercise plus model-owned workout prescription."""

    sets: Annotated[int, Field(ge=1)]
    reps: Annotated[str, Field(min_length=1)]
    rest_seconds: Annotated[int, Field(ge=0)]
    notes: str | None = None


class WorkoutOut(BaseModel):
    """Client-renderable workout assembled from validated selections."""

    name: str
    estimated_duration_minutes: Annotated[int, Field(ge=1)]
    exercises: list[WorkoutExercise]


class ChatResponse(BaseModel):
    """Grounded conversational response safe for direct client rendering."""

    session_id: UUID
    answer: str
    intent: Literal["exercise_search", "workout"]
    assumptions: list[str] = Field(default_factory=list)
    workout: WorkoutOut | None = None
    results: list[ExerciseOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MessageOut(BaseModel):
    """One ordered user or assistant message in a conversation."""

    model_config = ConfigDict(from_attributes=True)

    position: int
    role: Literal["user", "assistant"]
    text: str
    payload: dict[str, Any] | None
    created_at: datetime


class SessionOut(BaseModel):
    """A durable conversation and its complete ordered message history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    messages: list[MessageOut]
