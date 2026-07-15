# Persian Shorts

Paste a link to a long-form Persian/Farsi YouTube video and get back a handful
of short, engaging, vertical (9:16) clips — picked automatically by AI.

## How it works

1. **Download** — `yt-dlp` downloads the source video.
2. **Transcribe** — the audio is sent to OpenAI's Whisper API with
   `language="fa"` to get a timestamped Persian transcript. Long videos are
   chunked (10 min pieces) to stay under Whisper's upload size limit.
3. **Find highlights** — the transcript is sent to Claude, which picks the
   most engaging, self-contained moments (funny, surprising, quotable) and
   returns start/end timestamps, a title, and a reason for each.
4. **Clip + reframe** — each highlight is cut with `ffmpeg` and reframed to
   vertical 9:16. A Haar-cascade face detector samples a few frames per clip
   to keep detected faces in the crop; falls back to a center crop otherwise.

A job runs in the background and the frontend polls for progress until the
clips are ready to preview and download.

**Note on YouTube content:** only use this on videos you have the rights to
repurpose (your own channel, or content whose license/terms allow it) —
downloading and re-editing someone else's video may violate YouTube's Terms
of Service or copyright law.

## Project layout

```
persian-shorts/
  backend/    FastAPI app + the download/transcribe/highlight/clip pipeline
  frontend/   React + Vite UI (RTL, Persian)
```

## Backend setup

Requires Python 3.10+, and `ffmpeg`/`ffprobe` on your `PATH`.

```bash
cd persian-shorts/backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env: set OPENAI_API_KEY and ANTHROPIC_API_KEY

uvicorn app.main:app --reload --port 8000
```

Run the test suite (fully mocked — no API keys, ffmpeg, or network needed):

```bash
pytest
```

## Frontend setup

Requires Node 20+.

```bash
cd persian-shorts/frontend
npm install
npm run dev
```

Open the printed URL (defaults to http://localhost:5173). The dev server
proxies `/api/*` requests to the backend at `http://localhost:8000`.

## Configuration

Backend settings (env vars, see `.env.example` plus these optional ones with
defaults) live in `app/config.py`:

- `WHISPER_MODEL` (default `whisper-1`)
- `CLAUDE_MODEL` (default `claude-sonnet-5`)
- `MAX_CLIPS` (default `5`) — max highlight clips per video
- `MIN_CLIP_SECONDS` / `MAX_CLIP_SECONDS` (default `20` / `90`)
- `OUTPUT_WIDTH` / `OUTPUT_HEIGHT` (default `1080` / `1920`)
- `DATA_DIR` (default `data/`) — where downloads, clips, and job state live

## Limitations / next steps

- No persistent job store (in-memory only) — jobs are lost on backend
  restart. Fine for single-user local use; would need a database/queue
  (e.g. Redis + Celery) for multi-user or production deployment.
- No burned-in captions yet — clips are silent-caption-free; the transcript
  is available per job if you want to add subtitle rendering later.
- Face-detection reframing uses a fast Haar cascade (good enough for
  head-on faces); a proper face/saliency tracking model would handle motion
  and side profiles better.
