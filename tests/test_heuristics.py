"""
Unit tests for the forensic heuristic detectors (Tier 3).
These run fully offline — no database, no network, no API keys.
"""
import io
import math
import struct
import wave

import pytest

from app.ai import heuristics
from app.ai.heuristics import (
    HeuristicReport,
    analyze_audio_forensics,
    analyze_fake_news_signals,
    analyze_image_forensics,
    analyze_text_stylometry,
    blend_text_scores,
)


# ── Sample corpora ─────────────────────────────────────────────
AI_LIKE_TEXT = (
    "Il convient de noter que cette initiative gouvernementale représente une avancée "
    "majeure pour le développement du pays. En outre, il est important de souligner que "
    "les bénéfices pour la population seront considérables dans ce contexte. Par conséquent, "
    "les autorités compétentes ont mis en place des mécanismes appropriés pour assurer la mise "
    "en œuvre effective de ce programme. De plus, il est essentiel de mentionner que les "
    "partenaires internationaux ont exprimé leur soutien total à cette démarche. En conclusion, "
    "cette annonce constitue une étape déterminante vers la modernisation des infrastructures. "
    "Il va sans dire que les résultats attendus seront à la hauteur des ambitions affichées. "
    "En résumé, la population peut légitimement espérer des retombées positives significatives."
)

HUMAN_LIKE_TEXT = (
    "Franchement, j'étais au marché de Mokolo hier et les prix ont encore grimpé! Ma voisine "
    "m'a dit que le sac de riz coûte maintenant 28 000 francs. Tu te rends compte? Bon, on fait "
    "comment... Le taximan qui m'a déposée n'arrêtait pas de râler sur le carburant aussi. "
    "Moi je pense qu'avant Noël ça va encore monter. Mon frère qui vit à Douala dit que là-bas "
    "c'est pire. Enfin bref. J'ai quand même acheté mes condiments — le ndolé n'attend pas!"
)

FAKE_NEWS_TEXT = (
    "URGENT!!! Les médias cachent la VÉRITÉ sur le nouveau vaccin! Un remède secret que le "
    "gouvernement ne veut PAS que vous sachiez... PARTAGEZ AVANT SUPPRESSION!!! C'est 100% vrai, "
    "confirmé par des sources anonymes. Ce SCANDALE va tout changer. Réveillez-vous! Ils ne "
    "veulent pas que vous sachiez ce qui se passe VRAIMENT. INCROYABLE mais vrai!!!"
)

CREDIBLE_NEWS_TEXT = (
    'Selon le communiqué publié mardi par le ministère de la Santé, la campagne de vaccination '
    'débutera le 15 mars dans les régions du Centre et du Littoral. "Nous avons mobilisé 450 '
    'équipes de terrain", a déclaré le Dr Manaouda lors de la conférence de presse. D\'après '
    "les données de l'OMS, la couverture vaccinale actuelle atteint 67% dans la région. Reuters "
    "rapporte que des campagnes similaires sont prévues au Tchad et au Gabon."
)


# ═══════════════════════════════════════════════════════════════
# Text stylometry
# ═══════════════════════════════════════════════════════════════

class TestTextStylometry:
    def test_ai_like_text_scores_higher_than_human(self):
        ai = analyze_text_stylometry(AI_LIKE_TEXT, "fr")
        human = analyze_text_stylometry(HUMAN_LIKE_TEXT, "fr")
        assert ai.probability > human.probability
        assert ai.probability > 0.4, f"AI-like text should score >0.4, got {ai.probability}"
        assert human.probability < 0.4, f"Human text should score <0.4, got {human.probability}"

    def test_detects_formulaic_phrases(self):
        report = analyze_text_stylometry(AI_LIKE_TEXT, "fr")
        assert any("FORMULAIC" in i for i in report.indicators)

    def test_human_text_has_personal_voice(self):
        report = analyze_text_stylometry(HUMAN_LIKE_TEXT, "fr")
        assert not any("NO_PERSONAL_VOICE" in i for i in report.indicators)
        assert report.detail["personal_markers"] > 0

    def test_short_text_returns_low_confidence(self):
        report = analyze_text_stylometry("Bonjour tout le monde.", "fr")
        assert report.confidence <= 0.25
        assert "TEXT_TOO_SHORT_FOR_STYLOMETRY" in report.indicators

    def test_probability_bounds(self):
        for text in [AI_LIKE_TEXT, HUMAN_LIKE_TEXT, "x " * 500, "word"]:
            report = analyze_text_stylometry(text)
            assert 0.0 <= report.probability <= 1.0
            assert 0.0 <= report.confidence <= 1.0

    def test_returns_detail_metrics(self):
        report = analyze_text_stylometry(AI_LIKE_TEXT, "fr")
        assert report.detail["word_count"] > 50
        assert "sentence_length_cv" in report.detail


# ═══════════════════════════════════════════════════════════════
# Fake-news credibility signals
# ═══════════════════════════════════════════════════════════════

