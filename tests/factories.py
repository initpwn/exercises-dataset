"""Test data builders shared by exercise API tests."""

from datetime import UTC, datetime

from exercise_api.catalog import CatalogRecord


def catalog_record(id: str, name: str, **overrides: object) -> CatalogRecord:
    """Build a deterministic, valid projected catalog record."""
    values: dict[str, object] = {
        "id": id,
        "name": name,
        "category": "chest",
        "body_part": "chest",
        "equipment": "dumbbell",
        "muscle_group": "triceps",
        "secondary_muscles": ["triceps"],
        "target": "pectorals",
        "instructions": ["First step", "Second step"],
        "media_id": f"media-{id}",
        "image": f"images/{id}-media-{id}.jpg",
        "gif_url": f"videos/{id}-media-{id}.gif",
        "attribution": "© Gym visual — https://gymvisual.com/",
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return CatalogRecord.model_validate(values)
