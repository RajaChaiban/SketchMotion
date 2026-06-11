"""Application settings, sourced from environment / .env (pydantic-settings)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM provider selection: auto | gemini | claude_cli | stub.
    # "auto" => gemini if a key is set, else the local claude CLI if present, else stub.
    llm_provider: str = "auto"

    # Gemini — empty key => not used unless explicitly selected.
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_spec_model: str = "gemini-2.5-flash"
    gemini_spec_model_fallback: str = "gemini-2.5-pro"

    # Claude Code CLI provider (dev-time spec compilation without an API key).
    claude_cli_path: str = "claude"
    claude_cli_model: str = "sonnet"
    claude_cli_timeout: int = 180

    # Infra
    redis_url: str = "redis://localhost:6379"
    output_dir: str = "outputs"

    # Limits
    max_prompt_chars: int = 2000
    max_upload_image_mb: int = 10
    max_upload_video_mb: int = 200
    max_video_seconds: int = 45        # cap stylization work per the overlay-mode design
    stylize_workers: int = 4           # parallel frame workers for video stylization

    @property
    def max_upload_video_bytes(self) -> int:
        return self.max_upload_video_mb * 1024 * 1024

    @property
    def max_upload_image_bytes(self) -> int:
        return self.max_upload_image_mb * 1024 * 1024

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
