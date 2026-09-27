"""Local storage and independent, explicit model opt-ins."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def data_home():
    return (
        Path(os.getenv("OBSI_DATA_DIR", "~/Library/Application Support/obsi-onto"))
        .expanduser()
        .resolve()
    )


@dataclass
class Config:
    data_dir: Path = field(default_factory=data_home)
    debounce: float = 2.0
    reconcile_seconds: float = 1800.0
    embedding_provider: str = field(default_factory=lambda: os.getenv("OBSI_EMBEDDING", "local"))
    embedding_model: str = field(default_factory=lambda: os.getenv("OBSI_EMBED_MODEL", LOCAL_MODEL))
    embedding_dim: int = field(default_factory=lambda: int(os.getenv("OBSI_EMBED_DIM", "384")))
    embedding_url: str = field(default_factory=lambda: os.getenv("OBSI_EMBED_URL", ""))
    external_embedding: bool = field(
        default_factory=lambda: os.getenv("OBSI_ALLOW_EXTERNAL_EMBEDDING") == "1"
    )
    generation_url: str = field(default_factory=lambda: os.getenv("OBSI_LLM_URL", ""))
    generation_model: str = field(default_factory=lambda: os.getenv("OBSI_LLM_MODEL", ""))
    external_generation: bool = field(
        default_factory=lambda: os.getenv("OBSI_ALLOW_EXTERNAL_GENERATION") == "1"
    )
    external_suggestions: bool = field(
        default_factory=lambda: os.getenv("OBSI_ALLOW_EXTERNAL_SUGGESTIONS") == "1"
    )

    def validate_storage(self, vault: Path | None = None):
        cloud_parts = {"Mobile Documents", "CloudStorage", "com~apple~CloudDocs"}
        if cloud_parts.intersection(self.data_dir.parts):
            raise ValueError("색인 폴더는 iCloud·클라우드 동기화 폴더 밖이어야 합니다.")
        if vault and self.data_dir.is_relative_to(vault):
            raise ValueError("색인 폴더는 볼트 밖이어야 합니다.")
