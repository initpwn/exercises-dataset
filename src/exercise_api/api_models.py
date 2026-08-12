"""Public response models for exercise catalog endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
