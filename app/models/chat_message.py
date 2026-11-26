import uuid
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, Enum, ForeignKey, Identity, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class WorkspaceChatMessage(TimestampMixin, Base):
    __tablename__ = "workspace_chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(
            ChatMessageRole,
            name="workspace_chat_message_role",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda roles: [role.value for role in roles],
        )
    )
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
