"""Direct exercise catalog HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from exercise_api.api_models import ExerciseOut, ExercisePage
from exercise_api.dependencies import get_exercise_repository
from exercise_api.exercise_repository import ExerciseFilters, ExerciseRepository

router = APIRouter()


@router.get("/exercises", response_model=ExercisePage, response_model_by_alias=True)
async def list_exercises(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    category: str | None = None,
    body_part: str | None = None,
    equipment: str | None = None,
    muscle_group: str | None = None,
    target: str | None = None,
) -> ExercisePage:
    filters = ExerciseFilters(
        page=page,
        limit=limit,
        category=category,
        body_part=body_part,
        equipment=equipment,
        muscle_group=muscle_group,
        target=target,
    )
    return await repository.list(filters)


@router.get("/exercises/random", response_model=ExerciseOut)
async def random_exercise(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
) -> ExerciseOut:
    exercise = await repository.random()
    if exercise is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No exercises available"
        )
    return exercise


@router.get("/categories", response_model=list[str])
async def categories(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
) -> list[str]:
    return await repository.distinct("category")


@router.get("/body-parts", response_model=list[str])
async def body_parts(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
) -> list[str]:
    return await repository.distinct("body_part")


@router.get("/equipment", response_model=list[str])
async def equipment(
    repository: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
) -> list[str]:
    return await repository.distinct("equipment")
