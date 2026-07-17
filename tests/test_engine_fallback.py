"""
Tests for the three-tier detection cascade.

Verifies the core resilience guarantee: when Gemini AND Hugging Face are
unavailable (no keys / quota exhausted / network down), every content type
still produces a complete, well-formed DetectionResult from the local
forensic tier — never an exception, never an error surfaced to the analyst.

Fully offline — external clients are monkeypatched.
"""
import io
import wave

import numpy as np
import pytest

from app.ai import engine as engine_module
from app.ai.engine import (
    DetectionEngine,
    DetectionResult,
    ai_probability_to_risk_score,
    score_to_classification,
)
from app.ai.huggingface import HuggingFaceClient


@pytest.fixture
def offline_engine(monkeypatch):
    """Engine with both remote tiers disabled (simulates quota exhaustion)."""
    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(engine_module.gemini_client, "analyze_text", _none)
    monkeypatch.setattr(engine_module.gemini_client, "analyze_image", _none)
    monkeypatch.setattr(engine_module.gemini_client, "analyze_audio", _none)
    monkeypatch.setattr(engine_module.hf_client, "detect_ai_text", _none)
    monkeypatch.setattr(engine_module.hf_client, "detect_fake_news", _none)
    monkeypatch.setattr(engine_module.hf_client, "detect_deepfake_image", _none)
    monkeypatch.setattr(engine_module.hf_client, "detect_deepfake_audio", _none)
    return DetectionEngine()


def _assert_valid_result(result: DetectionResult):
    assert isinstance(result, DetectionResult)
    assert 0 <= result.risk_score <= 100
    assert 0.0 <= result.confidence <= 1.0
    assert result.classification in ("safe", "suspicious", "malicious")
    assert result.explanation_fr
    assert result.explanation_en
    assert result.engine_used
    assert isinstance(result.raw_response, dict)
    assert "key_indicators" in result.raw_response


# ═══════════════════════════════════════════════════════════════
# Cascade falls back to heuristics without errors
# ═══════════════════════════════════════════════════════════════

