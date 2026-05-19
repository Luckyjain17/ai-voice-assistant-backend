"""Temporary model placeholders.

SQLAlchemy models are disabled while the backend runs without PostgreSQL.
"""

from dataclasses import dataclass


@dataclass
class CallRecord:
    id: int
    name: str
    phone: str
    status: str
    duration: str | None = None
    external_id: str | None = None
    questions: str | None = None
    responses: str | None = None
    transcript: str | None = None
    created_at: str | None = None
