# Import every model here so Base.metadata is complete for Alembic.
from app.db.base import Base
from app.models.user import User
from app.models.workspace import Workspace
from app.models.document import Document

__all__ = ["Base", "User", "Workspace", "Document"]
