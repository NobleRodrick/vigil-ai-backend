"""
VIGIL-AI Cameroun — AI Detection Engine
Uses Google Gemini API (FREE tier) for multi-modal AI-content detection.

Strategy:
  1. Call Gemini API (free key at https://aistudio.google.com/apikey)
     — single multi-modal model handles text, image, AND audio
  2. If unavailable/rate-limited/no key set → use heuristic scoring
  3. Gemini is prompted to return strict JSON with a probability score,
     key indicators, and bilingual (FR/EN) explanations

Free tier (gemini-2.0-flash): 15 requests/min, 1M tokens/min, 1500 requests/day
— comfortably enough for an MVP demo.
"""
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

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


# ── GEMINI API CLIENT ──────────────────────────────────────────
class GeminiClient:
    """
    Thin wrapper around the Gemini `generateContent` REST endpoint.
    Uses `responseMimeType: application/json` + a response schema so
    Gemini always returns clean, parseable JSON — no markdown fences,
    no prose wrapper.
    """

    RESPONSE_SCHEMA = {
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

    async def _generate(self, parts: list[dict]) -> dict | None:
        """Send a multimodal generateContent request and return parsed JSON, or None on failure."""
        if not self.enabled:
            return None

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": self.RESPONSE_SCHEMA,
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
                parsed = json.loads(text)
                return parsed
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to parse Gemini response: {e}")
                return None

    async def analyze_text(self, text: str, language: str | None) -> dict | None:
        prompt = TEXT_ANALYSIS_PROMPT.format(
            language=language or "auto-detect (French, English, or Cameroonian Pidgin)",
            content=text[:8000],  # Gemini context is large, but cap for cost/speed
        )
        return await self._generate([{"text": prompt}])

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
            logger.info("Audio file exceeds Gemini inline limit — skipping Gemini, using heuristic")
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

Analyze the following text and assess the probability that it was generated or substantially \
rewritten by an AI language model (e.g. ChatGPT, Gemini, Claude) rather than written naturally \
by a human. Consider: uniform sentence structure, generic formal phrasing, absence of personal \
voice or local context, repetitive transitional phrases, and any factual claims that read as \
plausible-sounding but unverifiable propaganda.

Language hint: {language}

TEXT TO ANALYZE:
\"\"\"
{content}
\"\"\"

Respond with the probability the text is AI-generated (0.0 = certainly human-written, \
1.0 = certainly AI-generated), your confidence in that assessment, 2-5 short key indicators \
that informed your judgment, and a one-paragraph explanation in BOTH French and English suitable \
for a non-technical government analyst reading a case file."""

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


# ── TEXT DETECTOR ─────────────────────────────────────────────
class TextDetector:
    """Detects AI-generated text using Gemini, with heuristic fallback."""

    # Patterns that suggest AI-generated content in French and English
    AI_TEXT_PATTERNS_FR = [
        "il convient de noter", "en conclusion", "il est important de souligner",
        "d'une part", "d'autre part", "cela étant dit", "dans ce contexte",
        "il est essentiel", "par conséquent", "en outre", "néanmoins",
        "il faut mentionner", "en résumé", "à cet égard",
    ]
    AI_TEXT_PATTERNS_EN = [
        "it is important to note", "in conclusion", "it should be noted",
        "on the other hand", "moreover", "furthermore", "in addition",
        "it is essential", "as a result", "in summary", "with that said",
        "it is worth mentioning", "it goes without saying", "needless to say",
        "as previously mentioned", "in this context",
    ]

    async def analyze(self, text: str, language: str | None = None) -> DetectionResult:
        start = time.time()

        gemini_result = await gemini_client.analyze_text(text, language)

        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            confidence = float(gemini_result.get("confidence", 0.5))
            explanation_fr = gemini_result.get("explanation_fr", "")
            explanation_en = gemini_result.get("explanation_en", "")
            raw = {"gemini_raw": gemini_result}
            engine = f"gemini/{settings.GEMINI_MODEL}"
        else:
            ai_prob, raw = self._heuristic_analysis(text, language)
            confidence = ai_prob
            risk_score_tmp = ai_probability_to_risk_score(ai_prob)
            explanation_fr = self._explain_fr(risk_score_tmp, raw)
            explanation_en = self._explain_en(risk_score_tmp, raw)
            engine = "heuristic_v1"

        risk_score = ai_probability_to_risk_score(ai_prob)
        classification = score_to_classification(risk_score)
        ms = int((time.time() - start) * 1000)

        return DetectionResult(
            risk_score=risk_score,
            confidence=round(confidence, 3),
            classification=classification,
            explanation_fr=explanation_fr,
            explanation_en=explanation_en,
            engine_used=engine,
            raw_response=raw,
            processing_time_ms=ms,
        )

    def _heuristic_analysis(self, text: str, language: str | None) -> tuple[float, dict]:
        """Pattern-based AI text detection as a fallback when Gemini is unavailable."""
        text_lower = text.lower()
        score = 0.0
        signals = []

        patterns = self.AI_TEXT_PATTERNS_FR + self.AI_TEXT_PATTERNS_EN
        matches = [p for p in patterns if p in text_lower]
        if matches:
            pattern_score = min(0.5, len(matches) * 0.05)
            score += pattern_score
            signals.append(f"AI_PATTERNS:{len(matches)}")

        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if len(sentences) >= 3:
            lengths = [len(s) for s in sentences]
            avg_len = sum(lengths) / len(lengths)
            variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
            if variance < 100 and avg_len > 50:
                score += 0.2
                signals.append("UNIFORM_SENTENCES")

        formal_indicators = [
            "therefore", "consequently", "nevertheless", "furthermore",
            "notamment", "cependant", "toutefois", "ainsi",
        ]
        formal_count = sum(1 for w in formal_indicators if w in text_lower)
        if formal_count >= 3:
            score += min(0.25, formal_count * 0.05)
            signals.append(f"FORMAL_LANGUAGE:{formal_count}")

        personal = ["je", "j'", "moi", "i ", "my ", "me ", "i'm", "i've", "i'd"]
        personal_count = sum(1 for p in personal if p in text_lower)
        if personal_count == 0 and len(text) > 200:
            score += 0.1
            signals.append("NO_PERSONAL_PRONOUNS")

        score = min(1.0, score)
        return score, {"heuristic_signals": signals, "heuristic_score": score}

    def _explain_fr(self, score: int, raw: dict) -> str:
        if score <= 29:
            return (
                f"Ce texte présente des caractéristiques d'une rédaction humaine. "
                f"Score de risque: {score}/100. Aucune signature caractéristique d'une IA n'a été détectée."
            )
        elif score <= 69:
            signals = raw.get("heuristic_signals", [])
            sig_text = ", ".join(signals) if signals else "structure formelle inhabituelle"
            return (
                f"Ce texte présente des caractéristiques qui suggèrent une possible génération par IA. "
                f"Score de risque: {score}/100. Indicateurs détectés: {sig_text}. "
                f"Une vérification manuelle approfondie est recommandée."
            )
        else:
            return (
                f"Ce texte présente de fortes caractéristiques d'un contenu généré par intelligence artificielle. "
                f"Score de risque: {score}/100. Une action immédiate est recommandée."
            )

    def _explain_en(self, score: int, raw: dict) -> str:
        if score <= 29:
            return (
                f"This text exhibits characteristics consistent with human authorship. "
                f"Risk score: {score}/100. No significant AI-generation signatures were detected."
            )
        elif score <= 69:
            signals = raw.get("heuristic_signals", [])
            sig_text = ", ".join(signals) if signals else "unusually formal structure"
            return (
                f"This text shows characteristics that suggest possible AI generation. "
                f"Risk score: {score}/100. Detected indicators: {sig_text}. "
                f"Manual review is recommended."
            )
        else:
            return (
                f"This text exhibits strong characteristics of AI-generated content. "
                f"Risk score: {score}/100. Immediate action is recommended."
            )


# ── IMAGE / DEEPFAKE DETECTOR ─────────────────────────────────
class ImageDetector:
    """Detects AI-generated and deepfake images using Gemini vision, with metadata fallback."""

    async def analyze(self, image_path: str) -> DetectionResult:
        start = time.time()

        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError as e:
            logger.error(f"Cannot read image file {image_path}: {e}")
            return self._error_result(start)

        mime_type = self._guess_mime(image_path)
        gemini_result = await gemini_client.analyze_image(image_bytes, mime_type)

        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            confidence = float(gemini_result.get("confidence", 0.5))
            explanation_fr = gemini_result.get("explanation_fr", "")
            explanation_en = gemini_result.get("explanation_en", "")
            raw = {"gemini_raw": gemini_result}
            engine = f"gemini/{settings.GEMINI_MODEL}"
        else:
            ai_prob, raw = await self._metadata_analysis(image_bytes, image_path)
            confidence = ai_prob
            risk_score_tmp = ai_probability_to_risk_score(ai_prob)
            explanation_fr = self._explain_fr(risk_score_tmp)
            explanation_en = self._explain_en(risk_score_tmp)
            engine = "metadata_heuristic_v1"

        risk_score = ai_probability_to_risk_score(ai_prob)
        classification = score_to_classification(risk_score)
        ms = int((time.time() - start) * 1000)

        return DetectionResult(
            risk_score=risk_score,
            confidence=round(confidence, 3),
            classification=classification,
            explanation_fr=explanation_fr,
            explanation_en=explanation_en,
            engine_used=engine,
            raw_response=raw,
            processing_time_ms=ms,
        )

    def _guess_mime(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }.get(ext, "image/jpeg")

    async def _metadata_analysis(self, image_bytes: bytes, path: str) -> tuple[float, dict]:
        """Analyze image metadata for AI-generation indicators (fallback only)."""
        import io
        signals = []
        score = 0.3

        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))

            exif_data = img._getexif() if hasattr(img, "_getexif") else None
            if exif_data is None:
                score += 0.2
                signals.append("NO_EXIF_DATA")

            if hasattr(img, "info") and not img.info:
                score += 0.1
                signals.append("NO_METADATA")

            width, height = img.size
            ratio = width / height if height > 0 else 1.0
            common_ai_ratios = [1.0, 0.75, 1.333, 1.5, 0.667]
            if any(abs(ratio - r) < 0.01 for r in common_ai_ratios):
                score += 0.05
                signals.append("COMMON_AI_ASPECT_RATIO")

        except Exception as e:
            logger.warning(f"Image metadata analysis failed: {e}")
            signals.append("ANALYSIS_ERROR")

        return min(0.85, score), {"metadata_signals": signals, "heuristic_score": score}

    def _error_result(self, start: float) -> DetectionResult:
        ms = int((time.time() - start) * 1000)
        return DetectionResult(
            risk_score=0, confidence=0.0, classification="safe",
            explanation_fr="Erreur lors de l'analyse de l'image.",
            explanation_en="Error during image analysis.",
            engine_used="error", raw_response={"error": "file_read_error"},
            processing_time_ms=ms,
        )

    def _explain_fr(self, score: int) -> str:
        if score <= 29:
            return f"Cette image présente les caractéristiques d'une photographie authentique. Score: {score}/100."
        elif score <= 69:
            return f"Cette image présente certaines anomalies pouvant indiquer une manipulation numérique. Score: {score}/100."
        else:
            return f"Cette image présente de fortes indications d'un deepfake ou d'une génération par IA. Score: {score}/100."

    def _explain_en(self, score: int) -> str:
        if score <= 29:
            return f"This image shows characteristics of an authentic photograph. Score: {score}/100."
        elif score <= 69:
            return f"This image shows anomalies that may indicate digital manipulation. Score: {score}/100."
        else:
            return f"This image shows strong indicators of a deepfake or AI-generated content. Score: {score}/100."


# ── VIDEO DETECTOR ────────────────────────────────────────────
class VideoDetector:
    """
    Detects deepfakes in videos by:
    1. Extracting key frames using OpenCV
    2. Running each frame through the Gemini-powered image detector
    3. Aggregating scores
    """

    async def analyze(self, video_path: str) -> DetectionResult:
        start = time.time()
        frames = await self._extract_frames(video_path)

        if not frames:
            ms = int((time.time() - start) * 1000)
            return DetectionResult(
                risk_score=0, confidence=0.0, classification="safe",
                explanation_fr="Impossible d'extraire les images de la vidéo.",
                explanation_en="Could not extract frames from the video.",
                engine_used="video_error", raw_response={"error": "no_frames"},
                processing_time_ms=ms,
            )

        image_detector = ImageDetector()
        frame_scores = []

        for frame_path in frames[:5]:
            result = await image_detector.analyze(frame_path)
            frame_scores.append(result.risk_score)

        sorted_scores = sorted(frame_scores)
        p75_idx = int(len(sorted_scores) * 0.75)
        agg_score = sorted_scores[min(p75_idx, len(sorted_scores) - 1)]
        avg_score = int(sum(frame_scores) / len(frame_scores))
        final_score = int((agg_score * 0.6) + (avg_score * 0.4))

        classification = score_to_classification(final_score)
        ms = int((time.time() - start) * 1000)

        raw = {"frame_scores": frame_scores, "frames_analyzed": len(frame_scores)}

        return DetectionResult(
            risk_score=final_score,
            confidence=round(final_score / 100, 3),
            classification=classification,
            explanation_fr=self._explain_fr(final_score, len(frame_scores)),
            explanation_en=self._explain_en(final_score, len(frame_scores)),
            engine_used="gemini_frame_analysis_v1",
            raw_response=raw,
            processing_time_ms=ms,
        )

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
            indices = list(range(0, total_frames, interval))[:5]

            frame_dir = Path(video_path).parent / "frames"
            frame_dir.mkdir(exist_ok=True)

            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    frame_path = str(frame_dir / f"frame_{idx}.jpg")
                    cv2.imwrite(frame_path, frame)
                    frames.append(frame_path)
            cap.release()
        except Exception as e:
            logger.error(f"Frame extraction failed for {video_path}: {e}")
        return frames

    def _explain_fr(self, score: int, n_frames: int) -> str:
        base = f"Analyse de {n_frames} images extraites de la vidéo (via Gemini Vision). Score agrégé: {score}/100. "
        if score <= 29:
            return base + "Aucun indicateur de deepfake n'a été détecté dans les images analysées."
        elif score <= 69:
            return base + "Certaines images présentent des anomalies potentielles. Une analyse experte est recommandée."
        else:
            return base + "De fortes indications de manipulation par deepfake ont été détectées dans plusieurs images."

    def _explain_en(self, score: int, n_frames: int) -> str:
        base = f"Analysis of {n_frames} frames extracted from the video (via Gemini Vision). Aggregated score: {score}/100. "
        if score <= 29:
            return base + "No deepfake indicators were detected in the analyzed frames."
        elif score <= 69:
            return base + "Some frames show potential anomalies. Expert analysis is recommended."
        else:
            return base + "Strong deepfake manipulation indicators were detected across multiple frames."


# ── AUDIO DETECTOR ────────────────────────────────────────────
class AudioDetector:
    """Detects voice cloning and synthetic speech using Gemini's native audio understanding."""

    async def analyze(self, audio_path: str) -> DetectionResult:
        start = time.time()

        try:
            audio_bytes = Path(audio_path).read_bytes()
        except OSError as e:
            logger.error(f"Cannot read audio file {audio_path}: {e}")
            ms = int((time.time() - start) * 1000)
            return DetectionResult(
                risk_score=0, confidence=0.0, classification="safe",
                explanation_fr="Erreur lors de la lecture du fichier audio.",
                explanation_en="Error reading the audio file.",
                engine_used="error", raw_response={"error": "file_read_error"},
                processing_time_ms=ms,
            )

        mime_type = self._guess_mime(audio_path)
        gemini_result = await gemini_client.analyze_audio(audio_bytes, mime_type)

        if gemini_result:
            ai_prob = float(gemini_result.get("ai_generated_probability", 0.0))
            confidence = float(gemini_result.get("confidence", 0.5))
            explanation_fr = gemini_result.get("explanation_fr", "")
            explanation_en = gemini_result.get("explanation_en", "")
            raw = {"gemini_raw": gemini_result}
            engine = f"gemini/{settings.GEMINI_MODEL}"
        else:
            ai_prob, raw = await self._heuristic_analysis(audio_path, audio_bytes)
            confidence = ai_prob
            risk_score_tmp = ai_probability_to_risk_score(ai_prob)
            explanation_fr = self._explain_fr(risk_score_tmp)
            explanation_en = self._explain_en(risk_score_tmp)
            engine = "audio_heuristic_v1"

        risk_score = ai_probability_to_risk_score(ai_prob)
        classification = score_to_classification(risk_score)
        ms = int((time.time() - start) * 1000)

        return DetectionResult(
            risk_score=risk_score,
            confidence=round(confidence, 3),
            classification=classification,
            explanation_fr=explanation_fr,
            explanation_en=explanation_en,
            engine_used=engine,
            raw_response=raw,
            processing_time_ms=ms,
        )

    def _guess_mime(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return {
            ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".ogg": "audio/ogg", ".m4a": "audio/mp4",
        }.get(ext, "audio/mpeg")

    async def _heuristic_analysis(self, audio_path: str, audio_bytes: bytes) -> tuple[float, dict]:
        """Fallback heuristic when Gemini is unavailable or file too large."""
        signals = []
        score = 0.25

        try:
            file_size = len(audio_bytes)
            if file_size < 1024:
                signals.append("VERY_SHORT_AUDIO")
                score += 0.1

            header = audio_bytes[:12]
            if not (header[:3] == b"ID3" or header[:2] in (b"\xff\xfb", b"\xff\xf3")):
                if header[:4] == b"RIFF":
                    signals.append("WAV_FORMAT")
                else:
                    signals.append("UNKNOWN_AUDIO_FORMAT")
                    score += 0.1

        except Exception as e:
            logger.warning(f"Audio heuristic analysis failed: {e}")
            signals.append("ANALYSIS_ERROR")

        return min(0.8, score), {"audio_signals": signals, "heuristic_score": score}

    def _explain_fr(self, score: int) -> str:
        if score <= 29:
            return f"Cet enregistrement audio ne présente pas d'indicateurs significatifs de voix clonée ou synthétique. Score: {score}/100."
        elif score <= 69:
            return f"Cet enregistrement audio présente certaines caractéristiques inhabituelles. Score: {score}/100."
        else:
            return f"Cet enregistrement audio présente de fortes indications d'une voix synthétique ou clonée par IA. Score: {score}/100."

    def _explain_en(self, score: int) -> str:
        if score <= 29:
            return f"This audio recording shows no significant indicators of voice cloning or synthetic speech. Score: {score}/100."
        elif score <= 69:
            return f"This audio shows some unusual characteristics. Score: {score}/100."
        else:
            return f"This audio recording shows strong indicators of AI-generated or voice-cloned speech. Score: {score}/100."


# ── MAIN ENGINE ORCHESTRATOR ──────────────────────────────────
class DetectionEngine:
    """Main orchestrator that routes to the appropriate detector based on content type."""

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
            if not file_path:
                raise ValueError("file_path is required for image analysis")
            return await self.image_detector.analyze(file_path)

        elif content_type == "video":
            if file_path:
                return await self.video_detector.analyze(file_path)
            elif content_url:
                return DetectionResult(
                    risk_score=0,
                    confidence=0.0,
                    classification="safe",
                    explanation_fr="Analyse de vidéo par URL en attente d'implémentation complète.",
                    explanation_en="Video URL analysis pending full implementation.",
                    engine_used="url_stub",
                    raw_response={"url": content_url},
                    processing_time_ms=0,
                )
            else:
                raise ValueError("file_path or content_url required for video analysis")

        elif content_type == "audio":
            if not file_path:
                raise ValueError("file_path is required for audio analysis")
            return await self.audio_detector.analyze(file_path)

        else:
            raise ValueError(f"Unknown content type: {content_type}")


# Singleton engine instance
detection_engine = DetectionEngine()
