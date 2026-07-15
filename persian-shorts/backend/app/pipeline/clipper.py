from __future__ import annotations

import subprocess
from pathlib import Path

import cv2

from ..config import Settings

_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_SAMPLE_COUNT = 5


def make_vertical_clip(
    source_video: Path,
    start: float,
    end: float,
    clip_id: str,
    settings: Settings,
) -> Path:
    """Cut [start, end] from source_video and reframe it to a 9:16 vertical
    clip, keeping detected faces in frame where possible."""
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.clips_dir / f"{clip_id}.mp4"

    src_w, src_h = _probe_resolution(source_video)
    crop_x_ratio = _detect_face_center_ratio(source_video, start, end)

    target_ratio = settings.output_width / settings.output_height  # 9/16
    if src_w / src_h > target_ratio:
        # Source is wider than 9:16: crop width, keep full height.
        crop_h = src_h
        crop_w = int(round(src_h * target_ratio))
    else:
        # Source is taller/narrower than 9:16: crop height, keep full width.
        crop_w = src_w
        crop_h = int(round(src_w / target_ratio))

    max_x = max(src_w - crop_w, 0)
    desired_center_px = crop_x_ratio * src_w
    crop_x = int(round(desired_center_px - crop_w / 2))
    crop_x = min(max(crop_x, 0), max_x)
    max_y = max(src_h - crop_h, 0)
    crop_y = max_y // 2

    vf = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={settings.output_width}:{settings.output_height}"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(source_video),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def _probe_resolution(video_path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def _detect_face_center_ratio(video_path: Path, start: float, end: float) -> float:
    """Sample a few frames between start/end and return the average detected
    face center as a ratio of frame width (0=left edge, 1=right edge).

    Falls back to 0.5 (center crop) if no faces are detected.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.5

    ratios: list[float] = []
    duration = max(end - start, 0.1)
    for i in range(_SAMPLE_COUNT):
        t = start + duration * (i + 0.5) / _SAMPLE_COUNT
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(faces) == 0:
            continue
        frame_w = frame.shape[1]
        for x, y, w, h in faces:
            ratios.append((x + w / 2) / frame_w)

    cap.release()
    return sum(ratios) / len(ratios) if ratios else 0.5
