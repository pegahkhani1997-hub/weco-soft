from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI

from ..config import Settings
from ..schemas import TranscriptSegment

# Whisper's API caps uploads at 25MB; split into chunks comfortably under
# that so a long-form podcast/interview doesn't get rejected outright.
_MAX_CHUNK_SECONDS = 600  # 10 minutes


def transcribe_persian(audio_path: Path, settings: Settings) -> list[TranscriptSegment]:
    client = OpenAI(api_key=settings.openai_api_key)
    duration = _probe_duration(audio_path)

    segments: list[TranscriptSegment] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for chunk_path, offset in _split_audio(audio_path, duration, Path(tmpdir)):
            with open(chunk_path, "rb") as fh:
                result = client.audio.transcriptions.create(
                    model=settings.whisper_model,
                    file=fh,
                    language="fa",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            for seg in _segments_from_response(result):
                segments.append(
                    TranscriptSegment(
                        start=seg["start"] + offset,
                        end=seg["end"] + offset,
                        text=seg["text"].strip(),
                    )
                )
    return segments


def _segments_from_response(result) -> list[dict]:
    # The SDK returns a pydantic-like object; normalize to plain dicts.
    data = result.model_dump() if hasattr(result, "model_dump") else json.loads(result)
    return data.get("segments", [])


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _split_audio(
    audio_path: Path, duration: float, tmpdir: Path
) -> list[tuple[Path, float]]:
    if duration <= _MAX_CHUNK_SECONDS:
        return [(audio_path, 0.0)]

    chunks: list[tuple[Path, float]] = []
    offset = 0.0
    i = 0
    while offset < duration:
        chunk_path = tmpdir / f"chunk_{i}.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(offset),
                "-t",
                str(_MAX_CHUNK_SECONDS),
                "-i",
                str(audio_path),
                "-acodec",
                "copy",
                str(chunk_path),
            ],
            check=True,
            capture_output=True,
        )
        chunks.append((chunk_path, offset))
        offset += _MAX_CHUNK_SECONDS
        i += 1
    return chunks
