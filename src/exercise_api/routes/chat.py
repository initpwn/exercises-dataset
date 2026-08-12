"""Grounded conversational exercise endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from exercise_api.api_models import ChatRequest, ChatResponse
from exercise_api.chat_service import ChatService, UngroundedLLMResponseError
from exercise_api.dependencies import get_chat_service
from exercise_api.llm_gateway import LLMInvalidResponseError, LLMUnavailableError
from exercise_api.session_repository import SessionNotFoundError

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    try:
        return await service.chat(request)
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from None
    except (LLMInvalidResponseError, UngroundedLLMResponseError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Model returned an invalid grounded response",
        ) from None
    except LLMUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model service is unavailable",
        ) from None
