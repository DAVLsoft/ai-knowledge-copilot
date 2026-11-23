from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
