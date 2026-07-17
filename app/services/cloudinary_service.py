"""
VIGIL-AI Cameroun — Cloudinary Media Mirror (optional, FREE tier)

When Cloudinary credentials are present in the environment, small uploaded
media files are mirrored to Cloudinary and the resulting secure URL is
stored on the submission. This gives the platform durable, CDN-served media
even on hosts with ephemeral disks (e.g. Render free tier).

Entirely optional: if the `cloudinary` package or the credentials are
missing, every call is a silent no-op and local storage remains the source
of truth for analysis.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import cloudinary
    import cloudinary.uploader
    _CLOUDINARY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _CLOUDINARY_AVAILABLE = False


class CloudinaryService:
    def __init__(self):
        self._configured = False
        if not _CLOUDINARY_AVAILABLE:
            return
        if settings.CLOUDINARY_URL:
            # The SDK only auto-parses CLOUDINARY_URL from the process env,
            # not from a kwarg — parse the cloudinary:// URL ourselves so it
            # also works when only the .env file sets it.
            try:
                from urllib.parse import urlparse
                parsed = urlparse(settings.CLOUDINARY_URL)
                if parsed.scheme != "cloudinary" or not (
                    parsed.username and parsed.password and parsed.hostname
                ):
                    raise ValueError(
                        "expected cloudinary://<api_key>:<api_secret>@<cloud_name>"
                    )
                cloudinary.config(
                    cloud_name=parsed.hostname,
                    api_key=parsed.username,
                    api_secret=parsed.password,
                    secure=True,
                )
                self._configured = True
            except Exception as e:
                logger.warning(f"Invalid CLOUDINARY_URL: {e}")
        elif (
            settings.CLOUDINARY_CLOUD_NAME
            and settings.CLOUDINARY_API_KEY
            and settings.CLOUDINARY_API_SECRET
        ):
            cloudinary.config(
                cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                api_key=settings.CLOUDINARY_API_KEY,
                api_secret=settings.CLOUDINARY_API_SECRET,
                secure=True,
            )
            self._configured = True

        if self._configured:
            logger.info("Cloudinary media mirroring enabled")

    @property
    def enabled(self) -> bool:
        return self._configured

    def upload(self, file_bytes: bytes, content_type: str, case_number: str) -> str | None:
        """Mirror a media file to Cloudinary. Returns the secure URL or None.
        Blocking — call from FastAPI via starlette's run_in_threadpool."""
        if not self._configured:
            return None
        if len(file_bytes) > settings.CLOUDINARY_MAX_MB * 1024 * 1024:
            logger.info(
                f"Skipping Cloudinary mirror for {case_number}: file exceeds "
                f"{settings.CLOUDINARY_MAX_MB}MB cap"
            )
            return None

        # Cloudinary resource types: image | video (video also covers audio) | raw
        resource_type = {
            "image": "image",
            "audio": "video",
            "video": "video",
        }.get(content_type, "auto")

        try:
            result = cloudinary.uploader.upload(
                file_bytes,
                resource_type=resource_type,
                folder=settings.CLOUDINARY_FOLDER,
                public_id=case_number.lower().replace(" ", "-"),
                overwrite=False,
                unique_filename=True,
            )
            url = result.get("secure_url")
            logger.info(f"Mirrored {case_number} media to Cloudinary: {url}")
            return url
        except Exception as e:
            logger.warning(f"Cloudinary upload failed for {case_number}: {e}")
            return None


cloudinary_service = CloudinaryService()
