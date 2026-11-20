import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    filename: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
