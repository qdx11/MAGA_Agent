"""
HeaderDetector — 실제 구현
ExcelStructureParser 결과를 받아서:
1. 헤더가 몇 행인지 감지
2. 테이블 타입 (crosstable / database) 판별
3. 멀티헤더 처리
"""
import json
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _detect_header_rows(data_sample: list, header_hints: list) -> list:
    """
    헤더 행 위치 감지.
    스타일 힌트(Bold/배경색) + 값 패턴으로 판단.
    """
    if not data_sample:
        return [0]

    header_rows = []

    for i, (row, is_hint) in enumerate(zip(data_sample, header_hints)):
        # 값이 없는 행 스킵
        non_null = [v for v in row if v is not None and str(v).strip() != ""]
        if not non_null:
            continue

        # 스타일 힌트가 있으면 헤더
        if is_hint:
            header_rows.append(i)
            continue

        # 첫 번째 행은 무조건 헤더 후보
        if i == 0:
            # 전부 텍스트면 헤더
            all_text = all(isinstance(v, str) for v in non_null)
            if all_text:
                header_rows.append(i)
            continue

        # 이전 행이 헤더였고, 이번 행도 전부 텍스트면 멀티헤더
        if header_rows and i == header_rows[-1] + 1:
            all_text = all(isinstance(v, str) for v in non_null)
            if all_text:
                header_rows.append(i)
                continue

        # 데이터 행 만나면 중단
        break

    return header_rows if header_rows else [0]


def _detect_table_type(data_sample: list, header_rows: list) -> tuple:
    """
    테이블 타입 감지.
    반환: (type, confidence, reason)
    - crosstable: 첫 열이 카테고리, 첫 행이 날짜/숫자 시퀀스
    - database: 일반 행-열 형태
    """
    if not data_sample or len(data_sample) <= len(header_rows):
        return "database", 0.5, "데이터 부족으로 database로 가정"

    data_start = len(header_rows)
    if data_start >= len(data_sample):
        return "database", 0.5, "헤더만 있음"

    header_row = data_sample[0] if data_sample else []
    first_data_row = data_sample[data_start] if len(data_sample) > data_start else []

    # 헤더의 첫 번째 값 이후가 날짜/숫자 시퀀스인지 확인
    header_values = [v for v in header_row[1:] if v is not None]
    data_first_col = [
        data_sample[i][0]
        for i in range(data_start, len(data_sample))
        if data_sample[i] and data_sample[i][0] is not None
    ]

    score = 0
    reasons = []

    # 헤더 두 번째 열 이후가 날짜나 숫자면 크로스테이블 가능성
    numeric_or_date_header = sum(
        1 for v in header_values
        if isinstance(v, (int, float)) or _looks_like_date(v)
    )
    if header_values and numeric_or_date_header / len(header_values) > 0.5:
        score += 0.4
        reasons.append("헤더가 날짜/숫자 시퀀스")

    # 첫 열이 카테고리 텍스트면 크로스테이블 가능성
    text_first_col = sum(1 for v in data_first_col if isinstance(v, str))
    if data_first_col and text_first_col / len(data_first_col) > 0.7:
        score += 0.4
        reasons.append("첫 열이 카테고리 텍스트")

    # 데이터가 전부 숫자면 크로스테이블 가능성
    if first_data_row:
        numeric_data = [v for v in first_data_row[1:] if v is not None]
        if numeric_data and all(isinstance(v, (int, float)) for v in numeric_data):
            score += 0.2
            reasons.append("데이터가 전부 수치")

    if score >= 0.6:
        return "crosstable", score, " / ".join(reasons)
    else:
        return "database", 1.0 - score, "일반 행-열 형태"


def _looks_like_date(value) -> bool:
    """값이 날짜처럼 생겼는지 확인"""
    if value is None:
        return False
    s = str(value)
    import re
    date_patterns = [
        r'\d{4}-\d{2}',       # 2024-01
        r'\d{4}/\d{2}',       # 2024/01
        r'\d{1,2}/\d{1,2}',   # 1/1
        r'\d{4}년',             # 2024년
        r'\d{1,2}월',           # 1월
    ]
    return any(re.search(p, s) for p in date_patterns)


