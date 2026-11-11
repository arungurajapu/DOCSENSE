"""Application configuration loaded via Pydantic v2 BaseSettings."""

import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SPECIAL_TOKEN_RESERVE = 2  # [CLS] and [SEP] added by all-MiniLM-L6-v2 WordPiece tokenizer


class Settings(BaseSettings):
    """Typed runtime settings with chunk/token budget safety validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "DocSense"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "sqlite:///./data/docsense.db"
    CHROMA_PERSIST_DIR: str = "./data/chroma"
    CHROMA_COLLECTION_NAME: str = "docsense_chunks"
    UPLOAD_DIR: str = "./data/uploads"

    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHUNK_SIZE_TOKENS: int = Field(default=200, gt=0)
    CHUNK_OVERLAP_TOKENS: int = Field(default=40, ge=0)
    MODEL_MAX_SEQ_LENGTH: int = Field(default=256, gt=SPECIAL_TOKEN_RESERVE)
    MAX_UPLOAD_SIZE_MB: int = Field(default=25, gt=0)

    CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON value must be an array of strings.")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        raise ValueError("CORS_ORIGINS must be a list of origins or comma-separated/JSON string.")

    @model_validator(mode="after")
    def _validate_chunk_token_bounds(self) -> "Settings":
        if not (0 <= self.CHUNK_OVERLAP_TOKENS < self.CHUNK_SIZE_TOKENS):
            raise ValueError(
                "CHUNK_OVERLAP_TOKENS must satisfy 0 <= CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS "
                f"(got overlap={self.CHUNK_OVERLAP_TOKENS}, size={self.CHUNK_SIZE_TOKENS})."
            )
        if self.CHUNK_SIZE_TOKENS + SPECIAL_TOKEN_RESERVE > self.MODEL_MAX_SEQ_LENGTH:
            raise ValueError(
                "CHUNK_SIZE_TOKENS + 2 ([CLS]/[SEP] special tokens) must not exceed "
                f"MODEL_MAX_SEQ_LENGTH (got {self.CHUNK_SIZE_TOKENS} + {SPECIAL_TOKEN_RESERVE} "
                f"> {self.MODEL_MAX_SEQ_LENGTH})."
            )
        return self

    @property
    def max_upload_size_bytes(self) -> int:
        """Maximum allowed upload payload size in bytes."""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
