from __future__ import annotations

import unicodedata
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from .domain import SDLCStyle


def _reject_controls(value: str, allow_lines: bool = False) -> str:
    # Browsers submit multiline textareas as CRLF on Windows. Normalize those
    # line endings before checking controls so valid pasted requirements are
    # not rejected for containing the carriage-return half of CRLF.
    if allow_lines:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    allowed = {"\n", "\t"} if allow_lines else set()
    if any(unicodedata.category(char) == "Cc" and char not in allowed for char in value):
        raise ValueError("control characters are not allowed")
    return value.strip()


class ProjectCreate(BaseModel):
    name: Annotated[str, Field(min_length=3, max_length=100)]
    requirement: Annotated[str, Field(min_length=10, max_length=20_000)]
    sdlc_style: SDLCStyle
    repository_url: Annotated[str | None, Field(max_length=500)] = None
    workspace_hint: Annotated[str | None, Field(max_length=300)] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _reject_controls(value)

    @field_validator("requirement")
    @classmethod
    def validate_requirement(cls, value: str) -> str:
        return _reject_controls(value, allow_lines=True)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = _reject_controls(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("repository URL must be an HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("repository URL must not contain credentials")
        return value

    @field_validator("workspace_hint")
    @classmethod
    def validate_workspace_hint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = _reject_controls(value)
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("workspace hint must be a safe project-relative path")
        return normalized


class ApprovalInput(BaseModel):
    approved: bool
    comment: Annotated[str | None, Field(max_length=1000)] = None

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        return _reject_controls(value, allow_lines=True) if value else None


class SupportInput(BaseModel):
    issue: Annotated[str, Field(min_length=5, max_length=5000)]

    @field_validator("issue")
    @classmethod
    def validate_issue(cls, value: str) -> str:
        return _reject_controls(value, allow_lines=True)
