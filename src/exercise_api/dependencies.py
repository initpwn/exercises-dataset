"""FastAPI dependencies shared by route modules."""

from fastapi import Request

from exercise_api.exercise_repository import ExerciseRepository


def get_exercise_repository(request: Request) -> ExerciseRepository:
    """Build a repository from the application's database session factory."""
    return ExerciseRepository(request.app.state.database.session_factory)
