# timbel-compare-api

텍스트 비교(CER) 스크립트를 HTTP로 감싼 API 서버. FastAPI 0.121 / Python 3.12.

이 서버는 **비교 로직을 다시 구현하지 않는다.** 원본 스크립트
[`src/compare_api/engine/text_comparison_linux_007.py`](src/compare_api/engine/text_comparison_linux_007.py)를
별도 프로세스로 실행하고, 스크립트가 만든 결과 폴더를 zip으로 묶어 돌려줄 뿐이다. 이식이 없으니
수치가 달라질 여지도 없다 — 프론트([`timbel-compare-front`](../timbel-compare-front), 별도 레포)는
파일과 옵션만 올리고 zip을 받는다.

## 하는 일

```
브라우저(front) ──multipart──▶ /compare ──▶ 임시폴더에 .txt 저장
                                            └─▶ subprocess: text_comparison_linux_007.py -A .. -B .. -o ..
                                                            └─▶ summary.html · summary.csv · <파일명>.html
                              ◀──zip────────── 결과 폴더 압축 → 응답 후 임시폴더 삭제
```

## 엔드포인트

베이스 경로는 `API_PREFIX`(기본 `/api/compare/v1`)다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/healthz` | `{"status":"UP"}`. 컨테이너 HEALTHCHECK가 쓴다 |
| POST | `/compare` | 텍스트 폴더 두 벌을 비교하고 결과 zip을 돌려준다 |

### POST /compare

`multipart/form-data`:

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `reference` | 파일(여러 개) | 필수 | 참조 텍스트 `.txt` |
| `recognition` | 파일(여러 개) | 필수 | 인식 텍스트 `.txt` |
| `workers` | 정수 | 서버 코어 수 | 스크립트의 `-w`. `MAX_WORKERS`를 넘으면 상한으로 줄어든다 |
| `eli_gantu` | 불리언 | `false` | 스크립트의 `--eli-gantu` |

파일명이 같은 것끼리 짝이 된다. 짝이 없는 파일은 무시된다(원본 스크립트 규칙).

성공하면 **본문이 zip 바이너리**다(`Content-Type: application/zip`,
`Content-Disposition: attachment; filename="compare-result-YYYYmmdd-HHMMSS.zip"`). 압축을 풀면
원본 CLI의 출력 폴더가 그대로 나온다:

```
summary.html       전체 요약 표(파일별 행 + 합계 행, 각 행에서 상세 HTML로 링크)
summary.csv        같은 내용의 CSV(엑셀용 UTF-8 BOM)
<파일명>.txt.html   파일별 상세 — 시각적 비교 결과, 타임스탬프 재생, 원문/인식문 전문
```

실패하면 JSON이다:

```json
{ "code": "422", "message": "비교 실패 (exit 1)\n..." }
```

| 상태 | 언제 |
|---|---|
| 400 | `.txt`가 아니거나 허용되지 않는 파일명 |
| 413 | 파일 개수·크기·전체 용량 상한 초과 |
| 422 | 스크립트가 0이 아닌 코드로 끝남(예: 짝이 맞는 파일이 하나도 없음) |
| 429 | 동시 실행 상한(`MAX_CONCURRENT_JOBS`)이 차 있음 |
| 504 | `TIMEOUT_SECONDS` 안에 비교가 끝나지 않음 |

```bash
curl -o result.zip \
  -F "reference=@a/1.txt" -F "reference=@a/2.txt" \
  -F "recognition=@b/1.txt" -F "recognition=@b/2.txt" \
  -F "workers=4" \
  http://localhost:8080/api/compare/v1/compare
```

## 옵션 — 구두점·대소문자는 끌 수 없다

원본 스크립트의 `--remove-punctuation`과 `--ignore-case`는 argparse에서 `store_true`인데
`default=True`다. 즉 **인자를 줘도 안 줘도 항상 True**이고, 끄는 인자가 아예 없다. 그래서 API도
이 둘을 받지 않는다(받아 봐야 지킬 수 없다). 끄고 싶다면 스크립트에 `--no-remove-punctuation`
같은 플래그를 추가해야 하고, 그건 알고리즘 동작을 바꾸는 변경이라 별도 판단이 필요하다.

## 로컬에서 실행

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src uvicorn compare_api.main:app --reload --port 8080
```

`http://localhost:8080/api/compare/v1/healthz`가 `{"status":"UP"}`이면 정상이다. 대화형 문서는
FastAPI 기본값대로 `/docs`에 있다.

## 테스트

```bash
python -m pytest -q
```

테스트는 **실제로 스크립트를 subprocess로 돌린다.** `tests/fixtures`의 다섯 쌍을 비교해
`summary.csv`의 대체/삭제/삽입/총 문자 수가 원본 CLI를 직접 돌렸을 때와 같은지 확인하므로,
이 테스트가 초록이면 엔진이 그대로 동작한다는 뜻이다. 기대값은
[`tests/test_compare.py`](tests/test_compare.py)의 `EXPECTED`에 있다.

## 환경 변수

전부 선택 사항이고, 하나도 없이 기본값으로 뜬다. 정의는
[`src/compare_api/config.py`](src/compare_api/config.py), 템플릿은 [`.env.example`](.env.example).

| 이름 | 기본값 | 설명 |
|---|---|---|
| `API_PREFIX` | `/api/compare/v1` | 라우트 접두사 |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | 콤마 구분. **프론트 주소를 반드시 넣을 것** |
| `MAX_FILES` | `2000` | 참조·인식 각각의 파일 개수 상한 |
| `MAX_FILE_MB` | `10` | 파일 하나의 크기 상한(스크립트도 10MB를 넘기면 건너뛴다) |
| `MAX_TOTAL_MB` | `512` | 업로드 전체 크기 상한 |
| `MAX_WORKERS` | 서버 코어 수 | `-w`의 상한 |
| `TIMEOUT_SECONDS` | `1800` | 비교 한 건의 시간 제한 |
| `MAX_CONCURRENT_JOBS` | `2` | 동시에 도는 비교 작업 수 |

## 규약

- **엔진 파일은 손대지 않는다.** `engine/text_comparison_linux_007.py`는 납품된 원본 그대로다.
  알고리즘을 바꿔야 하면 그 파일을 고치고 `tests/test_compare.py`의 기대값을 다시 뜬다.
- **결과는 파일로 남기지 않는다.** 요청마다 임시 폴더를 만들고 응답을 보낸 뒤 지운다
  (`runner.CompareJob.cleanup`). 업로드된 텍스트도 서버에 남지 않는다.
- **uvicorn 워커는 1개다.** 비교 한 건이 이미 멀티프로세스로 코어를 나눠 쓴다. 동시 실행은
  `MAX_CONCURRENT_JOBS`로만 늘린다.
- **업로드 파일명은 basename만 쓴다.** 경로가 섞여 와도 잘라 내고, `.txt`가 아니면 거절한다
  (`runner.safe_name`).

## 배포

VM에서 git pull → `docker compose up -d --build`. 절차와 프록시 설정은
[`docs/deploy-guide.md`](docs/deploy-guide.md)에 있다.
