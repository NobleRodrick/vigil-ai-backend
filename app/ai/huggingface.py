"""
VIGIL-AI Cameroun — Hugging Face Inference API Client (Tier 2)

Free serverless inference against specialist detection models:
  - AI-generated-text detection (RoBERTa ChatGPT detectors)
  - Fake-news classification
  - Image deepfake detection (ViT classifiers)
  - Audio deepfake / voice-clone detection (wav2vec2 classifiers)

Design constraints:
  * Never raises to the caller — every failure path returns None so the
    engine can fall through to the local forensic heuristics.
  * Model IDs are configured in .env as comma-separated lists; each list is
    an ensemble — every responsive model contributes and probabilities are
    averaged.
  * Handles the free tier's realities: 503 model-loading (x-wait-for-model),
    401/402/429 quota exhaustion, and the router→legacy endpoint migration.
"""
import logging
from statistics import fmean

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Label vocabulary → normalized meaning. HF detection models disagree on
# label spelling ("Fake", "FAKE", "Deepfake", "artificial", "ChatGPT"…);
# we map by token so any classifier in the ensemble slot "just works".
FAKE_LABEL_TOKENS = (
    "fake", "deepfake", "spoof", "synthetic", "artificial", "generated",
    "chatgpt", "gpt", "machine", "ai",
)
REAL_LABEL_TOKENS = (
    "real", "human", "authentic", "true", "genuine", "realism", "natural",
    "bonafide", "bona-fide", "original",
)


class HuggingFaceClient:
    def __init__(self):
        self.api_key = settings.HF_API_KEY
        self.bases = [settings.HF_API_BASE, settings.HF_API_FALLBACK_BASE]
        self.timeout = settings.HF_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ── Low-level request ──────────────────────────────────────
    async def _request(
        self,
        model_id: str,
        *,
        json_payload: dict | None = None,
        binary: bytes | None = None,
        content_type: str | None = None,
    ) -> list | dict | None:
        """POST to the inference endpoint. Returns parsed JSON or None."""
        if not self.enabled:
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            # Ask HF to hold the request while a cold model loads instead of 503ing
            "x-wait-for-model": "true",
        }
        if binary is not None and content_type:
            headers["Content-Type"] = content_type

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for base in self.bases:
                url = f"{base.rstrip('/')}/{model_id}"
                try:
                    if json_payload is not None:
                        resp = await client.post(url, headers=headers, json=json_payload)
                    else:
                        resp = await client.post(url, headers=headers, content=binary)
                except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as e:
                    logger.warning(f"HF API unreachable at {base} for {model_id}: {e}")
                    continue

                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        logger.warning(f"HF API returned non-JSON for {model_id}")
                        return None
                if resp.status_code in (401, 402, 429):
                    logger.warning(
                        f"HF API quota/auth issue for {model_id}: {resp.status_code}"
                    )
                    return None  # no point trying the other base with same key
                if resp.status_code == 503:
                    logger.info(f"HF model {model_id} still loading (503)")
                    continue
                logger.warning(
                    f"HF API {resp.status_code} for {model_id}: {resp.text[:200]}"
                )
                # 404/410 on router → try legacy base
                continue
        return None

    # ── Label normalization ────────────────────────────────────
    @staticmethod
    def _flatten(results: list | dict) -> list[dict]:
        """HF classification output is either [{label,score}] or [[{...}]]."""
        if isinstance(results, dict):
            return []
        if results and isinstance(results[0], list):
            results = results[0]
        return [r for r in results if isinstance(r, dict) and "label" in r and "score" in r]

    @classmethod
    def fake_probability_from_labels(cls, results: list | dict) -> float | None:
        """Extract P(fake/AI-generated) from a classifier's label set."""
        items = cls._flatten(results)
        if not items:
            return None

        fake_score: float | None = None
        real_score: float | None = None
        for item in items:
            label = str(item["label"]).lower().replace("_", " ").replace("-", " ")
            tokens = label.split()
            joined = " ".join(tokens)
            if any(tok in joined for tok in FAKE_LABEL_TOKENS):
                fake_score = max(fake_score or 0.0, float(item["score"]))
            elif any(tok in joined for tok in REAL_LABEL_TOKENS):
                real_score = max(real_score or 0.0, float(item["score"]))

        if fake_score is not None:
            return max(0.0, min(1.0, fake_score))
        if real_score is not None:
            return max(0.0, min(1.0, 1.0 - real_score))
        return None  # unknown labels (e.g. LABEL_0/LABEL_1) — can't map safely

    # ── Ensemble helpers ───────────────────────────────────────
    async def _ensemble(
        self,
        model_ids: list[str],
        *,
        json_payload: dict | None = None,
        binary: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[float, list[str], dict] | None:
        """Query every model in the slot; average the mapped probabilities.
        Returns (probability, model_names_used, per_model_detail) or None."""
        probs: dict[str, float] = {}
        for model_id in model_ids:
            raw = await self._request(
                model_id,
                json_payload=json_payload,
                binary=binary,
                content_type=content_type,
            )
            if raw is None:
                continue
            prob = self.fake_probability_from_labels(raw)
            if prob is not None:
                probs[model_id] = round(prob, 4)

        if not probs:
            return None
        avg = fmean(probs.values())
        return avg, list(probs.keys()), probs

    # ── Public detection methods ───────────────────────────────
    async def detect_ai_text(self, text: str) -> tuple[float, list[str], dict] | None:
        """P(text was machine-generated)."""
        return await self._ensemble(
            settings.HF_TEXT_AI_MODEL_LIST,
            json_payload={"inputs": text[:3500], "options": {"wait_for_model": True}},
        )

    async def detect_fake_news(self, text: str) -> tuple[float, list[str], dict] | None:
        """P(text is fake news / disinformation)."""
        return await self._ensemble(
            settings.HF_FAKE_NEWS_MODEL_LIST,
            json_payload={"inputs": text[:3500], "options": {"wait_for_model": True}},
        )

    async def detect_deepfake_image(
        self, image_bytes: bytes, mime_type: str
    ) -> tuple[float, list[str], dict] | None:
        """P(image is a deepfake / AI-generated)."""
        return await self._ensemble(
            settings.HF_IMAGE_DEEPFAKE_MODEL_LIST,
            binary=image_bytes,
            content_type=mime_type or "image/jpeg",
        )

    async def detect_deepfake_audio(
        self, audio_bytes: bytes, mime_type: str
    ) -> tuple[float, list[str], dict] | None:
        """P(audio is synthetic / voice-cloned)."""
        return await self._ensemble(
            settings.HF_AUDIO_DEEPFAKE_MODEL_LIST,
            binary=audio_bytes,
            content_type=mime_type or "audio/wav",
        )


hf_client = HuggingFaceClient()
