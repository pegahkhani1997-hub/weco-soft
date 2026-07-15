from app.config import Settings
from app.pipeline.highlights import _clean
from app.schemas import Highlight


def _settings(**overrides):
    return Settings(min_clip_seconds=20, max_clip_seconds=90, **overrides)


def test_clean_drops_too_short_clips():
    settings = _settings()
    highlights = [Highlight(start=0, end=10, title="a", reason="r")]
    assert _clean(highlights, settings, video_duration=100) == []


def test_clean_truncates_too_long_clips():
    settings = _settings()
    highlights = [Highlight(start=0, end=200, title="a", reason="r")]
    result = _clean(highlights, settings, video_duration=300)
    assert len(result) == 1
    assert result[0].end - result[0].start == settings.max_clip_seconds


def test_clean_drops_overlapping_clips_keeping_earlier():
    settings = _settings()
    highlights = [
        Highlight(start=0, end=30, title="first", reason="r"),
        Highlight(start=20, end=50, title="overlaps", reason="r"),
        Highlight(start=100, end=130, title="no overlap", reason="r"),
    ]
    result = _clean(highlights, settings, video_duration=300)
    assert [h.title for h in result] == ["first", "no overlap"]


def test_clean_clamps_end_to_video_duration():
    settings = _settings()
    highlights = [Highlight(start=70, end=150, title="a", reason="r")]
    result = _clean(highlights, settings, video_duration=100)
    assert result[0].end == 100
    assert result[0].start == 70
