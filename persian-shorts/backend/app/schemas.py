from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    FINDING_HIGHLIGHTS = "finding_highlights"
    CLIPPING = "clipping"
    DONE = "done"
    FAILED = "failed"


class CreateJobRequest(BaseModel):
    youtube_url: HttpUrl
    max_clips: int | None = Field(default=None, ge=1, le=10)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Highlight(BaseModel):
    start: float
    end: float
    title: str
    reason: str


class Clip(BaseModel):
    id: str
    title: str
    reason: str
    start: float
    end: float
    file_path: str
    download_url: str


class JobState(BaseModel):
    id: str
    youtube_url: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    source_title: str | None = None
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
