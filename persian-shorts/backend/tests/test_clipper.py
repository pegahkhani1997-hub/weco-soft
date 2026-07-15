from pathlib import Path

from app.config import Settings
from app.pipeline import clipper


def test_landscape_crop_centers_on_detected_face(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, output_width=1080, output_height=1920)
    monkeypatch.setattr(clipper, "_probe_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(clipper, "_detect_face_center_ratio", lambda *a, **k: 0.75)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            pass

        return R()

    monkeypatch.setattr(clipper.subprocess, "run", fake_run)

    out = clipper.make_vertical_clip(
        source_video=Path("in.mp4"), start=1.0, end=10.0, clip_id="abc", settings=settings
    )

    assert out == settings.clips_dir / "abc.mp4"
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "crop=608:1080:" in vf  # 1080*9/16 = 607.5, banker's rounding -> 608
    assert "scale=1080:1920" in vf


def test_center_crop_when_no_face_detected(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, output_width=1080, output_height=1920)
    monkeypatch.setattr(clipper, "_probe_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(clipper, "_detect_face_center_ratio", lambda *a, **k: 0.5)

    captured = {}
    monkeypatch.setattr(
        clipper.subprocess, "run", lambda cmd, **k: captured.setdefault("cmd", cmd)
    )

    clipper.make_vertical_clip(
        source_video=Path("in.mp4"), start=0.0, end=5.0, clip_id="center", settings=settings
    )

    crop_w = round(1080 * 1080 / 1920)
    max_x = 1920 - crop_w
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert f"crop={crop_w}:1080:{max_x // 2}:0" in vf
