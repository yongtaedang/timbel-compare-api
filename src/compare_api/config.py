"""환경 설정. 전부 환경 변수로 덮어쓸 수 있고, 기본값만으로도 로컬에서 그냥 뜬다.

값의 의미와 기본값은 .env.example에 같은 순서로 적어 뒀다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENGINE_PATH = Path(__file__).parent / "engine" / "text_comparison_linux_007.py"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _origins() -> list[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    """요청 하나가 넘을 수 없는 선들. 이 서버는 요청마다 CPU를 다 쓰는 배치를 돌린다."""

    api_prefix: str = os.getenv("API_PREFIX", "/api/compare/v1")
    allowed_origins: tuple[str, ...] = tuple(_origins())

    max_files: int = _int("MAX_FILES", 2000)
    """참조·인식 각각의 파일 개수 상한."""

    max_file_bytes: int = _int("MAX_FILE_MB", 10) * 1024 * 1024
    """파일 하나의 크기 상한. 원본 스크립트가 10MB를 넘는 파일을 건너뛰므로 같은 값이 기본이다."""

    max_total_bytes: int = _int("MAX_TOTAL_MB", 512) * 1024 * 1024
    """업로드 전체 크기 상한."""

    max_workers: int = _int("MAX_WORKERS", os.cpu_count() or 4)
    """-w로 넘길 수 있는 최대 워커 수. 요청 하나가 서버 코어를 전부 먹는 걸 막는다."""

    timeout_seconds: int = _int("TIMEOUT_SECONDS", 1800)
    """비교 프로세스를 기다리는 한계. 넘으면 죽이고 504를 돌려준다."""

    max_concurrent_jobs: int = _int("MAX_CONCURRENT_JOBS", 2)
    """동시에 돌릴 수 있는 비교 작업 수. 넘치면 429로 거절한다."""


settings = Settings()
