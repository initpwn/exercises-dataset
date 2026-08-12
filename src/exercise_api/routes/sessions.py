"""Conversation-session HTTP endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from exercise_api.api_models import SessionOut
from exercise_api.dependencies import get_session_repository
from exercise_api.session_repository import SessionRepository

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> SessionOut:
    return await repository.create()


@router.get("/{id}", response_model=SessionOut)
async def get_session(
    id: UUID,
    repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> SessionOut:
    session = await repository.get(id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    id: UUID,
    repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> Response:
    if not await repository.delete(id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
