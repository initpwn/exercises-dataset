"""Provider-neutral prompt construction for structured model responses."""

from exercise_api.api_models import ChatMessage


def json_repair_message(malformed_output: str, validation_error: str) -> ChatMessage:
    """Ask a model to repair one invalid structured response."""
    return ChatMessage(
        role="user",
        content=(
            "Return only valid JSON matching the requested structure. "
            f"The previous response was invalid ({validation_error}). "
            "Repair this output:\n"
            f"{malformed_output}"
        ),
    )
