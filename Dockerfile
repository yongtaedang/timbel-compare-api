# timbel-compare-api — FastAPI + 원본 비교 스크립트.
#
# 빌드 단계가 따로 없다(컴파일할 게 없다). 의존성 설치 레이어와 소스 레이어만 나눠서
# requirements.txt가 그대로면 pip 설치를 캐시로 넘긴다.
FROM python:3.12-slim

# 비교는 CPU 바운드 멀티프로세스 작업이다 — 파이썬이 .pyc를 쓰느라 시간을 버리지 않게 하고,
# 로그는 버퍼링 없이 바로 흘린다(컨테이너 로그가 실시간으로 보여야 진행 상황을 안다).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# 비루트로 돌린다. 업로드/결과는 /tmp 아래 임시 폴더에 쓰고 응답 직후 지운다(runner.CompareJob).
RUN groupadd -r -g 1001 app \
 && useradd -r -m -d /home/app -g app -u 1001 -s /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8080

# slim 이미지엔 curl/wget이 없다 — 표준 라이브러리로 확인한다(추가 패키지 0).
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/compare/v1/healthz', timeout=3).status == 200 else 1)"

# 워커는 1개다. 요청 하나가 이미 멀티프로세스로 코어를 나눠 쓰므로(스크립트의 -w), uvicorn까지
# 늘리면 서로 CPU를 뺏는다. 동시 실행 수는 앱이 MAX_CONCURRENT_JOBS로 제어한다.
CMD ["uvicorn", "compare_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--timeout-keep-alive", "120"]
