from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Where downloads, transcripts and rendered clips are written.
    data_dir: Path = Path("data")

    # Whisper model used for Persian transcription.
    whisper_model: str = "whisper-1"

    # Claude model used to pick highlight segments.
    claude_model: str = "claude-sonnet-5"

    # How many highlight clips to try to produce per source video.
    max_clips: int = 5
    min_clip_seconds: int = 20
    max_clip_seconds: int = 90

    # Output vertical resolution (9:16).
    output_width: int = 1080
    output_height: int = 1920

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for d in (settings.downloads_dir, settings.clips_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return settings
