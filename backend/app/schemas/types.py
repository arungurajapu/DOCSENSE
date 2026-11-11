"""Shared Pydantic v2 schema field types."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def ensure_utc_datetime(value: datetime) -> datetime:
    """Normalize naive or offset-aware datetimes to explicit UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UTCDatetime = Annotated[datetime, AfterValidator(ensure_utc_datetime)]
