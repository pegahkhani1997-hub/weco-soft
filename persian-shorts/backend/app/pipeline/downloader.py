from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yt_dlp


@dataclass
class DownloadedVideo:
    video_path: Path
    audio_path: Path
    title: str
    duration: float


def download(youtube_url: str, downloads_dir: Path) -> DownloadedVideo:
    """Download a YouTube video (best mp4 video+audio) and extract its audio track.

    Only download videos you have the right to use — respect the source
    channel's terms and copyright when repurposing clips.
    """
    downloads_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(downloads_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        video_path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")

    audio_path = video_path.with_suffix(".m4a")
    _extract_audio(video_path, audio_path)

    return DownloadedVideo(
        video_path=video_path,
        audio_path=audio_path,
        title=info.get("title", video_path.stem),
        duration=float(info.get("duration") or 0.0),
    )


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "copy",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )
