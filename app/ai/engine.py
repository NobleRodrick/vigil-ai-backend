"""
VIGIL-AI Cameroun — AI Detection Engine

Three-tier detection cascade — the platform NEVER surfaces a raw error to
the analyst; every tier degrades gracefully into the next:

  Tier 1 — Google Gemini (free tier): multimodal reasoning over text,
           images and audio, prompted for structured JSON with bilingual
           explanations. Text analysis also scores disinformation.
  Tier 2 — Hugging Face Inference API (free tier): specialist detector
           ensembles — RoBERTa AI-text detectors, fake-news classifiers,
           ViT deepfake-image detectors, wav2vec2 audio-spoof detectors.
  Tier 3 — Local forensic heuristics (app/ai/heuristics.py): stylometry,
           linguistic credibility signals, ELA / FFT / noise forensics,
           audio signal statistics. Always available, zero network.

Video is analyzed by sampling frames (OpenCV) and running each frame
through the image cascade. URL submissions are fetched by
app/services/media_fetch.py (direct download or yt-dlp).
"""
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.ai import heuristics
from app.ai.heuristics import blend_text_scores
from app.ai.huggingface import hf_client
from app.config import settings

logger = logging.getLogger(__name__)


# ── Result Dataclass ──────────────────────────────────────────
@dataclass
class DetectionResult:
    risk_score: int            # 0–100
    confidence: float          # 0.0–1.0
    classification: str        # safe | suspicious | malicious
    explanation_fr: str
    explanation_en: str
    engine_used: str
    raw_response: dict
    processing_time_ms: int


# ── Classification from Score ─────────────────────────────────
def score_to_classification(score: int) -> str:
    if score <= settings.RISK_SCORE_SAFE_MAX:
        return "safe"
    if score <= settings.RISK_SCORE_SUSPICIOUS_MAX:
        return "suspicious"
    return "malicious"


def ai_probability_to_risk_score(ai_probability: float) -> int:
    """Convert AI-generation probability (0.0–1.0) to risk score (0–100)."""
    return min(100, max(0, round(ai_probability * 100)))


def _short_model_names(model_ids: list[str]) -> str:
    return "+".join(m.split("/")[-1][:28] for m in model_ids)


# ── GEMINI API CLIENT (Tier 1) ────────────────────────────────
class GeminiClient:
    """
    Thin wrapper around the Gemini `generateContent` REST endpoint.
    Uses `responseMimeType: application/json` + a response schema so
    Gemini always returns clean, parseable JSON — no markdown fences,
    no prose wrapper.
    """

    BASE_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "ai_generated_probability": {"type": "NUMBER"},
            "confidence": {"type": "NUMBER"},
            "key_indicators": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "explanation_fr": {"type": "STRING"},
            "explanation_en": {"type": "STRING"},
        },
        "required": [
            "ai_generated_probability",
            "confidence",
            "key_indicators",
            "explanation_fr",
            "explanation_en",
        ],
    }

    # Text analysis additionally scores disinformation
    TEXT_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            **BASE_SCHEMA["properties"],
            "disinformation_probability": {"type": "NUMBER"},
        },
        "required": BASE_SCHEMA["required"] + ["disinformation_probability"],
    }

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.base_url = settings.GEMINI_API_BASE
        self.timeout = 45.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _endpoint(self) -> str:
        return f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"

    async def _generate(self, parts: list[dict], schema: dict | None = None) -> dict | None:
        """Send a multimodal generateContent request and return parsed JSON, or None on failure."""
        if not self.enabled:
            return None

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": schema or self.BASE_SCHEMA,
                "maxOutputTokens": 1024,
            },
            "safetySettings": [
                # Detection work necessarily handles sensitive/harmful example content —
                # relax blocking so the model can actually analyze it instead of refusing.
                {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
                for c in [
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ]
            ],
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self._endpoint(), json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning(f"Gemini API unreachable: {e}")
                return None

            if response.status_code == 429:
                logger.warning("Gemini API rate-limited (free tier quota exceeded)")
                return None
            if response.status_code != 200:
                logger.warning(f"Gemini API returned {response.status_code}: {response.text[:300]}")
                return None

            try:
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to parse Gemini response: {e}")
                return None

    async def analyze_text(self, text: str, language: str | None) -> dict | None:
        prompt = TEXT_ANALYSIS_PROMPT.format(
            language=language or "auto-detect (French, English, or Cameroonian Pidgin)",
            content=text[:8000],  # Gemini context is large, but cap for cost/speed
        )
        return await self._generate([{"text": prompt}], schema=self.TEXT_SCHEMA)

    async def analyze_image(self, image_bytes: bytes, mime_type: str) -> dict | None:
        import base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return await self._generate([
            {"text": IMAGE_ANALYSIS_PROMPT},
            {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": b64}},
        ])

    async def analyze_audio(self, audio_bytes: bytes, mime_type: str) -> dict | None:
        import base64
        if len(audio_bytes) > settings.GEMINI_INLINE_FILE_LIMIT:
            logger.info("Audio file exceeds Gemini inline limit — skipping Gemini tier")
            return None
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return await self._generate([
            {"text": AUDIO_ANALYSIS_PROMPT},
            {"inline_data": {"mime_type": mime_type or "audio/mpeg", "data": b64}},
        ])


gemini_client = GeminiClient()


# ── Prompts ─────────────────────────────────────────────────────
TEXT_ANALYSIS_PROMPT = """You are a content-authenticity analyst for VIGIL-AI Cameroun, a national \
cybersecurity platform that detects AI-generated disinformation circulating in Cameroonian \
cyberspace (French, English, and Cameroonian Pidgin English content).

Analyze the following text on TWO independent axes:

1. AI GENERATION — the probability the text was generated or substantially rewritten by an AI \
language model (e.g. ChatGPT, Gemini, Claude) rather than written naturally by a human. Consider: \
uniform sentence structure, generic formal phrasing, absence of personal voice or local context, \
and repetitive transitional phrases.

2. DISINFORMATION — the probability the text is fake news / disinformation regardless of who \
wrote it. Consider: sensationalism, urgency and virality bait ("share before deleted"), missing \
attribution for strong claims, conspiratorial framing, and claims that read as plausible-sounding \
but unverifiable propaganda.

Language hint: {language}

TEXT TO ANALYZE:
\"\"\"
{content}
\"\"\"

Respond with ai_generated_probability (0.0 = certainly human-written, 1.0 = certainly \
AI-generated), disinformation_probability (0.0 = credible information, 1.0 = certainly fake \
news), your confidence, 2-5 short key indicators, and a one-paragraph explanation covering BOTH \
axes in French AND English, suitable for a non-technical government analyst reading a case file."""

IMAGE_ANALYSIS_PROMPT = """You are a forensic image analyst for VIGIL-AI Cameroun, a national \
cybersecurity platform that detects deepfakes and AI-generated images circulating in Cameroonian \
cyberspace.

Examine the attached image for signs of AI generation or deepfake manipulation: unnatural skin \
texture, inconsistent lighting/shadows, asymmetric or malformed facial features, distorted \
background elements, unnatural hands/fingers, repeating textures, or telltale diffusion-model \
artifacts. Also weigh evidence of authenticity (natural imperfections, consistent noise/grain, \
plausible real-world context).

Respond with the probability the image is AI-generated or a deepfake (0.0 = certainly authentic, \
1.0 = certainly AI-generated/deepfake), your confidence, 2-5 short key visual indicators, and a \
one-paragraph explanation in BOTH French and English suitable for a non-technical government \
analyst reading a case file."""

AUDIO_ANALYSIS_PROMPT = """You are a forensic audio analyst for VIGIL-AI Cameroun, a national \
cybersecurity platform that detects voice cloning and synthetic speech used in phone fraud and \
disinformation in Cameroon.

Listen to the attached audio and assess whether the voice is a synthetic / AI-cloned voice rather \
than a genuine human recording: listen for unnatural prosody, robotic or overly smooth intonation, \
inconsistent breathing/pauses, digital artifacts, or an unnaturally uniform pace and tone.

Respond with the probability the voice is synthetic or AI-cloned (0.0 = certainly a genuine human \
voice, 1.0 = certainly synthetic/cloned), your confidence, 2-5 short key audio indicators, and a \
one-paragraph explanation in BOTH French and English suitable for a non-technical government \
analyst reading a case file."""


# ── Bilingual explanation templates (Tiers 2 & 3) ─────────────
_CONTENT_FR = {"text": "ce texte", "image": "cette image", "audio": "cet enregistrement audio", "video": "cette vidéo"}
_CONTENT_EN = {"text": "this text", "image": "this image", "audio": "this audio recording", "video": "this video"}


def _verdict_phrase_fr(score: int) -> str:
    if score <= settings.RISK_SCORE_SAFE_MAX:
        return "Aucun indicateur significatif de contenu généré par IA n'a été détecté."
    if score <= settings.RISK_SCORE_SUSPICIOUS_MAX:
        return "Des indicateurs de possible génération par IA ont été détectés — une vérification manuelle est recommandée."
    return "De fortes indications de contenu généré ou manipulé par IA ont été détectées — une action immédiate est recommandée."


def _verdict_phrase_en(score: int) -> str:
    if score <= settings.RISK_SCORE_SAFE_MAX:
        return "No significant indicators of AI-generated content were detected."
    if score <= settings.RISK_SCORE_SUSPICIOUS_MAX:
        return "Indicators of possible AI generation were detected — manual review is recommended."
    return "Strong indications of AI-generated or AI-manipulated content were detected — immediate action is recommended."


def _hf_explanations(content_type: str, score: int, models: list[str],
                     fake_news_pct: int | None = None) -> tuple[str, str]:
    model_str = ", ".join(m.split("/")[-1] for m in models)
    fr = (
        f"Analyse de {_CONTENT_FR[content_type]} par des modèles de détection spécialisés "
        f"({model_str}) via l'API Hugging Face. Score de risque: {score}/100. "
    )
    en = (
        f"Analysis of {_CONTENT_EN[content_type]} by specialist detection models "
        f"({model_str}) via the Hugging Face API. Risk score: {score}/100. "
    )
    if fake_news_pct is not None:
        fr += f"Probabilité de désinformation estimée: {fake_news_pct}%. "
        en += f"Estimated disinformation probability: {fake_news_pct}%. "
    return fr + _verdict_phrase_fr(score), en + _verdict_phrase_en(score)


def _heuristic_explanations(content_type: str, score: int, indicators: list[str],
                            fake_news_pct: int | None = None) -> tuple[str, str]:
    ind = ", ".join(indicators[:5]) if indicators else "—"
    fr = (
        f"Analyse forensique locale de {_CONTENT_FR[content_type]} (algorithmes stylométriques "
        f"et forensiques intégrés). Score de risque: {score}/100. Indicateurs: {ind}. "
    )
    en = (
        f"Local forensic analysis of {_CONTENT_EN[content_type]} (built-in stylometric and "
        f"forensic algorithms). Risk score: {score}/100. Indicators: {ind}. "
    )
    if fake_news_pct is not None:
        fr += f"Probabilité de désinformation estimée: {fake_news_pct}%. "
        en += f"Estimated disinformation probability: {fake_news_pct}%. "
    return fr + _verdict_phrase_fr(score), en + _verdict_phrase_en(score)


def _build_result(
    *,
    ai_prob: float,
    confidence: float,
    explanation_fr: str,
    explanation_en: str,
    engine: str,
    raw: dict,
    start_time: float,
    key_indicators: list[str] | None = None,
    sub_scores: dict | None = None,
) -> DetectionResult:
    risk_score = ai_probability_to_risk_score(ai_prob)
    raw.setdefault("key_indicators", key_indicators or [])
    if sub_scores:
        raw["sub_scores"] = sub_scores
    return DetectionResult(
        risk_score=risk_score,
        confidence=round(min(1.0, max(0.0, confidence)), 3),
        classification=score_to_classification(risk_score),
        explanation_fr=explanation_fr,
        explanation_en=explanation_en,
        engine_used=engine,
        raw_response=raw,
        processing_time_ms=int((time.time() - start_time) * 1000),
    )


# ── TEXT DETECTOR ─────────────────────────────────────────────
class TextDetector:
    """AI-text + fake-news detection: Gemini → Hugging Face → stylometry."""

    async def analyze(self, text: str, language: str | None = None) -> DetectionResult:
        start = time.time()

        # ── Tier 1: Gemini ─────────────────────────────────────
        gemini_result = await gemini_client.analyze_text(text, language)
        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            disinfo = gemini_result.get("disinformation_probability")
            disinfo = float(disinfo) if disinfo is not None else None
            overall = blend_text_scores(ai_prob, disinfo)
            return _build_result(
                ai_prob=overall,
                confidence=float(gemini_result.get("confidence", 0.5)),
                explanation_fr=gemini_result.get("explanation_fr", ""),
                explanation_en=gemini_result.get("explanation_en", ""),
                engine=f"gemini/{settings.GEMINI_MODEL}",
                raw={"tier": "gemini", "gemini_raw": gemini_result},
                start_time=start,
                key_indicators=list(gemini_result.get("key_indicators", [])),
                sub_scores={
                    "ai_text": round(ai_prob, 3),
                    **({"fake_news": round(disinfo, 3)} if disinfo is not None else {}),
                },
            )

        # ── Tier 2: Hugging Face ensembles ─────────────────────
        hf_ai = await hf_client.detect_ai_text(text)
        hf_fake = await hf_client.detect_fake_news(text)
        if hf_ai or hf_fake:
            # Fill whichever axis HF couldn't score with local heuristics
            if hf_ai:
                ai_prob, ai_models, ai_detail = hf_ai
            else:
                stylo = heuristics.analyze_text_stylometry(text, language)
                ai_prob, ai_models, ai_detail = stylo.probability, [], stylo.detail
            if hf_fake:
                fake_prob, fake_models, fake_detail = hf_fake
            else:
                fn = heuristics.analyze_fake_news_signals(text)
                fake_prob, fake_models, fake_detail = fn.probability, [], fn.detail

            overall = blend_text_scores(ai_prob, fake_prob)
            score = ai_probability_to_risk_score(overall)
            models = ai_models + fake_models
            fr, en = _hf_explanations("text", score, models, round(fake_prob * 100))
            indicators = [
                f"AI_TEXT_PROBABILITY {round(ai_prob * 100)}%",
                f"FAKE_NEWS_PROBABILITY {round(fake_prob * 100)}%",
            ]
            return _build_result(
                ai_prob=overall,
                confidence=0.7 if (hf_ai and hf_fake) else 0.6,
                explanation_fr=fr,
                explanation_en=en,
                engine=f"huggingface/{_short_model_names(models)}",
                raw={
                    "tier": "huggingface",
                    "ai_text_models": ai_detail,
                    "fake_news_models": fake_detail,
                },
                start_time=start,
                key_indicators=indicators,
                sub_scores={"ai_text": round(ai_prob, 3), "fake_news": round(fake_prob, 3)},
            )

        # ── Tier 3: Local stylometric + credibility analysis ───
        stylo = heuristics.analyze_text_stylometry(text, language)
        fake = heuristics.analyze_fake_news_signals(text)
        overall = blend_text_scores(stylo.probability, fake.probability)
        score = ai_probability_to_risk_score(overall)
        indicators = stylo.indicators + fake.indicators
        fr, en = _heuristic_explanations("text", score, indicators, round(fake.probability * 100))
        return _build_result(
            ai_prob=overall,
            confidence=max(stylo.confidence, fake.confidence),
            explanation_fr=fr,
            explanation_en=en,
            engine="stylometric_forensics_v2",
            raw={"tier": "heuristic", "stylometry": stylo.detail, "fake_news": fake.detail},
            start_time=start,
            key_indicators=indicators,
            sub_scores={"ai_text": round(stylo.probability, 3), "fake_news": round(fake.probability, 3)},
        )


# ── IMAGE / DEEPFAKE DETECTOR ─────────────────────────────────
class ImageDetector:
    """Deepfake-image detection: Gemini vision → HF ViT ensemble → forensics."""

    async def analyze(self, image_path: str) -> DetectionResult:
        start = time.time()
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError as e:
            logger.error(f"Cannot read image file {image_path}: {e}")
            return self._unreadable_result(start)
        return await self.analyze_bytes(image_bytes, self._guess_mime(image_path), start)

    async def analyze_bytes(
        self, image_bytes: bytes, mime_type: str, start: float | None = None
    ) -> DetectionResult:
        start = start or time.time()

        # ── Tier 1: Gemini vision ──────────────────────────────
        gemini_result = await gemini_client.analyze_image(image_bytes, mime_type)
        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            return _build_result(
                ai_prob=ai_prob,
                confidence=float(gemini_result.get("confidence", 0.5)),
                explanation_fr=gemini_result.get("explanation_fr", ""),
                explanation_en=gemini_result.get("explanation_en", ""),
                engine=f"gemini/{settings.GEMINI_MODEL}",
                raw={"tier": "gemini", "gemini_raw": gemini_result},
                start_time=start,
                key_indicators=list(gemini_result.get("key_indicators", [])),
            )

        # ── Tier 2: HF deepfake ViT ensemble ───────────────────
        hf_result = await hf_client.detect_deepfake_image(image_bytes, mime_type)
        if hf_result:
            prob, models, detail = hf_result
            score = ai_probability_to_risk_score(prob)
            fr, en = _hf_explanations("image", score, models)
            return _build_result(
                ai_prob=prob,
                confidence=0.75,
                explanation_fr=fr,
                explanation_en=en,
                engine=f"huggingface/{_short_model_names(models)}",
                raw={"tier": "huggingface", "deepfake_models": detail},
                start_time=start,
                key_indicators=[f"DEEPFAKE_PROBABILITY {round(prob * 100)}%"],
            )

        # ── Tier 3: Local image forensics ──────────────────────
        report = heuristics.analyze_image_forensics(image_bytes)
        score = ai_probability_to_risk_score(report.probability)
        fr, en = _heuristic_explanations("image", score, report.indicators)
        return _build_result(
            ai_prob=report.probability,
            confidence=report.confidence,
            explanation_fr=fr,
            explanation_en=en,
            engine="image_forensics_v2",
            raw={"tier": "heuristic", "forensics": report.detail},
            start_time=start,
            key_indicators=report.indicators,
        )

    def _guess_mime(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }.get(ext, "image/jpeg")

    def _unreadable_result(self, start: float) -> DetectionResult:
        return _build_result(
            ai_prob=0.0,
            confidence=0.0,
            explanation_fr="Le fichier image n'a pas pu être lu — analyse impossible. Vérification manuelle requise.",
            explanation_en="The image file could not be read — analysis impossible. Manual review required.",
            engine="file_error",
            raw={"tier": "error", "error": "file_read_error"},
            start_time=start,
            key_indicators=["FILE_READ_ERROR"],
        )


# ── VIDEO DETECTOR ────────────────────────────────────────────
class VideoDetector:
    """
    Detects deepfakes in videos by:
    1. Extracting evenly-spaced key frames using OpenCV
    2. Running each frame through the full image detection cascade
    3. Aggregating scores (p75-weighted, robust to one clean frame)
    """

    async def analyze(self, video_path: str) -> DetectionResult:
        start = time.time()
        frames = await self._extract_frames(video_path)

        if not frames:
            return _build_result(
                ai_prob=0.0,
                confidence=0.0,
                explanation_fr="Impossible d'extraire des images de la vidéo — analyse incomplète. Vérification manuelle requise.",
                explanation_en="Could not extract frames from the video — analysis incomplete. Manual review required.",
                engine="video_error",
                raw={"tier": "error", "error": "no_frames"},
                start_time=start,
                key_indicators=["FRAME_EXTRACTION_FAILED"],
            )

        image_detector = ImageDetector()
        frame_scores: list[int] = []
        frame_engines: set[str] = set()
        frame_indicators: list[str] = []

        for frame_path in frames[:5]:
            result = await image_detector.analyze(frame_path)
            frame_scores.append(result.risk_score)
            frame_engines.add(result.engine_used)
            frame_indicators.extend(result.raw_response.get("key_indicators", [])[:2])

        sorted_scores = sorted(frame_scores)
        p75_idx = int(len(sorted_scores) * 0.75)
        agg_score = sorted_scores[min(p75_idx, len(sorted_scores) - 1)]
        avg_score = int(sum(frame_scores) / len(frame_scores))
        final_score = int((agg_score * 0.6) + (avg_score * 0.4))

        # De-duplicate indicators, keep order
        seen: set[str] = set()
        indicators = [i for i in frame_indicators if not (i in seen or seen.add(i))][:6]

        raw = {
            "tier": "video_frames",
            "frame_scores": frame_scores,
            "frames_analyzed": len(frame_scores),
            "frame_engines": sorted(frame_engines),
        }
        return _build_result(
            ai_prob=final_score / 100,
            confidence=round(min(0.85, 0.4 + len(frame_scores) * 0.08), 3),
            explanation_fr=self._explain_fr(final_score, len(frame_scores)),
            explanation_en=self._explain_en(final_score, len(frame_scores)),
            engine=f"video_frames[{'|'.join(sorted(frame_engines))}]",
            raw=raw,
            start_time=start,
            key_indicators=indicators,
        )

    async def analyze_url(self, url: str) -> DetectionResult:
        """Fetch a video URL (direct or platform via yt-dlp) then analyze it."""
        from app.services.media_fetch import fetch_video

        start = time.time()
        local_path = await fetch_video(url)
        if not local_path:
            return _build_result(
                ai_prob=0.0,
                confidence=0.0,
                explanation_fr=(
                    "La vidéo n'a pas pu être téléchargée depuis l'URL fournie "
                    "(plateforme non prise en charge, vidéo trop longue/volumineuse, ou lien inaccessible). "
                    "Aucun verdict automatique — vérification manuelle requise."
                ),
                explanation_en=(
                    "The video could not be downloaded from the provided URL "
                    "(unsupported platform, video too long/large, or unreachable link). "
                    "No automated verdict — manual review required."
                ),
                engine="video_url_unfetchable",
                raw={"tier": "error", "url": url, "error": "fetch_failed"},
                start_time=start,
                key_indicators=["VIDEO_URL_FETCH_FAILED"],
            )
        result = await self.analyze(local_path)
        result.raw_response["source_video_url"] = url
        return result

    async def _extract_frames(self, video_path: str) -> list[str]:
        """Extract evenly-spaced key frames from video using OpenCV."""
        frames = []
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                logger.warning(f"Cannot open video: {video_path}")
                return []

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 24
            interval = max(1, int(fps * 2))
            indices = list(range(0, max(total_frames, 1), interval))[:5]

            frame_dir = Path(video_path).parent / "frames"
            frame_dir.mkdir(exist_ok=True)

            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    frame_path = str(frame_dir / f"frame_{Path(video_path).stem}_{idx}.jpg")
                    cv2.imwrite(frame_path, frame)
                    frames.append(frame_path)
            cap.release()
        except Exception as e:
            logger.error(f"Frame extraction failed for {video_path}: {e}")
        return frames

    def _explain_fr(self, score: int, n_frames: int) -> str:
        base = f"Analyse de {n_frames} images extraites de la vidéo via la cascade de détection. Score agrégé: {score}/100. "
        return base + _verdict_phrase_fr(score)

    def _explain_en(self, score: int, n_frames: int) -> str:
        base = f"Analysis of {n_frames} frames extracted from the video through the detection cascade. Aggregated score: {score}/100. "
        return base + _verdict_phrase_en(score)


# ── AUDIO DETECTOR ────────────────────────────────────────────
class AudioDetector:
    """Voice-clone detection: Gemini audio → HF wav2vec2 ensemble → signal forensics."""

    async def analyze(self, audio_path: str) -> DetectionResult:
        start = time.time()
        try:
            audio_bytes = Path(audio_path).read_bytes()
        except OSError as e:
            logger.error(f"Cannot read audio file {audio_path}: {e}")
            return _build_result(
                ai_prob=0.0,
                confidence=0.0,
                explanation_fr="Le fichier audio n'a pas pu être lu — analyse impossible. Vérification manuelle requise.",
                explanation_en="The audio file could not be read — analysis impossible. Manual review required.",
                engine="file_error",
                raw={"tier": "error", "error": "file_read_error"},
                start_time=start,
                key_indicators=["FILE_READ_ERROR"],
            )
        return await self.analyze_bytes(audio_bytes, self._guess_mime(audio_path), start)

    async def analyze_bytes(
        self, audio_bytes: bytes, mime_type: str, start: float | None = None
    ) -> DetectionResult:
        start = start or time.time()

        # ── Tier 1: Gemini native audio ────────────────────────
        gemini_result = await gemini_client.analyze_audio(audio_bytes, mime_type)
        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            return _build_result(
                ai_prob=ai_prob,
                confidence=float(gemini_result.get("confidence", 0.5)),
                explanation_fr=gemini_result.get("explanation_fr", ""),
                explanation_en=gemini_result.get("explanation_en", ""),
                engine=f"gemini/{settings.GEMINI_MODEL}",
                raw={"tier": "gemini", "gemini_raw": gemini_result},
                start_time=start,
                key_indicators=list(gemini_result.get("key_indicators", [])),
            )

        # ── Tier 2: HF audio-deepfake ensemble ─────────────────
        hf_result = await hf_client.detect_deepfake_audio(audio_bytes, mime_type)
        if hf_result:
            prob, models, detail = hf_result
            score = ai_probability_to_risk_score(prob)
            fr, en = _hf_explanations("audio", score, models)
            return _build_result(
                ai_prob=prob,
                confidence=0.7,
                explanation_fr=fr,
                explanation_en=en,
                engine=f"huggingface/{_short_model_names(models)}",
                raw={"tier": "huggingface", "deepfake_models": detail},
                start_time=start,
                key_indicators=[f"VOICE_CLONE_PROBABILITY {round(prob * 100)}%"],
            )

        # ── Tier 3: Local audio signal forensics ───────────────
        report = heuristics.analyze_audio_forensics(audio_bytes)
        score = ai_probability_to_risk_score(report.probability)
        fr, en = _heuristic_explanations("audio", score, report.indicators)
        return _build_result(
            ai_prob=report.probability,
            confidence=report.confidence,
            explanation_fr=fr,
            explanation_en=en,
            engine="audio_forensics_v2",
            raw={"tier": "heuristic", "forensics": report.detail},
            start_time=start,
            key_indicators=report.indicators,
        )

    def _guess_mime(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return {
            ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".ogg": "audio/ogg", ".m4a": "audio/mp4",
        }.get(ext, "audio/mpeg")


# ── MAIN ENGINE ORCHESTRATOR ──────────────────────────────────
class DetectionEngine:
    """Routes each submission to the right detector, resolving URL-based
    media through the fetch service first."""

    def __init__(self):
        self.text_detector = TextDetector()
        self.image_detector = ImageDetector()
        self.video_detector = VideoDetector()
        self.audio_detector = AudioDetector()

    async def analyze(
        self,
        content_type: str,
        content_text: str | None = None,
        file_path: str | None = None,
        content_url: str | None = None,
        language: str | None = None,
    ) -> DetectionResult:
        """Route to the correct detector based on content type."""
        logger.info(f"Starting analysis: type={content_type}")

        if content_type == "text":
            if not content_text:
                raise ValueError("content_text is required for text analysis")
            return await self.text_detector.analyze(content_text, language)

        elif content_type == "image":
            file_path = await self._resolve_media(file_path, content_url, "image")
            if not file_path:
                return self._unfetchable("image", content_url)
            return await self.image_detector.analyze(file_path)

        elif content_type == "video":
            if file_path and Path(file_path).exists():
                return await self.video_detector.analyze(file_path)
            elif content_url:
                return await self.video_detector.analyze_url(content_url)
            else:
                raise ValueError("file_path or content_url required for video analysis")

        elif content_type == "audio":
            file_path = await self._resolve_media(file_path, content_url, "audio")
            if not file_path:
                return self._unfetchable("audio", content_url)
            return await self.audio_detector.analyze(file_path)

        else:
            raise ValueError(f"Unknown content type: {content_type}")

    async def _resolve_media(
        self, file_path: str | None, content_url: str | None, kind: str
    ) -> str | None:
        """Prefer the local file; fall back to downloading the URL (covers
        both URL submissions and ephemeral-disk restarts where the local
        copy vanished but a Cloudinary mirror URL survives)."""
        if file_path and Path(file_path).exists():
            return file_path
        if content_url:
            from app.services.media_fetch import fetch_direct
            return await fetch_direct(content_url, kind)
        return file_path if file_path else None

    def _unfetchable(self, kind: str, url: str | None) -> DetectionResult:
        return _build_result(
            ai_prob=0.0,
            confidence=0.0,
            explanation_fr=(
                "Le média n'a pas pu être récupéré depuis l'URL fournie (lien inaccessible, "
                "type de contenu inattendu ou fichier trop volumineux). Aucun verdict automatique — "
                "vérification manuelle requise."
            ),
            explanation_en=(
                "The media could not be retrieved from the provided URL (unreachable link, "
                "unexpected content type, or file too large). No automated verdict — "
                "manual review required."
            ),
            engine=f"{kind}_url_unfetchable",
            raw={"tier": "error", "url": url, "error": "fetch_failed"},
            start_time=time.time(),
            key_indicators=["MEDIA_URL_FETCH_FAILED"],
        )


# Singleton engine instance
detection_engine = DetectionEngine()