def _build_column_names(data_sample: list, header_rows: list) -> list:
    """
    멀티헤더를 하나의 컬럼명으로 합치기.
    예: ["대분류", "소분류"] → "대분류_소분류"
    """
    if not header_rows or not data_sample:
        return []

    if len(header_rows) == 1:
        row = data_sample[header_rows[0]]
        return [str(v) if v is not None else f"col_{i}" for i, v in enumerate(row)]

    # 멀티헤더: 각 열마다 헤더 행 값을 합침
    num_cols = max(len(data_sample[i]) for i in header_rows)
    col_names = []
    for col_idx in range(num_cols):
        parts = []
        for row_idx in header_rows:
            row = data_sample[row_idx]
            if col_idx < len(row) and row[col_idx] is not None:
                val = str(row[col_idx]).strip()
                if val and val not in parts:
                    parts.append(val)
        col_names.append("_".join(parts) if parts else f"col_{col_idx}")

    return col_names


# ── 메인 툴 ─────────────────────────────────────────────
class HeaderDetectorInput(BaseModel):
    excel_structure: str = Field(
        ...,
        description="excel_structure_parser 실행 결과 JSON 문자열."
    )
    sheet_name: Optional[str] = Field(
        None,
        description="분석할 시트명. None이면 첫 번째 시트 사용."
    )


@tool(args_schema=HeaderDetectorInput)
def header_detector(excel_structure: str, sheet_name: Optional[str] = None) -> str:
    """
    [기능]
    엑셀 시트에서 헤더 행 위치와 테이블 타입을 감지합니다.
    멀티헤더, 스타일 힌트, 데이터 패턴을 복합적으로 분석합니다.

    [선행 조건]
    excel_structure_parser 결과 필요.

    [사용 시점]
    구조 파악 후, 데이터 읽기 전에 반드시 호출.

    [반환값]
    JSON {status, data: {header_rows, table_type, table_type_confidence,
                         data_start_row, column_names, is_multi_header}}
    """
    try:
        struct = json.loads(excel_structure)
        if struct.get("status") != "success":
            return json.dumps({
                "status": "error",
                "error_code": "MISSING_PREREQUISITE",
                "root_cause": "invalid_excel_structure",
                "early_stop": False,
                "message": "유효한 excel_structure가 필요합니다.",
                "suggested_fix": "excel_structure_parser를 먼저 실행하세요.",
            }, ensure_ascii=False)

        sheets = struct["data"]["sheets"]
        if not sheets:
            return json.dumps({
                "status": "error",
                "error_code": "TOOL_EXECUTION_ERROR",
                "root_cause": "no_sheets",
                "early_stop": True,
                "message": "시트가 없습니다.",
            }, ensure_ascii=False)

        # 시트 선택
        if sheet_name:
            sheet = next((s for s in sheets if s["name"] == sheet_name), sheets[0])
        else:
            sheet = sheets[0]

        data_sample = sheet.get("data_sample", [])
        header_hints = sheet.get("header_hints", [False] * len(data_sample))

        # 헤더 감지
        header_row_indices = _detect_header_rows(data_sample, header_hints)

        # 테이블 타입 감지
        table_type, confidence, reason = _detect_table_type(data_sample, header_row_indices)

        # 컬럼명 생성
        column_names = _build_column_names(data_sample, header_row_indices)

        # 실제 행 번호 (data_start 기준)
        data_start = sheet["data_start"]["row"]
        actual_header_rows = [data_start + i - 1 for i in header_row_indices]
        actual_data_start_row = data_start + len(header_row_indices)

        return json.dumps({
            "status": "success",
            "data": {
                "sheet_name": sheet["name"],
                "header_rows": actual_header_rows,        # 실제 엑셀 행 번호
                "header_row_count": len(header_row_indices),
                "is_multi_header": len(header_row_indices) > 1,
                "data_start_row": actual_data_start_row,  # 데이터 시작 행
                "table_type": table_type,
                "table_type_confidence": round(confidence, 2),
                "table_type_reason": reason,
                "column_names": column_names,
                "data_start_col": sheet["data_start"]["col"],
            }
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "unexpected_error",
            "early_stop": False,
            "message": str(e),
            "suggested_fix": "excel_structure JSON이 올바른지 확인하세요.",
        }, ensure_ascii=False)
