"""
VIGIL-AI Cameroun — Forensic Heuristic Detectors (Tier 3)

Fully local, zero-API detection algorithms used when both Gemini and the
Hugging Face Inference API are unavailable or over quota. These implement
established, published forensic techniques rather than ad-hoc rules:

  Text     — stylometric analysis: burstiness (sentence-length coefficient
             of variation, per GLTR/DetectGPT literature), lexical diversity
             (moving-average type-token ratio), n-gram self-repetition,
             punctuation entropy, formulaic-phrase lexicon (FR/EN), and
             personal-voice markers.
  FakeNews — linguistic credibility signals used in clickbait/disinformation
             research: sensationalist punctuation, ALL-CAPS density, urgency
             and conspiracy lexicons, virality bait, missing attribution.
  Image    — classic image forensics: generator metadata fingerprints
             (Stable Diffusion / Midjourney / DALL-E tags), EXIF camera-trace
             analysis, Error Level Analysis (ELA), noise-residual uniformity
             across blocks, FFT radial-spectrum anomalies (GAN/diffusion
             upsampling artifacts), and canonical AI output dimensions.
  Audio    — signal statistics on decodable WAV audio: frame-energy variance
             (flat energy = synthetic prosody), digital-silence ratio,
             spectral-flatness profile, clipping; container-level checks for
             compressed formats.

Every analyzer returns a HeuristicReport — probability that the content is
AI-generated / disinformation, an honest confidence value, human-readable
indicator strings, and a detail dict preserved in the analysis record.
"""
import io
import logging
import math
import re
import wave
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HeuristicReport:
    probability: float                  # 0.0–1.0 likelihood of AI/disinformation
    confidence: float                   # 0.0–1.0 honesty about the estimate
    indicators: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ═══════════════════════════════════════════════════════════════
# TEXT — Stylometric AI-generation analysis
# ═══════════════════════════════════════════════════════════════

# Formulaic transition phrases heavily over-represented in LLM output.
AI_PHRASES_FR = [
    "il convient de noter", "en conclusion", "il est important de souligner",
    "d'une part", "d'autre part", "cela étant dit", "dans ce contexte",
    "il est essentiel", "par conséquent", "en outre", "néanmoins",
    "il faut mentionner", "en résumé", "à cet égard", "en somme",
    "il est crucial", "de plus", "en définitive", "force est de constater",
    "il va sans dire", "dans l'ensemble", "à la lumière de",
    "il importe de", "en fin de compte", "plus précisément",
]
AI_PHRASES_EN = [
    "it is important to note", "in conclusion", "it should be noted",
    "on the other hand", "moreover", "furthermore", "in addition",
    "it is essential", "as a result", "in summary", "with that said",
    "it is worth mentioning", "it goes without saying", "needless to say",
    "as previously mentioned", "in this context", "delve into",
    "in today's world", "in the realm of", "it is crucial",
    "plays a vital role", "a wide range of", "in essence",
    "ultimately", "significantly", "to summarize", "overall,",
    "additionally", "consequently", "navigate the complexities",
]

FORMAL_CONNECTIVES = [
    "therefore", "consequently", "nevertheless", "furthermore", "moreover",
    "notamment", "cependant", "toutefois", "ainsi", "néanmoins", "en outre",
]