class TestFakeNewsSignals:
    def test_fake_news_scores_higher_than_credible(self):
        fake = analyze_fake_news_signals(FAKE_NEWS_TEXT)
        credible = analyze_fake_news_signals(CREDIBLE_NEWS_TEXT)
        assert fake.probability > credible.probability
        assert fake.probability > 0.5, f"Fake news should score >0.5, got {fake.probability}"
        assert credible.probability < 0.3, f"Credible news should score <0.3, got {credible.probability}"

    def test_detects_clickbait_language(self):
        report = analyze_fake_news_signals(FAKE_NEWS_TEXT)
        assert any("CLICKBAIT" in i for i in report.indicators)

    def test_detects_sensationalist_punctuation(self):
        report = analyze_fake_news_signals(FAKE_NEWS_TEXT)
        assert any("PUNCTUATION" in i or "CAPITALIZATION" in i for i in report.indicators)

    def test_credible_text_has_attribution(self):
        report = analyze_fake_news_signals(CREDIBLE_NEWS_TEXT)
        assert report.detail["has_attribution"] is True
        assert not any("NO_SOURCE_ATTRIBUTION" in i for i in report.indicators)

    def test_short_text_low_confidence(self):
        report = analyze_fake_news_signals("Trop court.")
        assert report.confidence <= 0.2


# ═══════════════════════════════════════════════════════════════
# Score blending
# ═══════════════════════════════════════════════════════════════

class TestBlendTextScores:
    def test_no_fake_news_passthrough(self):
        assert blend_text_scores(0.7, None) == pytest.approx(0.7)

    def test_high_fake_news_raises_low_ai_score(self):
        blended = blend_text_scores(0.1, 0.9)
        assert blended > 0.4  # disinformation alone must flag the content

    def test_high_ai_score_not_diluted_by_low_fake_news(self):
        blended = blend_text_scores(0.9, 0.05)
        assert blended >= 0.8

    def test_bounds(self):
        for ai in (0.0, 0.5, 1.0):
            for fake in (None, 0.0, 0.5, 1.0):
                assert 0.0 <= blend_text_scores(ai, fake) <= 1.0


# ═══════════════════════════════════════════════════════════════
# Image forensics
# ═══════════════════════════════════════════════════════════════

def _make_png(width=512, height=512, noisy=False) -> bytes:
    from PIL import Image
    import numpy as np

    if noisy:
        rng = np.random.default_rng(42)
        arr = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
        # Spatially varying noise like a real photo (bright/dark regions)
        gradient = np.linspace(0.2, 1.0, width)[None, :, None]
        arr = (arr * gradient).astype(np.uint8)
        img = Image.fromarray(arr)
    else:
        img = Image.new("RGB", (width, height), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestImageForensics:
    def test_returns_valid_report(self):
        report = analyze_image_forensics(_make_png())
        assert isinstance(report, HeuristicReport)
        assert 0.0 <= report.probability <= 1.0
        assert report.detail["dimensions"] == "512x512"

    def test_flags_missing_exif(self):
        report = analyze_image_forensics(_make_png())
        assert any("NO_EXIF" in i for i in report.indicators)

    def test_flags_canonical_ai_dimensions(self):
        report = analyze_image_forensics(_make_png(512, 512))
        assert any("CANONICAL_AI_DIMENSIONS" in i for i in report.indicators)

    def test_detects_generator_metadata(self):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        img = Image.new("RGB", (512, 512), (100, 120, 140))
        meta = PngInfo()
        meta.add_text("parameters", "masterpiece, ultra detailed, Steps: 30, Sampler: Euler a, Model: stable diffusion xl")
        buf = io.BytesIO()
        img.save(buf, "PNG", pnginfo=meta)

        report = analyze_image_forensics(buf.getvalue())
        assert report.probability > 0.9
        assert any("AI_GENERATOR_METADATA" in i for i in report.indicators)

    def test_garbage_bytes_do_not_raise(self):
        report = analyze_image_forensics(b"\x00\x01\x02 this is not an image")
        assert isinstance(report, HeuristicReport)
        assert report.confidence <= 0.2


# ═══════════════════════════════════════════════════════════════
# Audio forensics
# ═══════════════════════════════════════════════════════════════

def _make_wav(duration_s=3.0, framerate=16000, synthetic=True) -> bytes:
    """Generate a WAV file: `synthetic` = flat sine (TTS-like),
    otherwise amplitude-varying noisy speech-like signal."""
    import numpy as np

    t = np.linspace(0, duration_s, int(framerate * duration_s), endpoint=False)
    if synthetic:
        signal = 0.5 * np.sin(2 * math.pi * 220 * t)  # perfectly flat tone
    else:
        rng = np.random.default_rng(7)
        envelope = np.abs(np.sin(2 * math.pi * 0.7 * t)) ** 2 + 0.05
        carrier = np.sin(2 * math.pi * 180 * t) + 0.4 * rng.standard_normal(len(t))
        signal = 0.6 * envelope * carrier
    samples = (signal / np.abs(signal).max() * 32000).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


class TestAudioForensics:
    def test_wav_is_recognized(self):
        report = analyze_audio_forensics(_make_wav())
        assert report.detail["container"] == "wav"

    def test_flat_tone_scores_higher_than_dynamic_signal(self):
        flat = analyze_audio_forensics(_make_wav(synthetic=True))
        dynamic = analyze_audio_forensics(_make_wav(synthetic=False))
        assert flat.probability >= dynamic.probability
        assert any("FLAT_ENERGY" in i or "REDUCED_ENERGY" in i for i in flat.indicators)

    def test_tiny_payload_flagged(self):
        report = analyze_audio_forensics(b"abc")
        assert "AUDIO_TOO_SHORT" in report.indicators

    def test_mp3_header_recognized(self):
        fake_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 50_000
        report = analyze_audio_forensics(fake_mp3)
        assert report.detail["container"] == "mp3"

    def test_bounds(self):
        for payload in [_make_wav(), b"garbage" * 1000, b""]:
            report = analyze_audio_forensics(payload)
            assert 0.0 <= report.probability <= 1.0
            assert 0.0 <= report.confidence <= 1.0
