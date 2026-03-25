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


def _build_data_sample_from_row_index(row_index: list) -> list:
    """
    structure_parser의 row_index를 header_detector가 쓰던 data_sample 유사 형태로 복원.
    preview만 존재하므로 각 행의 비어있지 않은 값 5개까지만 재구성한다.
    """
    if not row_index:
        return []

    max_cols = 0
    for row in row_index:
        first_col = row.get("first_col") or 1
        preview = row.get("preview") or []
        max_cols = max(max_cols, first_col - 1 + len(preview))

    data_sample = []
    for row in row_index:
        reconstructed = [None] * max_cols
        first_col = row.get("first_col")
        preview = row.get("preview") or []
        if first_col is not None:
            start_idx = max(first_col - 1, 0)
            for offset, value in enumerate(preview):
                idx = start_idx + offset
                if idx < len(reconstructed):
                    reconstructed[idx] = value
        data_sample.append(reconstructed)

    return data_sample


def _detect_header_rows(row_index: list, header_hints: list) -> list:
    """
    헤더 행 위치 감지.
    스타일 힌트(Bold/배경색) + 값 패턴으로 판단.
    """
    if not row_index:
        return [0]

    header_rows = []

    for i, row_summary in enumerate(row_index):
        is_hint = header_hints[i] if i < len(header_hints) else False

        if row_summary.get("non_null_count", 0) <= 0:
            continue

        # 스타일 힌트가 있으면 헤더
        if is_hint:
            header_rows.append(i)
            continue

        # 첫 번째 행은 무조건 헤더 후보
        if i == 0:
            if row_summary.get("value_type") == "text":
                header_rows.append(i)
            continue

        # 이전 행이 헤더였고, 이번 행도 전부 텍스트면 멀티헤더
        if header_rows and i == header_rows[-1] + 1:
            if row_summary.get("value_type") == "text":
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
        if _looks_like_number(v) or _looks_like_date(v)
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
        if numeric_data and all(_looks_like_number(v) for v in numeric_data):
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


def _looks_like_number(value) -> bool:
    """문자열로 들어온 수치도 숫자로 간주"""
    if value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    s = str(value).strip().replace(",", "")
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _resolve_data_start_row(row_index: list, header_row_indices: list) -> int:
    """
    실제 데이터 시작 행 계산.
    기존 data_start.row 대신 row_index의 첫 실제 행번호를 기준으로 잡되,
    가능하면 마지막 헤더 다음의 실제 행 번호를 우선 사용한다.
    """
    if not row_index:
        return 1

    if not header_row_indices:
        return row_index[0]["row"]

    next_idx = header_row_indices[-1] + 1
    if next_idx < len(row_index):
        return row_index[next_idx]["row"]

    first_row_num = row_index[0]["row"]
    return first_row_num + len(header_row_indices)


def _resolve_data_start_col(row_index: list, header_row_indices: list) -> int:
    """헤더 구간의 시작 열을 data_start_col로 사용"""
    if not row_index:
        return 1

    candidate_indices = header_row_indices or [0]
    cols = [
        row_index[idx].get("first_col")
        for idx in candidate_indices
        if idx < len(row_index) and row_index[idx].get("first_col") is not None
    ]
    if cols:
        return min(cols)

    return row_index[0].get("first_col") or 1


def _build_column_names(data_sample: list, header_row_excel_nums: list) -> list:
    """
    멀티헤더를 하나의 컬럼명으로 합치기.
    예: ["대분류", "소분류"] → "대분류_소분류"
    header_row_excel_nums: 실제 엑셀 행 번호 리스트 (1-based).
    data_sample은 엑셀 1행부터 시작하므로 data_sample[row_num - 1]로 접근.
    """
    if not header_row_excel_nums or not data_sample:
        return []

    # 유효한 행 번호만 필터 (data_sample 범위 내)
    valid_nums = [n for n in header_row_excel_nums if 0 < n <= len(data_sample)]
    if not valid_nums:
        return []

    if len(valid_nums) == 1:
        row = data_sample[valid_nums[0] - 1]
        return [str(v) if v is not None else f"col_{i}" for i, v in enumerate(row)]

    # 멀티헤더: 각 열마다 헤더 행 값을 합침
    num_cols = max(len(data_sample[n - 1]) for n in valid_nums)
    col_names = []
    for col_idx in range(num_cols):
        parts = []
        for row_num in valid_nums:
            row = data_sample[row_num - 1]
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

        row_index = sheet.get("row_index", [])
        data_sample = sheet.get("data_sample") or _build_data_sample_from_row_index(row_index)
        header_hints = sheet.get("header_hints", [False] * len(row_index or data_sample))

        # 헤더 감지
        header_row_indices = _detect_header_rows(row_index, header_hints)

        # 실제 엑셀 행 번호 계산 (컬럼명 생성보다 먼저 필요)
        if row_index:
            actual_header_rows = [
                row_index[i]["row"] for i in header_row_indices if i < len(row_index)
            ]
            actual_data_start_row = _resolve_data_start_row(row_index, header_row_indices)
            data_start_col = _resolve_data_start_col(row_index, header_row_indices)
        else:
            actual_header_rows = [i + 1 for i in header_row_indices]
            actual_data_start_row = len(header_row_indices) + 1
            data_start_col = 1

        # 테이블 타입 감지
        table_type, confidence, reason = _detect_table_type(data_sample, header_row_indices)

        # 컬럼명 생성 (actual_header_rows = 실제 엑셀 행번호, data_sample[row-1]로 접근)
        column_names = _build_column_names(data_sample, actual_header_rows)

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
                "data_start_col": data_start_col,
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
