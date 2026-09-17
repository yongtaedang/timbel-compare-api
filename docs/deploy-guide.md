# 배포 가이드 (timbel-compare-api)

VM 한 대에 레포를 pull 받아 컨테이너로 띄우는 방식을 전제로 한다. 프론트
([`timbel-compare-front`](../../timbel-compare-front))는 같은 VM에 함께 올려도 되고 따로 둬도 된다 —
브라우저가 API를 직접 호출하므로 둘 사이에 내부 통신은 없다.

## 1. VM 준비

- CPU: 비교가 코어를 전부 쓴다. 4코어 이상 권장.
- 디스크: 업로드 + 결과 zip이 요청당 최대 `MAX_TOTAL_MB`의 두세 배까지 `/tmp`에 잠깐 쌓인다.
- 필요한 것: Docker Engine + Compose 플러그인. 파이썬은 이미지 안에 있으므로 호스트엔 필요 없다.

## 2. 첫 배포

```bash
git clone <저장소 주소> timbel-compare-api
cd timbel-compare-api

cp deploy/dev/.env.example deploy/dev/.env
vi deploy/dev/.env          # CORS_ALLOWED_ORIGINS를 프론트 주소로 반드시 바꾼다

docker compose -f deploy/dev/docker-compose.yml up -d --build
curl localhost:8080/api/compare/v1/healthz     # {"status":"UP"}
```

## 3. 재배포

```bash
git pull
docker compose -f deploy/dev/docker-compose.yml up -d --build
```

이미지를 CI에서 받아 쓰려면 `.gitlab-ci.yml`의 `image` 잡이 Registry에 푸시한 태그를
compose의 `image:`로 바꾸고 `docker compose pull && up -d`만 한다.

## 4. 앞단 프록시

nginx 같은 리버스 프록시를 둘 거라면 두 가지를 꼭 손봐야 한다.

```nginx
location /api/compare/ {
    proxy_pass http://127.0.0.1:8080;

    # 폴더 통째 업로드다 — 기본값 1MB면 413이 난다. MAX_TOTAL_MB보다 넉넉히.
    client_max_body_size 600m;

    # 비교가 몇 분 걸릴 수 있다. 기본 60초면 응답을 받기 전에 끊긴다.
    proxy_read_timeout 1800s;
    proxy_send_timeout 1800s;
}
```

TLS는 이 앞단에서 종단한다. 컨테이너는 평문 8080만 연다.

## 5. CORS

프론트가 브라우저에서 직접 API를 부르므로 `CORS_ALLOWED_ORIGINS`에 **프론트의 실제 origin**이
들어 있어야 한다(스킴·포트까지 정확히). 값이 틀리면 브라우저 콘솔에만 CORS 오류가 뜨고 서버
로그에는 아무것도 남지 않는다 — 증상이 "실행을 눌러도 아무 일도 안 일어난다"로 보인다.

## 6. 결과 대조

배포 뒤 엔진이 그대로인지 확인하려면 픽스처로 한 번 돌려 본다.

```bash
cd tests/fixtures
curl -o /tmp/result.zip \
  -F "reference=@reference/plain.txt" -F "recognition=@recognition/plain.txt" \
  http://localhost:8080/api/compare/v1/compare
unzip -p /tmp/result.zip summary.csv | iconv -f utf-8
# plain.txt,1,0,0,24,95.83,4.17,...
```

같은 입력을 스크립트로 직접 돌린 값이 기준이다:

```bash
python src/compare_api/engine/text_comparison_linux_007.py \
  -A tests/fixtures/reference -B tests/fixtures/recognition -o /tmp/direct
```

`pytest`가 하는 일이 정확히 이 대조다.

## 7. 운영 중 확인

```bash
docker compose -f deploy/dev/docker-compose.yml logs -f api
```

비교 시작/완료 로그에 파일 수·용량·워커 수가 찍힌다. 스크립트 자신의 진행률(tqdm)과 파일별
HTML 생성 로그도 그대로 흘러나온다.

자주 보는 상태:

| 증상 | 원인 |
|---|---|
| 413 | 업로드가 상한을 넘었다. `MAX_TOTAL_MB`/`MAX_FILE_MB` 또는 프록시의 `client_max_body_size` |
| 429 | 동시 실행 상한. `MAX_CONCURRENT_JOBS`를 올리거나 기다린다 |
| 504 | 비교가 `TIMEOUT_SECONDS`를 넘겼다. 파일을 나눠 보내거나 값을 올린다 |
| 422 "비교 실패" | 두 폴더에 같은 이름의 `.txt`가 없다(원본 스크립트가 exit 1로 끝난다) |
