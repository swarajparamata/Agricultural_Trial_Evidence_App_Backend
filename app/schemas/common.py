from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import PlainSerializer

Role = Literal["admin", "viewer"]
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Timestamps are stored as naive UTC; this serializes them with an explicit "Z".
UtcDateTime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str)]
