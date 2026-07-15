from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import jobs
from .schemas import CreateJobRequest, JobState

app = FastAPI(title="Persian Shorts", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/jobs", response_model=JobState)
def create_job(payload: CreateJobRequest) -> JobState:
    return jobs.create_job(str(payload.youtube_url), payload.max_clips)


@app.get("/api/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/clips/{clip_id}")
def download_clip(job_id: str, clip_id: str) -> FileResponse:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    clip = next((c for c in job.clips if c.id == clip_id), None)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(clip.file_path, media_type="video/mp4", filename=f"{clip_id}.mp4")


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}
