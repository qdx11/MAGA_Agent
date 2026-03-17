import json
from typing import Any, Dict, List

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class HeaderDetectorInput(BaseModel):
    excel_structure: str = Field(
        ...,
        description=(
            "excel_structure_parser 실행 결과 JSON 문자열. "
            "이 값이 없으면 툴이 즉시 에러를 반환합니다."
        ),
    )
    sheet_name: str = Field(
        ...,
        description="헤더를 감지할 시트 이름. 예: 'Sheet1'",
    )


@tool(args_schema=HeaderDetectorInput)
def header_detector(excel_structure: str, sheet_name: str) -> str:
    \"\"\"[기능]
    excel_structure_parser 결과를 기반으로 헤더 행 위치와 멀티헤더 정보를 감지합니다.

    [선행 조건]
    - excel_structure_parser: 엑셀 구조 분석 결과(JSON 문자열)

    [사용 시점]
    - 크로스테이블/DB형 테이블에서 컬럼 헤더가 몇 행에 있는지 알아야 할 때.
    - 멀티헤더(대분류/소분류)를 하나의 컬럼명으로 합쳐야 할 때.

    [반환값]
    성공: {
      "status": "success",
      "data": {
        "sheet": "...",
        "header_rows": [3,4],
        "header_confidence": 0.9,
        "columns": [{"col":2,"name":"대분류_소분류", "source_rows":[3,4]}, ...],
        "table_type": "crosstable|database",
        "table_type_confidence": 0.8
      }
    }
    실패: {"status":"error","error_code":"...","early_stop":bool,...}
    \"\"\"
    import math

    try:
        struct = json.loads(excel_structure)
    except json.JSONDecodeError:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "excel_structure JSON 을 파싱할 수 없습니다.",
                "suggested_fix": "excel_structure_parser 결과를 그대로 전달했는지 확인하세요.",
            },
            ensure_ascii=False,
        )

    sheets: List[Dict[str, Any]] = struct.get("sheets", [])
    target = next((s for s in sheets if s.get("name") == sheet_name), None)
    if not target:
        return json.dumps(
            {
                "status": "error",
                "error_code": "SHEET_NOT_FOUND",
                "root_cause": "invalid_path",
                "early_stop": False,
                "message": f"시트 '{sheet_name}' 를 excel_structure 에서 찾을 수 없습니다.",
                "suggested_fix": "시트 이름이 정확한지 확인하세요.",
            },
            ensure_ascii=False,
        )

    data_sample = target.get("data_sample", [])
    style_hints = target.get("style_hints", [])
    if not data_sample:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "데이터 샘플이 비어 있어 헤더를 감지할 수 없습니다.",
                "suggested_fix": "데이터가 있는 시트를 선택했는지 확인하세요.",
            },
            ensure_ascii=False,
        )

    # 1) 스타일 기반 행 스코어링
    row_scores: Dict[int, float] = {}
    for hint in style_hints:
        r = int(hint["row"])
        score = 0.0
        if hint.get("bold"):
            score += 0.5
        bg = hint.get("bg")
        if bg not in (None, "00000000", "FFFFFFFF"):
            score += 0.3
        if hint.get("horizontal") in ("center", "centerContinuous"):
            score += 0.2
        row_scores[r] = row_scores.get(r, 0.0) + score

    # 샘플 상단 5행 정도만 헤더 후보
    candidate_rows = sorted({row["row"] for row in data_sample})[:5]
    scored = [(r, row_scores.get(r, 0.0)) for r in candidate_rows]
    scored.sort(key=lambda x: x[1], reverse=True)

    header_rows: List[int] = []
    if scored:
        top_score = scored[0][1]
        # 점수가 0 이상이면서, top의 60% 이상인 행들은 모두 헤더로 본다.
        for r, s in scored:
            if s <= 0:
                continue
            if top_score == 0 or s >= 0.6 * top_score:
                header_rows.append(r)

    # 스타일 정보만으로 헤더를 찾지 못한 경우, 가장 위의 한 행을 헤더로 가정
    if not header_rows:
        header_rows = [candidate_rows[0]]
        header_confidence = 0.5
    else:
        header_confidence = 0.8 if len(header_rows) == 1 else 0.9

    header_rows_sorted = sorted(header_rows)

    # 2) 멀티헤더 컬럼명 생성
    first_data_row = min(r["row"] for r in data_sample)
    sample_row = next(r for r in data_sample if r["row"] == first_data_row)
    values_len = len(sample_row.get("values", []))

    # 헤더 행들의 값 모으기 (행 순서 유지)
    header_values_by_row: Dict[int, List[Any]] = {}
    for r in data_sample:
        if r["row"] in header_rows_sorted:
            header_values_by_row[r["row"]] = r.get("values", [])[:values_len]

    columns: List[Dict[str, Any]] = []
    for idx in range(values_len):
        labels: List[str] = []
        source_rows: List[int] = []
        for hr in header_rows_sorted:
            row_vals = header_values_by_row.get(hr, [])
            if idx < len(row_vals):
                v = row_vals[idx]
                if v is not None and str(v).strip() != "":
                    labels.append(str(v).strip())
                    source_rows.append(hr)
        if not labels:
            col_name = f"col_{idx+1}"
        elif len(labels) == 1:
            col_name = labels[0]
        else:
            col_name = "_".join(labels)
        columns.append(
            {
                "col": idx + 1,
                "name": col_name,
                "source_rows": source_rows or header_rows_sorted,
            }
        )

    # 3) 간단한 크로스테이블 vs DB 형 판정 (신뢰도만 반환)
    first_col_vals = [row["values"][0] for row in data_sample if row["values"]]
    first_row_vals = data_sample[0]["values"] if data_sample else []

    def _text_ratio(seq: List[Any]) -> float:
        if not seq:
            return 0.0
        n = len(seq)
        t = sum(1 for v in seq if isinstance(v, str))
        return t / n

    first_col_text = _text_ratio(first_col_vals)
    first_row_text = _text_ratio(first_row_vals)

    # 매우 단순한 휴리스틱: 첫 열 대부분 텍스트, 첫 행도 텍스트/날짜 위주면 크로스테이블일 가능성
    if first_col_text > 0.7 and first_row_text > 0.4:
        table_type = "crosstable"
        table_type_confidence = 0.8
    else:
        table_type = "database"
        table_type_confidence = 0.6

    payload = {
        "sheet": sheet_name,
        "header_rows": header_rows_sorted,
        "header_confidence": header_confidence,
        "columns": columns,
        "table_type": table_type,
        "table_type_confidence": table_type_confidence,
    }

    return json.dumps({"status": "success", "data": payload}, ensure_ascii=False)

