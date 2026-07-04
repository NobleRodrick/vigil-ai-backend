"""
VIGIL-AI Cameroun — Storage Service
Local filesystem storage with path security and file validation.
Compatible with MinIO API for easy cloud migration.
"""
import logging
import mimetypes
import uuid
from pathlib import Path

import aiofiles

try:
    import magic  # type: ignore
except Exception:  # pragma: no cover - optional dependency on Windows
    magic = None

from app.config import settings
from app.core.exceptions import FileTooLargeError, InvalidFileTypeError

logger = logging.getLogger(__name__)


class StorageService:
    def __init__(self):
        self.upload_dir = Path(settings.UPLOAD_DIR)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _get_subdir(self, content_type: str) -> Path:
        """Return the appropriate subdirectory for a content type."""
        subdir = self.upload_dir / content_type
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def _generate_safe_filename(self, original_filename: str, content_type: str) -> str:
        """Generate a UUID-based filename to prevent path traversal attacks."""
        # Extract extension safely
        ext = Path(original_filename).suffix.lower()
        allowed_extensions = {
            "image": [".jpg", ".jpeg", ".png", ".webp", ".gif"],
            "audio": [".mp3", ".wav", ".ogg", ".m4a"],
            "video": [".mp4", ".avi", ".mov", ".mkv"],
        }
        if ext not in allowed_extensions.get(content_type, []):
            ext = ""
        return f"{uuid.uuid4().hex}{ext}"

    async def save_file(
        self,
        file_bytes: bytes,
        original_filename: str,
        content_type: str,
        expected_mime_types: list[str],
    ) -> tuple[str, str, int]:
        """
        Save uploaded file to local storage.
        Returns: (file_path, safe_filename, file_size_bytes)
        """
        # Validate size
        file_size = len(file_bytes)
        if file_size > settings.MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(settings.MAX_FILE_SIZE_MB)

        # Validate MIME type using magic if available, otherwise fall back to extension-based detection
        if magic is not None:
            detected_mime = magic.from_buffer(file_bytes, mime=True)
        else:
            guessed_type, _ = mimetypes.guess_type(original_filename)
            detected_mime = guessed_type or "application/octet-stream"

        if detected_mime not in expected_mime_types:
            raise InvalidFileTypeError(expected_mime_types)

        # Generate safe filename and path
        safe_filename = self._generate_safe_filename(original_filename, content_type)
        subdir = self._get_subdir(content_type)
        file_path = subdir / safe_filename

        # Write file
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(file_bytes)

        logger.info(f"Saved {content_type} file: {file_path} ({file_size} bytes)")
        return str(file_path), safe_filename, file_size

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file from storage."""
        path = Path(file_path)
        if path.exists() and path.is_file():
            # Ensure the path is within our upload directory (security check)
            try:
                path.resolve().relative_to(self.upload_dir.resolve())
            except ValueError:
                logger.error(f"Attempted to delete file outside upload dir: {file_path}")
                return False
            path.unlink()
            logger.info(f"Deleted file: {file_path}")
            return True
        return False

    def get_file_url(self, file_path: str) -> str:
        """Get a publicly accessible URL for a stored file."""
        relative = Path(file_path).relative_to(self.upload_dir)
        return f"{settings.APP_BASE_URL}/uploads/{relative}"


storage_service = StorageService()
