"""FastAPI dependencies shared by route modules."""

from fastapi import Request

from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.session_repository import SessionRepository


def get_exercise_repository(request: Request) -> ExerciseRepository:
    """Build a repository from the application's database session factory."""
    return ExerciseRepository(request.app.state.database.session_factory)


def get_session_repository(request: Request) -> SessionRepository:
    """Build a session repository from the application's database factory."""
    return SessionRepository(request.app.state.database.session_factory)
