from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .config import get_settings
from .pipeline import clipper, downloader, highlights as highlights_module, transcribe
from .schemas import Clip, JobState, JobStatus

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_lock = Lock()
_jobs: dict[str, JobState] = {}


def create_job(youtube_url: str, max_clips: int | None) -> JobState:
    job = JobState(id=uuid.uuid4().hex[:12], youtube_url=youtube_url)
    with _lock:
        _jobs[job.id] = job
    _executor.submit(_run_pipeline, job.id, max_clips)
    return job


def get_job(job_id: str) -> JobState | None:
    with _lock:
        return _jobs.get(job_id)


def _update(job_id: str, **fields) -> None:
    with _lock:
        job = _jobs[job_id]
        for key, value in fields.items():
            setattr(job, key, value)


def _run_pipeline(job_id: str, max_clips: int | None) -> None:
    settings = get_settings()
    job = get_job(job_id)
    assert job is not None
    n_clips = max_clips or settings.max_clips

    try:
        _update(job_id, status=JobStatus.DOWNLOADING, progress=0.05, message="Downloading video...")
        video = downloader.download(job.youtube_url, settings.downloads_dir)
        _update(job_id, source_title=video.title, progress=0.25)

        _update(job_id, status=JobStatus.TRANSCRIBING, progress=0.3, message="Transcribing Persian audio...")
        transcript = transcribe.transcribe_persian(video.audio_path, settings)
        _update(job_id, transcript=transcript, progress=0.5)

        _update(
            job_id,
            status=JobStatus.FINDING_HIGHLIGHTS,
            progress=0.55,
            message="Finding the most engaging moments...",
        )
        picked = highlights_module.pick_highlights(
            transcript=transcript,
            settings=settings,
            n_clips=n_clips,
            video_duration=video.duration,
        )
        _update(job_id, highlights=picked, progress=0.65)

        _update(job_id, status=JobStatus.CLIPPING, progress=0.7, message="Cutting and reframing clips...")
        clips: list[Clip] = []
        for i, hl in enumerate(picked):
            clip_id = f"{job_id}-{i}"
            out_path = clipper.make_vertical_clip(
                source_video=video.video_path,
                start=hl.start,
                end=hl.end,
                clip_id=clip_id,
                settings=settings,
            )
            clips.append(
                Clip(
                    id=clip_id,
                    title=hl.title,
                    reason=hl.reason,
                    start=hl.start,
                    end=hl.end,
                    file_path=str(out_path),
                    download_url=f"/api/jobs/{job_id}/clips/{clip_id}",
                )
            )
            _update(job_id, clips=clips, progress=0.7 + 0.28 * (i + 1) / max(len(picked), 1))

        _update(job_id, status=JobStatus.DONE, progress=1.0, message="Done", clips=clips)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job_id)
        _update(
            job_id,
            status=JobStatus.FAILED,
            error=str(exc),
            message=f"Failed: {exc}",
        )
        logger.debug(traceback.format_exc())
