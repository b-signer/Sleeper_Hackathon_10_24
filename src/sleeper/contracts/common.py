"""Shared building blocks for every contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = 1


class Contract(BaseModel):
    """Base for every contract: unknown fields are rejected, so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


# "owner/name", as GitHub writes it.
Repo = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]

# Run-unique tool output ID, assigned by the harness: c_001, c_002, ... Cards cite these.
CallId = Annotated[str, StringConstraints(pattern=r"^c_\d{3,}$")]

# RFC 6901 JSON Pointer into a tool output, e.g. "/metrics/stars". "" is the whole output.
JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/[^/]*)*$")]

# A criterion score: computed by code (D8), 0 = worst in the universe, 100 = best.
Score = Annotated[float, Field(ge=0, le=100)]

Criterion = Literal["popularity", "momentum", "quality", "newness"]
CRITERIA: tuple[Criterion, ...] = ("popularity", "momentum", "quality", "newness")