class TestOfflineFallback:
    async def test_text_falls_back_to_stylometry(self, offline_engine):
        result = await offline_engine.analyze(
            content_type="text",
            content_text=(
                "Il convient de noter que cette annonce représente une avancée majeure. "
                "En conclusion, il est essentiel de souligner les bénéfices considérables "
                "pour la population. Par conséquent, les mesures appropriées seront prises."
            ),
            language="fr",
        )
        _assert_valid_result(result)
        assert result.engine_used == "stylometric_forensics_v2"
        assert result.raw_response["tier"] == "heuristic"
        assert "sub_scores" in result.raw_response
        assert "fake_news" in result.raw_response["sub_scores"]

    async def test_image_falls_back_to_forensics(self, offline_engine, tmp_path):
        from PIL import Image

        img_path = tmp_path / "sample.png"
        Image.new("RGB", (512, 512), (90, 100, 110)).save(img_path)

        result = await offline_engine.analyze(content_type="image", file_path=str(img_path))
        _assert_valid_result(result)
        assert result.engine_used == "image_forensics_v2"

    async def test_audio_falls_back_to_signal_analysis(self, offline_engine, tmp_path):
        t = np.linspace(0, 2.0, 32000, endpoint=False)
        samples = (0.5 * np.sin(2 * np.pi * 220 * t) * 32000).astype("<i2")
        audio_path = tmp_path / "sample.wav"
        with wave.open(str(audio_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(samples.tobytes())

        result = await offline_engine.analyze(content_type="audio", file_path=str(audio_path))
        _assert_valid_result(result)
        assert result.engine_used == "audio_forensics_v2"

    async def test_missing_image_file_returns_result_not_exception(self, offline_engine):
        result = await offline_engine.analyze(
            content_type="image", file_path="Z:/does/not/exist.png"
        )
        _assert_valid_result(result)
        assert result.engine_used == "file_error"
        assert result.confidence == 0.0

    async def test_unfetchable_video_url_returns_result(self, offline_engine, monkeypatch):
        async def _no_video(url):
            return None
        import app.services.media_fetch as media_fetch
        monkeypatch.setattr(media_fetch, "fetch_video", _no_video)

        result = await offline_engine.analyze(
            content_type="video", content_url="https://youtube.com/watch?v=xxxx"
        )
        _assert_valid_result(result)
        assert result.engine_used == "video_url_unfetchable"

    async def test_unknown_content_type_raises_value_error(self, offline_engine):
        with pytest.raises(ValueError):
            await offline_engine.analyze(content_type="hologram")


# ═══════════════════════════════════════════════════════════════
# Score → classification mapping
# ═══════════════════════════════════════════════════════════════

class TestScoring:
    def test_classification_thresholds(self):
        assert score_to_classification(0) == "safe"
        assert score_to_classification(29) == "safe"
        assert score_to_classification(30) == "suspicious"
        assert score_to_classification(69) == "suspicious"
        assert score_to_classification(70) == "malicious"
        assert score_to_classification(100) == "malicious"

    def test_probability_to_score_bounds(self):
        assert ai_probability_to_risk_score(-0.5) == 0
        assert ai_probability_to_risk_score(0.0) == 0
        assert ai_probability_to_risk_score(0.437) == 44
        assert ai_probability_to_risk_score(1.0) == 100
        assert ai_probability_to_risk_score(1.7) == 100


# ═══════════════════════════════════════════════════════════════
# Hugging Face label normalization (pure logic, no network)
# ═══════════════════════════════════════════════════════════════

class TestHFLabelNormalization:
    def test_fake_label_direct(self):
        prob = HuggingFaceClient.fake_probability_from_labels(
            [{"label": "Fake", "score": 0.83}, {"label": "Real", "score": 0.17}]
        )
        assert prob == pytest.approx(0.83)

    def test_nested_list_format(self):
        prob = HuggingFaceClient.fake_probability_from_labels(
            [[{"label": "ChatGPT", "score": 0.91}, {"label": "Human", "score": 0.09}]]
        )
        assert prob == pytest.approx(0.91)

    def test_real_label_only_inverted(self):
        prob = HuggingFaceClient.fake_probability_from_labels(
            [{"label": "Realism", "score": 0.75}]
        )
        assert prob == pytest.approx(0.25)

    def test_deepfake_label_variants(self):
        for label in ("Deepfake", "deepfake", "DEEP_FAKE", "AI-generated", "artificial"):
            prob = HuggingFaceClient.fake_probability_from_labels(
                [{"label": label, "score": 0.6}]
            )
            assert prob == pytest.approx(0.6), f"label {label!r} not mapped"

    def test_unknown_labels_return_none(self):
        assert HuggingFaceClient.fake_probability_from_labels(
            [{"label": "LABEL_0", "score": 0.9}]
        ) is None

    def test_error_dict_returns_none(self):
        assert HuggingFaceClient.fake_probability_from_labels(
            {"error": "Model too busy"}
        ) is None

    def test_empty_returns_none(self):
        assert HuggingFaceClient.fake_probability_from_labels([]) is None


# ═══════════════════════════════════════════════════════════════
# Tier-2 path: HF responds, Gemini down
# ═══════════════════════════════════════════════════════════════

class TestHuggingFaceTier:
    async def test_text_uses_hf_when_gemini_down(self, monkeypatch):
        async def _none(*args, **kwargs):
            return None

        async def _hf_ai(text):
            return 0.82, ["Hello-SimpleAI/chatgpt-detector-roberta"], {"Hello-SimpleAI/chatgpt-detector-roberta": 0.82}

        async def _hf_fake(text):
            return 0.65, ["hamzab/roberta-fake-news-classification"], {"hamzab/roberta-fake-news-classification": 0.65}

        monkeypatch.setattr(engine_module.gemini_client, "analyze_text", _none)
        monkeypatch.setattr(engine_module.hf_client, "detect_ai_text", _hf_ai)
        monkeypatch.setattr(engine_module.hf_client, "detect_fake_news", _hf_fake)

        result = await DetectionEngine().analyze(
            content_type="text",
            content_text="Some long enough piece of content to be analyzed properly here.",
        )
        _assert_valid_result(result)
        assert result.engine_used.startswith("huggingface/")
        assert result.raw_response["tier"] == "huggingface"
        assert result.raw_response["sub_scores"]["ai_text"] == pytest.approx(0.82)
        assert result.raw_response["sub_scores"]["fake_news"] == pytest.approx(0.65)
        assert result.risk_score >= 70  # 0.82 ai + 0.65 fake blends high

    async def test_image_uses_hf_when_gemini_down(self, monkeypatch, tmp_path):
        from PIL import Image

        async def _none(*args, **kwargs):
            return None

        async def _hf_img(image_bytes, mime):
            return 0.88, ["dima806/deepfake_vs_real_image_detection"], {"dima806/deepfake_vs_real_image_detection": 0.88}

        monkeypatch.setattr(engine_module.gemini_client, "analyze_image", _none)
        monkeypatch.setattr(engine_module.hf_client, "detect_deepfake_image", _hf_img)

        img_path = tmp_path / "df.jpg"
        Image.new("RGB", (300, 300), (10, 20, 30)).save(img_path)

        result = await DetectionEngine().analyze(content_type="image", file_path=str(img_path))
        _assert_valid_result(result)
        assert result.engine_used.startswith("huggingface/")
        assert result.risk_score == 88
        assert result.classification == "malicious"
