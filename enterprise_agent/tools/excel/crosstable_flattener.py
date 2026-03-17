import json
from typing import Any, Dict, List, Optional

import pandas as pd
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class CrossTableFlattenerInput(BaseModel):
    excel_structure: str = Field(
        ...,
        description="excel_structure_parser 실행 결과 JSON 문자열.",
    )
    header_info: str = Field(
        ...,
        description="header_detector 실행 결과 JSON 문자열.",
    )
    sheet_name: str = Field(
        ...,
        description="크로스테이블을 평탄화할 시트 이름.",
    )
    id_col_count: int = Field(
        1,
        description="왼쪽에서 몇 개의 열을 ID(행 차원)로 쓸지. 기본 1.",
        ge=1,
    )


@tool(args_schema=CrossTableFlattenerInput)
def crosstable_flattener(
    excel_structure: str,
    header_info: str,
    sheet_name: str,
    id_col_count: int = 1,
) -> str:
    \"\"\"[기능]
    크로스테이블 형태의 엑셀 시트를 행 기반 테이블로 평탄화합니다.

    [선행 조건]
    - excel_structure_parser: 엑셀 구조 분석 결과
    - header_detector: 헤더 행/컬럼 정보

    [사용 시점]
    - 가로/세로로 헤더가 있는 피벗/크로스테이블을 DB 형태로 변환하고 싶을 때.

    [반환값]
    성공: {
      "status":"success",
      "data":{
        "rows":[...],            # 평탄화된 레코드 목록
        "columns":[...],         # 컬럼명 리스트
        "source_sheet":"Sheet1"
      }
    }
    실패: {"status":"error","error_code":"...","early_stop":bool,...}
    \"\"\"
    try:
        struct = json.loads(excel_structure)
        header = json.loads(header_info)
    except json.JSONDecodeError:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "excel_structure 또는 header_info JSON 을 파싱할 수 없습니다.",
                "suggested_fix": "두 파라미터 모두 원본 툴 결과 JSON 인지 확인하세요.",
            },
            ensure_ascii=False,
        )

    # 테이블 타입이 크로스테이블이 아닐 경우는 그대로 에러로 돌려준다.
    header_data = header.get("data", {})
    if header_data.get("table_type") != "crosstable":
        return json.dumps(
            {
                "status": "error",
                "error_code": "NOT_CROSSTABLE",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "해당 시트는 크로스테이블로 판정되지 않았습니다.",
                "suggested_fix": "id_col_count 또는 시트 선택을 조정하세요.",
            },
            ensure_ascii=False,
        )

    # 구조 정보에서 대상 시트 찾기
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

    used = target.get("used_range") or {}
    min_row = int(used.get("min_row", 1))
    max_row = int(used.get("max_row", 0))
    min_col = int(used.get("min_col", 1))
    max_col = int(used.get("max_col", 0))

    header_rows = header_data.get("header_rows") or []
    if not header_rows:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "헤더 행 정보가 없어 평탄화할 수 없습니다.",
                "suggested_fix": "먼저 header_detector 를 실행하세요.",
            },
            ensure_ascii=False,
        )

    first_data_row = max(header_rows) + 1
    if first_data_row > max_row:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "헤더 아래에 데이터 행이 없습니다.",
                "suggested_fix": "데이터가 있는 시트인지 확인하세요.",
            },
            ensure_ascii=False,
        )

    # 평탄화 대상 컬럼 정보 (멀티헤더 결합된 컬럼명)
    columns_meta = header_data.get("columns", [])
    col_names = [c["name"] for c in columns_meta]

    # openpyxl 대신 pandas 로 실제 데이터 범위를 읽어온다.
    file_path = struct.get("file_path")
    try:
        df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            header=None,
            engine="openpyxl",
        )
    except Exception as exc:  # pragma: no cover
        return json.dumps(
            {
                "status": "error",
                "error_code": "FILE_NOT_READABLE",
                "root_cause": "file_not_readable",
                "early_stop": True,
                "message": f"엑셀 시트를 읽는 중 오류가 발생했습니다: {exc}",
                "suggested_fix": "파일이 변경되었는지, 시트 이름이 맞는지 확인하세요.",
            },
            ensure_ascii=False,
        )

    # used_range 에 맞게 자르기
    df = df.iloc[min_row - 1 : max_row, min_col - 1 : max_col]

    # 헤더 부분과 데이터 부분 분리
    header_part = df.iloc[[r - min_row for r in header_rows], :].fillna("")
    data_part = df.iloc[(first_data_row - min_row) :, :].reset_index(drop=True)

    # 컬럼명 교체 (멀티헤더 기준)
    # id 영역과 value 영역을 나눈다.
    id_cols = list(range(0, min(id_col_count, df.shape[1])))
    value_cols = list(range(len(id_cols), df.shape[1]))

    # id 영역 컬럼명: 첫 헤더 행에서만 가져오거나, 기본 이름 사용
    id_names: List[str] = []
    if header_part.shape[0] > 0:
        first_header_row = header_part.iloc[0].tolist()
        for idx in id_cols:
            raw = first_header_row[idx] if idx < len(first_header_row) else ""
            if raw:
                id_names.append(str(raw))
            else:
                id_names.append(f"id_{idx+1}")
    else:
        id_names = [f"id_{i+1}" for i in id_cols]

    # value 영역 컬럼명: header_detector 의 columns 메타를 사용
    value_names = col_names[len(id_cols) : len(id_cols) + len(value_cols)]
    if len(value_names) < len(value_cols):
        # 부족한 경우 기본 이름으로 채움
        for i in range(len(value_names), len(value_cols)):
            value_names.append(f"value_{i+1}")

    # 실제 데이터프레임에 컬럼 이름 적용
    df_data = data_part.copy()
    all_names = id_names + value_names
    df_data.columns = all_names[: df_data.shape[1]]

    # 평탄화: id 컬럼은 그대로, value 컬럼은 melt
    id_name_list = id_names
    value_name_list = all_names[len(id_names) : df_data.shape[1]]

    if not value_name_list:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "평탄화할 값(value) 컬럼이 없습니다.",
                "suggested_fix": "id_col_count 를 줄이거나 헤더 구성을 확인하세요.",
            },
            ensure_ascii=False,
        )

    df_long = df_data.melt(
        id_vars=id_name_list,
        value_vars=value_name_list,
        var_name="measure",
        value_name="value",
    )

    rows = df_long.where(pd.notnull(df_long), None).to_dict(orient="records")

    payload = {
        "source_sheet": sheet_name,
        "columns": list(df_long.columns),
        "rows": rows,
    }

    return json.dumps({"status": "success", "data": payload}, ensure_ascii=False)

