import logging
import uuid

logger = logging.getLogger(__name__)


async def trigger_ingestion(document_id: uuid.UUID) -> None:
    logger.info(
        "AI ingestion requested for document %s",
        document_id,
    )
