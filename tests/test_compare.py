"""API 테스트 — 실제로 스크립트를 돌려 zip이 나오는지까지 본다.

tests/fixtures의 참조/인식 폴더는 원본 CLI로 직접 돌려 본 것과 같은 입력이다. 기대 수치는
`python src/compare_api/engine/text_comparison_linux_007.py -A ... -B ... -o ...`의
summary.csv에서 그대로 가져왔다(docs/deploy-guide.md "결과 대조" 절).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from compare_api.main import app
from compare_api.runner import CompareError, safe_name

FIXTURES = Path(__file__).parent / "fixtures"

# 원본 스크립트가 낸 값(기본 옵션). 파일명 → (대체, 삭제, 삽입, 총 문자 수)
EXPECTED = {
    "eligantu.txt": (0, 1, 0, 22),
    "identical.txt": (0, 0, 0, 11),
    "plain.txt": (1, 0, 0, 24),
    "punct.txt": (0, 0, 0, 22),
    "timestamp.txt": (1, 0, 0, 36),
}

client = TestClient(app)


def upload_fields(names: list[str] | None = None) -> list[tuple[str, tuple[str, bytes, str]]]:
    """multipart 폼 필드 목록을 만든다. names로 일부 파일만 보낼 수 있다."""
    fields: list[tuple[str, tuple[str, bytes, str]]] = []
    for folder, field in (("reference", "reference"), ("recognition", "recognition")):
        for path in sorted((FIXTURES / folder).glob("*.txt")):
            if names and path.name not in names:
                continue
            fields.append((field, (path.name, path.read_bytes(), "text/plain")))
    return fields


def test_healthz() -> None:
    response = client.get("/api/compare/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_compare_returns_zip_with_reports() -> None:
    response = client.post("/api/compare/v1/compare", files=upload_fields(), data={"workers": 2})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "compare-result-" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "summary.html" in names
        assert "summary.csv" in names
        for file_name in EXPECTED:
            assert f"{file_name}.html" in names


def test_summary_csv_matches_original_script() -> None:
    response = client.post("/api/compare/v1/compare", files=upload_fields())
    assert response.status_code == 200, response.text

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        raw = archive.read("summary.csv").decode("utf-8-sig")

    rows = {row["파일명"]: row for row in csv.DictReader(io.StringIO(raw))}

    for file_name, (sub, deleted, inserted, total) in EXPECTED.items():
        row = rows[file_name]
        assert int(row["대체(Substitutions)"]) == sub
        assert int(row["삭제(Deletions)"]) == deleted
        assert int(row["삽입(Insertions)"]) == inserted
        assert int(row["총_문자_수"]) == total

    summary = next(row for name, row in rows.items() if name.startswith("=== 요약"))
    assert int(summary["총_문자_수"]) == sum(e[3] for e in EXPECTED.values())


def test_eli_gantu_option_changes_result() -> None:
    """--eli-gantu를 켜면 감탄사가 빠져 참조문 글자 수가 줄어든다."""
    files = upload_fields(["eligantu.txt"])

    plain = client.post("/api/compare/v1/compare", files=files)
    stripped = client.post("/api/compare/v1/compare", files=files, data={"eli_gantu": "true"})

    def total_chars(response) -> int:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            raw = archive.read("summary.csv").decode("utf-8-sig")
        row = next(csv.DictReader(io.StringIO(raw)))
        return int(row["총_문자_수"])

    assert total_chars(stripped) < total_chars(plain)


def test_no_common_file_names_is_rejected() -> None:
    files = [
        ("reference", ("a.txt", b"hello", "text/plain")),
        ("recognition", ("b.txt", b"hello", "text/plain")),
    ]
    response = client.post("/api/compare/v1/compare", files=files)
    assert response.status_code == 422
    assert "비교 실패" in response.json()["message"]


def test_non_txt_upload_is_rejected() -> None:
    files = [
        ("reference", ("a.pdf", b"%PDF", "application/pdf")),
        ("recognition", ("a.pdf", b"%PDF", "application/pdf")),
    ]
    response = client.post("/api/compare/v1/compare", files=files)
    assert response.status_code == 400
    assert ".txt" in response.json()["message"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain.txt", "plain.txt"),
        ("reference/plain.txt", "plain.txt"),
        (r"reference\plain.txt", "plain.txt"),
        # 상위 경로는 이름을 거절하는 게 아니라 잘라 낸다 — 결과는 폴더 안의 단순 파일명이다.
        ("../../etc/passwd.txt", "passwd.txt"),
    ],
)
def test_safe_name_keeps_only_basename(raw: str, expected: str) -> None:
    assert safe_name(raw) == expected


@pytest.mark.parametrize("raw", [".hidden.txt", "", "note.md", "..", "reference/"])
def test_safe_name_rejects_dangerous_names(raw: str) -> None:
    with pytest.raises(CompareError):
        safe_name(raw)
