import time
from pathlib import Path

from app import jobs
from app.pipeline import clipper, downloader, highlights, transcribe
from app.schemas import Highlight, JobStatus, TranscriptSegment


def test_run_pipeline_end_to_end_with_mocked_stages(monkeypatch, tmp_path):
    fake_video = downloader.DownloadedVideo(
        video_path=tmp_path / "vid.mp4",
        audio_path=tmp_path / "vid.m4a",
        title="Test Video",
        duration=120.0,
    )
    monkeypatch.setattr(downloader, "download", lambda url, out_dir: fake_video)

    fake_transcript = [
        TranscriptSegment(start=0.0, end=5.0, text="سلام"),
        TranscriptSegment(start=5.0, end=10.0, text="این یک تست است"),
    ]
    monkeypatch.setattr(transcribe, "transcribe_persian", lambda path, settings: fake_transcript)

    fake_highlights = [
        Highlight(start=0.0, end=25.0, title="بخش جالب", reason="چون جالب است"),
    ]
    monkeypatch.setattr(
        highlights,
        "pick_highlights",
        lambda transcript, settings, n_clips, video_duration: fake_highlights,
    )

    def fake_make_vertical_clip(source_video, start, end, clip_id, settings):
        out = settings.clips_dir / f"{clip_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-mp4")
        return out

    monkeypatch.setattr(clipper, "make_vertical_clip", fake_make_vertical_clip)

    job = jobs.create_job("https://youtube.com/watch?v=abc", max_clips=1)

    for _ in range(100):
        current = jobs.get_job(job.id)
        if current.status in (JobStatus.DONE, JobStatus.FAILED):
            break
        time.sleep(0.05)

    final = jobs.get_job(job.id)
    assert final.status == JobStatus.DONE, final.error
    assert final.source_title == "Test Video"
    assert len(final.clips) == 1
    assert final.clips[0].title == "بخش جالب"
    assert Path(final.clips[0].file_path).read_bytes() == b"fake-mp4"
