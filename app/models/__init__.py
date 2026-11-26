# Import every model here so Base.metadata is complete for Alembic.
from app.db.base import Base
from app.models.chat_message import WorkspaceChatMessage
from app.models.document import Document
from app.models.user import User
from app.models.workspace import Workspace

__all__ = ["Base", "Document", "User", "Workspace", "WorkspaceChatMessage"]
