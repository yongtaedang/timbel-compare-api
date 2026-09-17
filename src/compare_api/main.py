"""timbel-compare-api — 텍스트 비교 스크립트를 HTTP로 감싼 서버.

엔드포인트는 둘뿐이다:
  GET  {prefix}/healthz  — 상태 확인(컨테이너 HEALTHCHECK가 쓴다)
  POST {prefix}/compare  — 텍스트 폴더 두 벌을 받아 비교하고 결과 폴더를 zip으로 돌려준다

성공하면 응답 본문이 zip 바이너리고, 실패하면 {"code","message"} JSON이다. 프론트는 이
구분만 알면 된다(Content-Type이 application/zip인지 보면 된다).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from .config import settings
from .runner import CompareError, CompareRequest, create_job, run_engine, save_uploads, zip_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("compare_api")

app = FastAPI(
    title="timbel-compare-api",
    description="텍스트 비교(CER) 스크립트를 실행하고 결과 폴더를 zip으로 돌려주는 서버",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# 동시에 도는 비교 작업 수 제한 — 하나가 코어를 다 쓰므로 무제한으로 받으면 서버가 눕는다.
_slots = asyncio.Semaphore(settings.max_concurrent_jobs)


@app.exception_handler(CompareError)
async def compare_error_handler(_request, exc: CompareError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"code": str(exc.status), "message": exc.message})


@app.get(settings.api_prefix + "/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "UP"}


@app.post(settings.api_prefix + "/compare")
async def compare(
    reference: list[UploadFile] = File(..., description="참조 텍스트 .txt 파일들"),
    recognition: list[UploadFile] = File(..., description="인식 텍스트 .txt 파일들"),
    workers: int | None = Form(None, description="워커 수(-w). 비우면 스크립트 기본값=서버 코어 수"),
    eli_gantu: bool = Form(False, description="단독 감탄사(이/그/저/뭐/어) 제거(--eli-gantu)"),
) -> FileResponse:
    """폴더 두 벌을 비교하고 결과 zip을 돌려준다.

    파일명이 같은 .txt끼리 짝이 된다(원본 스크립트 규칙). 짝이 없는 파일은 그냥 무시된다.
    """
    # 자리가 없으면 기다리게 두지 않고 바로 거절한다(업로드까지 끝난 요청을 몇 분씩 붙잡아
    # 두면 프록시 타임아웃으로 끊긴다). 동시 요청이 겹치면 한 칸 정도는 초과할 수 있다.
    if _slots.locked():
        raise CompareError(429, "비교 작업이 이미 가득 찼습니다. 잠시 뒤 다시 시도해 주세요.")

    async with _slots:
        job = create_job()
        try:
            reference_files = [(f.filename or "", await f.read()) for f in reference]
            recognition_files = [(f.filename or "", await f.read()) for f in recognition]

            total = save_uploads(reference_files, job.reference, label="참조")
            total += save_uploads(recognition_files, job.recognition, label="인식")
            if total > settings.max_total_bytes:
                limit_mb = settings.max_total_bytes // 1024 // 1024
                raise CompareError(413, f"업로드 전체 크기가 너무 큽니다(최대 {limit_mb}MB).")

            request = CompareRequest(workers=workers, eli_gantu=eli_gantu)
            logger.info(
                "비교 시작 — 참조 %d개, 인식 %d개, %.1fMB, workers=%s, eli_gantu=%s",
                len(reference_files),
                len(recognition_files),
                total / 1024 / 1024,
                workers or "기본값",
                eli_gantu,
            )

            # 블로킹 subprocess는 스레드로 내보낸다 — 이벤트 루프가 멈추면 healthz도 안 뜬다.
            log = await asyncio.to_thread(run_engine, job, request)
            logger.info("비교 완료\n%s", log)

            zip_directory(job.output, job.archive)
        except Exception:
            job.cleanup()
            raise

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return FileResponse(
            job.archive,
            media_type="application/zip",
            filename=f"compare-result-{stamp}.zip",
            background=BackgroundTask(job.cleanup),
        )