PERSONAL_MARKERS = [
    " je ", "j'", " moi ", " mon ", " ma ", " mes ", " nous ",
    " i ", "i'm", "i've", "i'd", "i'll", " my ", " me ", " we ", " our ",
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-zàâäéèêëîïôöùûüç'-]+", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if len(s.strip()) > 2]


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _shannon_entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    return entropy


def analyze_text_stylometry(text: str, language: str | None = None) -> HeuristicReport:
    """Estimate the probability that text was produced by an LLM."""
    words = _words(text)
    sentences = _sentences(text)
    n_words = len(words)
    text_lower = text.lower()

    indicators: list[str] = []
    detail: dict = {"word_count": n_words, "sentence_count": len(sentences)}
    score = 0.0

    if n_words < 20:
        # Too short for meaningful stylometry
        return HeuristicReport(
            probability=0.15,
            confidence=0.2,
            indicators=["TEXT_TOO_SHORT_FOR_STYLOMETRY"],
            detail=detail,
        )

    # 1 — Formulaic AI-phrase lexicon (strong signal)
    phrase_hits = [p for p in (AI_PHRASES_FR + AI_PHRASES_EN) if p in text_lower]
    if phrase_hits:
        contribution = min(0.30, len(phrase_hits) * 0.06)
        score += contribution
        indicators.append(f"FORMULAIC_AI_PHRASES ({len(phrase_hits)})")
        detail["ai_phrases"] = phrase_hits[:10]

    # 2 — Burstiness: coefficient of variation of sentence lengths.
    #     Human writing is "bursty" (CV typically > 0.45); LLM output is
    #     unusually uniform. Established signal in AI-text detection research.
    if len(sentences) >= 4:
        lengths = [len(_words(s)) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        if mean_len > 0:
            variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
            cv = math.sqrt(variance) / mean_len
            detail["sentence_length_cv"] = round(cv, 3)
            if cv < 0.30:
                score += 0.22
                indicators.append("LOW_BURSTINESS (uniform sentence lengths)")
            elif cv < 0.42:
                score += 0.10
                indicators.append("MODERATE_BURSTINESS")

    # 3 — Lexical diversity: moving-average type-token ratio (window 100).
    if n_words >= 120:
        window = 100
        ratios = []
        for start in range(0, n_words - window + 1, window // 2):
            chunk = words[start:start + window]
            ratios.append(len(set(chunk)) / window)
        mattr = sum(ratios) / len(ratios)
        detail["mattr"] = round(mattr, 3)
        if mattr < 0.52:
            score += 0.12
            indicators.append("LOW_LEXICAL_DIVERSITY")

    # 4 — Trigram self-repetition (templated writing)
    if n_words >= 60:
        trigrams = [tuple(words[i:i + 3]) for i in range(n_words - 2)]
        repeated = len(trigrams) - len(set(trigrams))
        rep_ratio = repeated / max(1, len(trigrams))
        detail["trigram_repetition"] = round(rep_ratio, 4)
        if rep_ratio > 0.06:
            score += 0.12
            indicators.append("REPEATED_PHRASE_TEMPLATES")

    # 5 — Punctuation entropy: humans mix . , ; : ! ? — ( ) " …
    puncts = [".", ",", ";", ":", "!", "?", "—", "-", "(", ")", '"', "'", "…"]
    counts = [text.count(p) for p in puncts]
    if sum(counts) >= 8:
        p_entropy = _shannon_entropy(counts)
        detail["punctuation_entropy"] = round(p_entropy, 3)
        if p_entropy < 1.4:
            score += 0.10
            indicators.append("LOW_PUNCTUATION_VARIETY")

    # 6 — Formal connective density
    formal_count = sum(1 for w in FORMAL_CONNECTIVES if w in text_lower)
    if formal_count >= 3:
        score += min(0.15, formal_count * 0.04)
        indicators.append(f"DENSE_FORMAL_CONNECTIVES ({formal_count})")

    # 7 — Absence of personal voice in long text
    padded = f" {text_lower} "
    personal_count = sum(1 for p in PERSONAL_MARKERS if p in padded)
    if personal_count == 0 and n_words > 80:
        score += 0.10
        indicators.append("NO_PERSONAL_VOICE")
    detail["personal_markers"] = personal_count

    # 8 — Paragraph uniformity
    paragraphs = [p for p in text.split("\n\n") if len(p.strip()) > 40]
    if len(paragraphs) >= 3:
        p_lengths = [len(p) for p in paragraphs]
        p_mean = sum(p_lengths) / len(p_lengths)
        p_cv = math.sqrt(sum((l - p_mean) ** 2 for l in p_lengths) / len(p_lengths)) / p_mean
        detail["paragraph_length_cv"] = round(p_cv, 3)
        if p_cv < 0.22:
            score += 0.08
            indicators.append("UNIFORM_PARAGRAPH_STRUCTURE")

    probability = _clamp(score, 0.02, 0.95)
    # Confidence grows with text length and number of independent signals
    confidence = _clamp(0.35 + min(0.25, n_words / 1600) + min(0.20, len(indicators) * 0.05), 0.2, 0.8)
    detail["stylometry_score"] = round(probability, 3)

    return HeuristicReport(probability, round(confidence, 3), indicators, detail)


# ═══════════════════════════════════════════════════════════════
# TEXT — Fake-news / disinformation credibility signals
# ═══════════════════════════════════════════════════════════════

CLICKBAIT_LEXICON = [
    # French
    "urgent", "alerte", "choc", "choquant", "incroyable", "scandale",
    "vous ne croirez jamais", "partagez avant", "partagez massivement",
    "faites tourner", "les médias cachent", "on nous cache", "on nous ment",
    "révélation", "ils ne veulent pas que vous sachiez", "réveillez-vous",
    "100% vrai", "confirmé par des sources", "avant qu'il ne soit supprimé",
    "miracle", "remède secret", "la vérité sur", "complot",
    # English
    "breaking", "shocking", "unbelievable", "you won't believe",
    "share before", "share this before", "they don't want you to know",
    "the media won't tell you", "mainstream media hides", "wake up",
    "100% true", "confirmed by sources", "before it gets deleted",
    "miracle cure", "secret remedy", "the truth about", "cover-up",
    "exposed", "must share", "viral",
]

ATTRIBUTION_MARKERS = [
    "selon", "d'après", "source", "sources", "a déclaré", "affirme",
    "according to", "said", "stated", "reported", "reuters", "afp",
    "communiqué", "official statement", "étude publiée", "study published",
]


def analyze_fake_news_signals(text: str) -> HeuristicReport:
    """Estimate disinformation probability from linguistic credibility signals."""
    text_lower = text.lower()
    tokens = text.split()
    n_tokens = len(tokens)

    indicators: list[str] = []
    detail: dict = {}
    score = 0.0

    if n_tokens < 15:
        return HeuristicReport(0.1, 0.15, ["TEXT_TOO_SHORT_FOR_CREDIBILITY_ANALYSIS"], detail)

    # 1 — Clickbait / urgency / conspiracy lexicon
    bait_hits = [p for p in CLICKBAIT_LEXICON if p in text_lower]
    if bait_hits:
        score += min(0.45, len(bait_hits) * 0.12)
        indicators.append(f"CLICKBAIT_URGENCY_LANGUAGE ({len(bait_hits)})")
        detail["clickbait_hits"] = bait_hits[:10]

    # 2 — Sensationalist punctuation (!!, ???, !?)
    exclam = text.count("!")
    multi_punct = len(re.findall(r"[!?]{2,}", text))
    exclam_density = exclam / max(1, n_tokens)
    detail["exclamation_density"] = round(exclam_density, 4)
    if exclam_density > 0.03 or multi_punct >= 2:
        score += 0.15
        indicators.append("SENSATIONALIST_PUNCTUATION")

    # 3 — ALL-CAPS shouting (words of 4+ letters fully capitalised)
    caps_words = [w for w in tokens if len(w) >= 4 and w.isupper() and w.isalpha()]
    caps_ratio = len(caps_words) / max(1, n_tokens)
    detail["caps_ratio"] = round(caps_ratio, 4)
    if caps_ratio > 0.04:
        score += 0.15
        indicators.append("EXCESSIVE_CAPITALIZATION")

    # 4 — Missing attribution in claim-heavy text
    has_attribution = any(m in text_lower for m in ATTRIBUTION_MARKERS)
    has_quotes = ('"' in text) or ("«" in text) or ("»" in text)
    if n_tokens > 60 and not has_attribution and not has_quotes:
        score += 0.12
        indicators.append("NO_SOURCE_ATTRIBUTION")
    detail["has_attribution"] = has_attribution

    # 5 — Ellipsis abuse (suspenseful trailing dots)
    ellipsis_count = text.count("...") + text.count("…")
    if ellipsis_count >= 3:
        score += 0.06
        indicators.append("SUSPENSEFUL_ELLIPSES")

    probability = _clamp(score, 0.02, 0.95)
    confidence = _clamp(0.3 + min(0.25, n_tokens / 1200) + min(0.2, len(indicators) * 0.06), 0.15, 0.75)
    detail["fake_news_score"] = round(probability, 3)

    return HeuristicReport(probability, round(confidence, 3), indicators, detail)


def blend_text_scores(ai_probability: float, fake_news_probability: float | None) -> float:
    """
    Combine AI-generation and disinformation probabilities into one risk value.
    A strong signal on either axis dominates: human-written fake news and
    benign AI-assisted text are both risk-relevant, but confirmed AI +
    disinformation compounds.
    """
    if fake_news_probability is None:
        return _clamp(ai_probability)
    blended = 0.55 * ai_probability + 0.45 * fake_news_probability
    return _clamp(max(ai_probability * 0.9, blended))


# ═══════════════════════════════════════════════════════════════
# IMAGE — Classic forensic analysis (ELA / FFT / noise / metadata)
# ═══════════════════════════════════════════════════════════════

AI_GENERATOR_TAGS = [
    "stable diffusion", "stablediffusion", "midjourney", "dall-e", "dalle",
    "flux", "firefly", "imagen", "leonardo", "runway", "comfyui",
    "automatic1111", "invokeai", "novelai", "dream", "generated",
]

COMMON_AI_DIMENSIONS = {512, 576, 640, 704, 768, 832, 896, 960, 1024, 1152, 1280, 1344, 1536, 2048}


def analyze_image_forensics(image_bytes: bytes) -> HeuristicReport:
    """Multi-technique forensic analysis of a still image."""
    indicators: list[str] = []
    detail: dict = {}
    score = 0.30  # neutral prior
    confidence = 0.45

    try:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        width, height = img.size
        detail["dimensions"] = f"{width}x{height}"

        # ── 1. Metadata fingerprints ─────────────────────────
        # Generator software tags (PNG tEXt "parameters" from Stable
        # Diffusion, EXIF Software field, XMP) are near-definitive.
        meta_blobs: list[str] = []
        if img.info:
            for k, v in img.info.items():
                if isinstance(v, (str, bytes)):
                    meta_blobs.append(f"{k}={v if isinstance(v, str) else v[:200]!r}".lower())
        exif = img.getexif()
        camera_make = None
        software = None
        if exif:
            camera_make = exif.get(271)          # Make
            software = exif.get(305)             # Software
            if software:
                meta_blobs.append(str(software).lower())

        meta_text = " ".join(meta_blobs)
        generator_hit = next((t for t in AI_GENERATOR_TAGS if t in meta_text), None)
        if generator_hit:
            indicators.append(f"AI_GENERATOR_METADATA ({generator_hit})")
            detail["generator_tag"] = generator_hit
            return HeuristicReport(0.96, 0.9, indicators, detail)

        if not exif or len(exif) == 0:
            score += 0.12
            indicators.append("NO_EXIF_METADATA")
        elif camera_make:
            score -= 0.15
            indicators.append(f"CAMERA_EXIF_PRESENT ({str(camera_make)[:30]})")
        detail["exif_fields"] = len(exif) if exif else 0

        # ── 2. Canonical AI output dimensions ────────────────
        if width in COMMON_AI_DIMENSIONS and height in COMMON_AI_DIMENSIONS:
            score += 0.10
            indicators.append("CANONICAL_AI_DIMENSIONS")
        elif width % 64 == 0 and height % 64 == 0 and max(width, height) <= 2048:
            score += 0.05
            indicators.append("DIMENSIONS_MULTIPLE_OF_64")

        # Convert to grayscale array for signal analysis (bounded size)
        gray = img.convert("L")
        if max(gray.size) > 1024:
            ratio = 1024 / max(gray.size)
            gray = gray.resize((max(8, int(gray.size[0] * ratio)), max(8, int(gray.size[1] * ratio))))
        arr = np.asarray(gray, dtype=np.float64)

        # ── 3. Error Level Analysis (ELA) ─────────────────────
        # Recompress at JPEG q90 and measure residual error field.
        try:
            rgb = img.convert("RGB")
            if max(rgb.size) > 1024:
                ratio = 1024 / max(rgb.size)
                rgb = rgb.resize((max(8, int(rgb.size[0] * ratio)), max(8, int(rgb.size[1] * ratio))))
            buf = io.BytesIO()
            rgb.save(buf, "JPEG", quality=90)
            buf.seek(0)
            recompressed = Image.open(buf)
            a = np.asarray(rgb, dtype=np.int16)
            b = np.asarray(recompressed, dtype=np.int16)
            ela = np.abs(a - b).mean(axis=2)
            ela_mean = float(ela.mean())
            # Block-wise ELA dispersion — natural photos show wide variation
            bh, bw = ela.shape[0] // 8, ela.shape[1] // 8
            block_means = [
                ela[i * bh:(i + 1) * bh, j * bw:(j + 1) * bw].mean()
                for i in range(8) for j in range(8)
                if bh > 0 and bw > 0
            ]
            if block_means and ela_mean > 0.5:
                ela_cv = float(np.std(block_means) / (np.mean(block_means) + 1e-9))
                detail["ela_mean"] = round(ela_mean, 3)
                detail["ela_block_cv"] = round(ela_cv, 3)
                if ela_cv < 0.25:
                    score += 0.10
                    indicators.append("UNIFORM_ELA_RESPONSE")
        except Exception:  # pragma: no cover - ELA is best-effort
            pass

        # ── 4. Noise-residual uniformity ──────────────────────
        # High-pass residual = image minus local mean; natural sensor noise
        # varies with content/exposure, diffusion output is eerily uniform.
        try:
            kernel = np.ones((3, 3)) / 9.0
            from numpy.lib.stride_tricks import sliding_window_view
            if arr.shape[0] > 16 and arr.shape[1] > 16:
                windows = sliding_window_view(arr, (3, 3))
                local_mean = (windows * kernel).sum(axis=(2, 3))
                residual = arr[1:-1, 1:-1] - local_mean
                gh, gw = residual.shape[0] // 8, residual.shape[1] // 8
                if gh > 2 and gw > 2:
                    block_stds = [
                        float(residual[i * gh:(i + 1) * gh, j * gw:(j + 1) * gw].std())
                        for i in range(8) for j in range(8)
                    ]
                    noise_cv = float(np.std(block_stds) / (np.mean(block_stds) + 1e-9))
                    detail["noise_block_cv"] = round(noise_cv, 3)
                    if noise_cv < 0.30:
                        score += 0.14
                        indicators.append("UNNATURALLY_UNIFORM_NOISE")
                    elif noise_cv > 0.9:
                        score -= 0.05  # strongly textured natural scene
        except Exception:  # pragma: no cover
            pass

        # ── 5. FFT radial spectrum ─────────────────────────────
        # Natural images follow a smooth 1/f power law; GAN/diffusion
        # upsampling leaves excess or spiked high-frequency energy.
        try:
            n = 256
            small = np.asarray(gray.resize((n, n)), dtype=np.float64)
            f = np.fft.fftshift(np.fft.fft2(small - small.mean()))
            power = np.abs(f) ** 2
            cy, cx = n // 2, n // 2
            y, x = np.ogrid[:n, :n]
            r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(int)
            radial = np.bincount(r.ravel(), power.ravel()) / (np.bincount(r.ravel()) + 1e-9)
            radial = radial[2:n // 2]
            log_r = np.log(np.arange(2, n // 2))
            log_p = np.log(radial + 1e-12)
            slope, intercept = np.polyfit(log_r, log_p, 1)
            fitted = slope * log_r + intercept
            tail = slice(int(len(log_p) * 0.7), None)
            tail_excess = float(np.mean(log_p[tail] - fitted[tail]))
            detail["fft_slope"] = round(float(slope), 3)
            detail["fft_tail_excess"] = round(tail_excess, 3)
            if tail_excess > 1.0:
                score += 0.12
                indicators.append("HIGH_FREQUENCY_SPECTRAL_ANOMALY")
            if slope > -1.2:  # abnormally flat spectrum
                score += 0.08
                indicators.append("FLAT_POWER_SPECTRUM")
        except Exception:  # pragma: no cover
            pass

        confidence = _clamp(0.4 + len(indicators) * 0.05, 0.35, 0.65)

    except Exception as e:
        logger.warning(f"Image forensic analysis failed: {e}")
        indicators.append("FORENSIC_ANALYSIS_ERROR")
        detail["error"] = str(e)[:200]
        return HeuristicReport(0.3, 0.15, indicators, detail)

    probability = _clamp(score, 0.03, 0.90)
    detail["forensic_score"] = round(probability, 3)
    return HeuristicReport(probability, round(confidence, 3), indicators, detail)


# ═══════════════════════════════════════════════════════════════
# AUDIO — Signal-statistics analysis
# ═══════════════════════════════════════════════════════════════

def analyze_audio_forensics(audio_bytes: bytes, filename: str = "") -> HeuristicReport:
    """Analyze audio for synthetic-speech indicators. Full signal analysis
    for WAV; container-level checks for compressed formats."""
    indicators: list[str] = []
    detail: dict = {"size_bytes": len(audio_bytes)}
    score = 0.25
    confidence = 0.35

    if len(audio_bytes) < 1024:
        return HeuristicReport(0.35, 0.2, ["AUDIO_TOO_SHORT"], detail)

    header = audio_bytes[:12]
    is_wav = header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    is_mp3 = header[:3] == b"ID3" or header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    is_ogg = header[:4] == b"OggS"
    detail["container"] = "wav" if is_wav else "mp3" if is_mp3 else "ogg" if is_ogg else "unknown"

    if not (is_wav or is_mp3 or is_ogg):
        score += 0.08
        indicators.append("UNRECOGNIZED_AUDIO_CONTAINER")

    if is_wav:
        try:
            import numpy as np

            with wave.open(io.BytesIO(audio_bytes)) as wf:
                n_frames = wf.getnframes()
                framerate = wf.getframerate()
                sampwidth = wf.getsampwidth()
                n_channels = wf.getnchannels()
                raw = wf.readframes(min(n_frames, framerate * 60))  # cap at 60s

            dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sampwidth)
            if dtype is not None and raw:
                samples = np.frombuffer(raw, dtype=dtype).astype(np.float64)
                if n_channels > 1:
                    samples = samples[::n_channels]
                peak = float(np.abs(samples).max()) or 1.0
                samples /= peak
                duration = len(samples) / framerate
                detail["duration_s"] = round(duration, 1)

                # 1 — Frame-energy variance: synthetic voices show flat,
                #     unnaturally regular energy contours.
                frame = max(1, int(framerate * 0.02))  # 20ms
                n_full = (len(samples) // frame) * frame
                if n_full >= frame * 20:
                    energies = (samples[:n_full].reshape(-1, frame) ** 2).mean(axis=1)
                    voiced = energies[energies > energies.max() * 0.01]
                    if len(voiced) > 10:
                        energy_cv = float(voiced.std() / (voiced.mean() + 1e-12))
                        detail["energy_cv"] = round(energy_cv, 3)
                        if energy_cv < 0.55:
                            score += 0.20
                            indicators.append("FLAT_ENERGY_CONTOUR")
                        elif energy_cv < 0.85:
                            score += 0.08
                            indicators.append("REDUCED_ENERGY_DYNAMICS")

                    # 2 — Digital-silence ratio: real recordings have a noise
                    #     floor; TTS output often contains perfect zeros.
                    silent = float((energies < 1e-8).mean())
                    detail["digital_silence_ratio"] = round(silent, 3)
                    if silent > 0.10:
                        score += 0.12
                        indicators.append("PURE_DIGITAL_SILENCE_SEGMENTS")

                # 3 — Spectral flatness on sampled windows
                win = 2048
                if len(samples) >= win * 4:
                    flatness_vals = []
                    for start in np.linspace(0, len(samples) - win, 8).astype(int):
                        seg = samples[start:start + win] * np.hanning(win)
                        mag = np.abs(np.fft.rfft(seg)) + 1e-12
                        flatness_vals.append(float(np.exp(np.mean(np.log(mag))) / np.mean(mag)))
                    sf_mean = sum(flatness_vals) / len(flatness_vals)
                    detail["spectral_flatness"] = round(sf_mean, 4)
                    if sf_mean < 0.0005:
                        score += 0.10
                        indicators.append("OVERLY_TONAL_SPECTRUM")

                # 4 — Clipping
                clip_ratio = float((np.abs(samples) > 0.999).mean())
                if clip_ratio > 0.02:
                    score -= 0.05  # heavy clipping is typical of cheap real recordings
                    indicators.append("ANALOG_STYLE_CLIPPING")

                confidence = _clamp(0.45 + len(indicators) * 0.05, 0.4, 0.65)
        except Exception as e:
            logger.warning(f"WAV signal analysis failed: {e}")
            indicators.append("WAV_DECODE_ERROR")
    else:
        # Compressed container: limited but still useful checks
        if is_mp3 and header[:3] == b"ID3":
            indicators.append("ID3_TAGS_PRESENT")
            score -= 0.03
        # Very small "voice message" files are a common vishing artifact
        if len(audio_bytes) < 40_000:
            score += 0.08
            indicators.append("VERY_SHORT_CLIP")
        confidence = 0.3

    probability = _clamp(score, 0.05, 0.85)
    detail["audio_score"] = round(probability, 3)
    return HeuristicReport(probability, round(confidence, 3), indicators, detail)
