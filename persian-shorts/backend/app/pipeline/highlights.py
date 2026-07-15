from __future__ import annotations

from anthropic import Anthropic

from ..config import Settings
from ..schemas import Highlight, TranscriptSegment

_TOOL_NAME = "return_highlights"

_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": "Return the chosen highlight segments for the short-form clips.",
    "input_schema": {
        "type": "object",
        "properties": {
            "highlights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number", "description": "Start time in seconds"},
                        "end": {"type": "number", "description": "End time in seconds"},
                        "title": {
                            "type": "string",
                            "description": "Short, catchy Persian title/hook for this clip",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One sentence (Persian or English) on why this moment is engaging",
                        },
                    },
                    "required": ["start", "end", "title", "reason"],
                },
            }
        },
        "required": ["highlights"],
    },
}


def pick_highlights(
    transcript: list[TranscriptSegment],
    settings: Settings,
    n_clips: int,
    video_duration: float,
) -> list[Highlight]:
    if not transcript:
        return []

    client = Anthropic(api_key=settings.anthropic_api_key)
    transcript_text = "\n".join(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}" for seg in transcript)

    prompt = f"""You are editing a long-form Persian (Farsi) YouTube video down to short, \
engaging vertical clips (like Instagram Reels / YouTube Shorts / TikTok).

The full transcript below has timestamps in seconds. The source video is about \
{video_duration:.0f} seconds long.

Pick up to {n_clips} of the MOST engaging, self-contained moments — funny, \
surprising, emotional, controversial, or highly quotable. Each clip must:
- be between {settings.min_clip_seconds} and {settings.max_clip_seconds} seconds long
- start and end at natural sentence/thought boundaries from the transcript
- not overlap with any other chosen clip
- make sense on its own without the rest of the video for context

Transcript:
{transcript_text}

Call the {_TOOL_NAME} tool with your selections, ordered from most to least engaging."""

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    highlights: list[Highlight] = []
    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            for item in block.input.get("highlights", []):
                highlights.append(Highlight(**item))

    return _clean(highlights, settings, video_duration)[:n_clips]


def _clean(
    highlights: list[Highlight], settings: Settings, video_duration: float
) -> list[Highlight]:
    cleaned: list[Highlight] = []
    for hl in sorted(highlights, key=lambda h: h.start):
        start = max(0.0, hl.start)
        end = min(video_duration or hl.end, hl.end)
        length = end - start
        if length < settings.min_clip_seconds:
            continue
        if length > settings.max_clip_seconds:
            end = start + settings.max_clip_seconds
        if cleaned and start < cleaned[-1].end:
            continue  # overlaps the previous kept clip
        cleaned.append(Highlight(start=start, end=end, title=hl.title, reason=hl.reason))
    return cleaned
