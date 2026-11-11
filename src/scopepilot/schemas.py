from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
ProjectName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
Message = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)
]
OriginalScope = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)
]


class BriefCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Identifier
    client_id: Identifier
    project_name: ProjectName
    message: Message
    original_scope: OriginalScope | None = None


class BriefResponse(BriefCreate):
    id: UUID
    received_at: datetime
    status: str


class HealthResponse(BaseModel):
    status: str

