"""업로드를 받아 원본 스크립트를 그대로 실행하고 결과 폴더를 zip으로 묶는다.

이 서버는 비교 로직을 **하나도 다시 구현하지 않는다**. `engine/text_comparison_linux_007.py`를
별도 프로세스로 띄워 CLI 인자를 그대로 넘기고, 스크립트가 만든 출력 폴더(summary.html,
summary.csv, <파일명>.html)를 통째로 압축할 뿐이다 — 수치가 달라질 여지를 없애려는 구조다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from .config import ENGINE_PATH, settings


class CompareError(Exception):
    """요청이 잘못됐거나 비교가 실패했다. status는 그대로 HTTP 상태 코드로 나간다."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class CompareRequest:
    """CLI 인자에 1:1로 대응하는 실행 옵션."""

    workers: int | None = None
    eli_gantu: bool = False


def safe_name(raw: str) -> str:
    """업로드 파일명을 폴더 안의 단순 파일명으로 좁힌다.

    브라우저가 폴더를 통째로 올리면 이름에 경로가 섞여 온다(webkitRelativePath). 경로 구분자를
    자르고 basename만 쓰며, 상위 경로나 숨은 이름은 거절한다.
    """
    name = raw.replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."} or name.startswith("."):
        raise CompareError(400, f"허용되지 않는 파일명입니다: {raw!r}")
    if not name.lower().endswith(".txt"):
        raise CompareError(400, f".txt 파일만 비교합니다: {name!r}")
    return name


def save_uploads(files: list[tuple[str, bytes]], dest: Path, *, label: str) -> int:
    """(파일명, 내용) 목록을 dest 폴더에 쓴다. 저장한 총 바이트를 돌려준다."""
    if not files:
        raise CompareError(400, f"{label} 텍스트 파일이 없습니다.")
    if len(files) > settings.max_files:
        raise CompareError(413, f"{label} 파일이 너무 많습니다(최대 {settings.max_files}개).")

    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for raw_name, content in files:
        if len(content) > settings.max_file_bytes:
            limit_mb = settings.max_file_bytes // 1024 // 1024
            raise CompareError(413, f"파일이 너무 큽니다(최대 {limit_mb}MB): {raw_name}")
        total += len(content)
        (dest / safe_name(raw_name)).write_bytes(content)
    return total


def build_command(reference: Path, recognition: Path, output: Path, request: CompareRequest) -> list[str]:
    """원본 스크립트에 넘길 인자.

    원본의 `--remove-punctuation`과 `--ignore-case`는 argparse에서 `store_true`인데 default가
    이미 True다 — 즉 **끌 방법이 없고 항상 켜져 있다**. 그래서 여기서도 넘기지 않는다(넘겨도
    같다). 끄고 싶다면 스크립트에 `--no-...` 플래그를 추가해야 한다(README "옵션" 절).
    """
    command = [
        sys.executable,
        str(ENGINE_PATH),
        "-A",
        str(reference),
        "-B",
        str(recognition),
        "-o",
        str(output),
    ]
    if request.workers:
        workers = max(1, min(request.workers, settings.max_workers))
        command += ["-w", str(workers)]
    if request.eli_gantu:
        command.append("--eli-gantu")
    return command


def zip_directory(source: Path, archive: Path) -> Path:
    """결과 폴더를 통째로 압축한다. 폴더 구조 그대로 들어간다(summary.html이 최상위)."""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source))
    return archive


@dataclass
class CompareJob:
    """작업 하나가 쓰는 임시 공간. 응답을 보낸 뒤 cleanup()으로 통째로 지운다."""

    root: Path

    @property
    def reference(self) -> Path:
        return self.root / "reference"

    @property
    def recognition(self) -> Path:
        return self.root / "recognition"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def archive(self) -> Path:
        return self.root / "result.zip"

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def create_job() -> CompareJob:
    return CompareJob(Path(mkdtemp(prefix="timbel-compare-")))


def run_engine(job: CompareJob, request: CompareRequest) -> str:
    """스크립트를 돌리고 stdout+stderr 로그를 돌려준다. 실패하면 CompareError."""
    job.output.mkdir(parents=True, exist_ok=True)
    command = build_command(job.reference, job.recognition, job.output, request)

    try:
        completed = subprocess.run(  # noqa: S603 - 인자는 전부 이 모듈이 만든 경로다
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompareError(504, f"비교가 {settings.timeout_seconds}초 안에 끝나지 않았습니다.") from exc

    log = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode != 0:
        # 원본은 짝이 맞는 파일이 없을 때도 exit 1로 끝난다 — 사유를 그대로 올려 보낸다.
        raise CompareError(422, f"비교 실패 (exit {completed.returncode})\n{log}")
    if not any(job.output.iterdir()):
        raise CompareError(422, f"결과 파일이 만들어지지 않았습니다.\n{log}")
    return log
