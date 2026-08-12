"""FastAPI dependencies shared by route modules."""

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from exercise_api.chat_service import ChatService, StructuredLLM
from exercise_api.exercise_repository import ExerciseRepository
from exercise_api.retrieval import RetrievalService
from exercise_api.session_repository import SessionRepository


def require_ready(request: Request) -> None:
    """Reject persistence-backed requests until startup initialization succeeds."""
    readiness = request.app.state.readiness
    if readiness["database"] != "ready" or readiness["catalog"] != "ready":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application is not ready",
        )


def get_exercise_repository(request: Request) -> ExerciseRepository:
    """Build a repository from the application's database session factory."""
    return ExerciseRepository(request.app.state.database.session_factory)


def get_session_repository(request: Request) -> SessionRepository:
    """Build a session repository from the application's database factory."""
    return SessionRepository(request.app.state.database.session_factory)


def get_chat_service(
    request: Request,
    exercises: Annotated[ExerciseRepository, Depends(get_exercise_repository)],
    sessions: Annotated[SessionRepository, Depends(get_session_repository)],
) -> ChatService:
    """Build the conversational orchestrator from application dependencies."""
    return ChatService(
        sessions=sessions,
        retrieval=RetrievalService(exercises),
        exercises=exercises,
        llm=cast(StructuredLLM, request.app.state.llm_gateway),
        settings=request.app.state.settings,
    )
