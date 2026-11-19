import uuid
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.core.config import settings


async def save_file(file: UploadFile) -> Path:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    file_extension = Path(file.filename or "").suffix.lower()
    file_path = settings.uploads_dir / f"{uuid.uuid4()}{file_extension}"

    try:
        async with aiofiles.open(file_path, "wb") as destination:
            while chunk := await file.read(1024 * 1024):
                await destination.write(chunk)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    return file_path


def delete_file(file_path: Path) -> None:
    file_path.unlink(missing_ok=True)
