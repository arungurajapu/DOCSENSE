"""Application configuration loaded via Pydantic v2 BaseSettings."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SPECIAL_TOKEN_RESERVE = 2  # [CLS] and [SEP] added by all-MiniLM-L6-v2 WordPiece tokenizer
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


def _resolve_backend_path(value: str) -> str:
    """Normalize relative filesystem paths against canonical BACKEND_DIR."""
    path = Path(value)
    if path.is_absolute() or value.startswith(("/", "\\")):
        return str(path)
    return str((BACKEND_DIR / path).resolve())


def _resolve_database_url(value: str) -> str:
    """Normalize relative file-backed SQLite URLs against canonical BACKEND_DIR."""
    stripped = value.strip()
    if not stripped.lower().startswith("sqlite:///"):
        return stripped
    raw_target = stripped[len("sqlite:///") :]
    path_part, sep, query_part = raw_target.partition("?")
    if (
        not path_part
        or path_part == ":memory:"
        or path_part.startswith("file:")
        or "mode=memory" in query_part.lower()
        or path_part.startswith(("/", "\\"))
        or Path(path_part).is_absolute()
    ):
        return stripped
    resolved = (BACKEND_DIR / path_part).resolve()
    return f"sqlite:///{resolved.as_posix()}{sep}{query_part}"


class Settings(BaseSettings):
    """Typed runtime settings with CWD-independent paths and chunk/token safety validation."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
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

    @field_validator("CHROMA_PERSIST_DIR", "UPLOAD_DIR", mode="after")
    @classmethod
    def _normalize_data_dirs(cls, value: str) -> str:
        return _resolve_backend_path(value)

    @field_validator("DATABASE_URL", mode="after")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return _resolve_database_url(value)

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
