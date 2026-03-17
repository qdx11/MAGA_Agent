import json
from typing import Any, Dict, List, Optional

import pandas as pd
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class OutlierDetectorInput(BaseModel):
    file_path: Optional[str] = Field(
        None,
        description=(
            "분석할 엑셀 파일 상대 경로. 예: './data/measurement.xlsx'. "
            "CrossTableFlattener 결과(table_json)를 사용할 경우 생략 가능."
        ),
    )
    sheet_name: Optional[str] = Field(
        None,
        description="읽을 시트 이름. file_path 를 사용하는 경우 필수.",
    )
    table_json: Optional[str] = Field(
        None,
        description=(
            "CrossTableFlattener 등에서 생성한 테이블 JSON 문자열. "
            "제공되면 이 데이터를 그대로 사용합니다."
        ),
    )
    target_column: Optional[str] = Field(
        None,
        description="이상값을 탐지할 컬럼명. None 이면 모든 수치 컬럼을 분석합니다.",
    )


@tool(args_schema=OutlierDetectorInput)
def outlier_detector(
    file_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
    table_json: Optional[str] = None,
    target_column: Optional[str] = None,
) -> str:
    """[기능]
    엑셀 또는 평탄화된 테이블 데이터에서 수치 컬럼의 이상값(Outlier)을 IQR 방식으로 탐지합니다.

    [선행 조건]
    - (선택) CrossTableFlattener: 크로스테이블을 평탄화한 경우, table_json 으로 전달.
    - 또는 file_path + sheet_name 으로 직접 엑셀 시트를 읽을 수 있습니다.

    [사용 시점]
    - 측정 데이터의 극단값, 비정상값을 찾아 품질 이상 여부를 확인하고 싶을 때.
    - 라인/설비/품번별 측정값 분포를 점검할 때.

    [반환값]
    성공: {
      "status":"success",
      "data":{
        "columns":[...],                   # 분석한 컬럼 목록
        "outliers":{col:[값,...],...},    # 컬럼별 이상값 리스트
        "total_outlier_count": 10,
        "total_rows": 200
      }
    }
    실패: {"status":"error","error_code":"...","early_stop":bool,...}
    """
    # 입력 검증
    if table_json is None and (file_path is None or sheet_name is None):
        return json.dumps(
            {
                "status": "error",
                "error_code": "MISSING_PREREQUISITE",
                "root_cause": "missing_prerequisite",
                "early_stop": False,
                "message": "table_json 또는 file_path+sheet_name 중 하나가 필요합니다.",
                "suggested_fix": "먼저 CrossTableFlattener 를 실행하거나 file_path 와 sheet_name 을 모두 전달하세요.",
            },
            ensure_ascii=False,
        )

    try:
        if table_json is not None:
            # 평탄화된 JSON 기반 분석
            table = json.loads(table_json)
            data = table.get("data") or table  # 두 형태 모두 허용
            rows = data.get("rows", [])
            if not rows:
                return json.dumps(
                    {
                        "status": "error",
                        "error_code": "INVALID_SCHEMA",
                        "root_cause": "schema_mismatch",
                        "early_stop": False,
                        "message": "table_json 에 rows 가 없어 이상값을 분석할 수 없습니다.",
                        "suggested_fix": "CrossTableFlattener 결과를 그대로 전달했는지 확인하세요.",
                    },
                    ensure_ascii=False,
                )
            df = pd.DataFrame(rows)
        else:
            # 엑셀 시트 직접 읽기
            assert file_path is not None and sheet_name is not None
            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                engine="openpyxl",
            )
    except Exception as exc:  # pragma: no cover
        return json.dumps(
            {
                "status": "error",
                "error_code": "TOOL_EXECUTION_ERROR",
                "root_cause": "unexpected_error",
                "early_stop": False,
                "message": f"데이터를 읽는 중 오류가 발생했습니다: {exc}",
                "suggested_fix": "입력값을 확인하고 다시 시도하세요.",
            },
            ensure_ascii=False,
        )

    if df.empty:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "데이터가 비어 있어 이상값을 분석할 수 없습니다.",
                "suggested_fix": "데이터가 있는 시트를 선택하거나 table_json 을 확인하세요.",
            },
            ensure_ascii=False,
        )

    # 수치 컬럼 선택
    if target_column:
        if target_column not in df.columns:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": "TOOL_EXECUTION_ERROR",
                    "root_cause": "invalid_column",
                    "early_stop": False,
                    "message": f"{target_column} 컬럼을 찾을 수 없거나 수치형이 아닙니다.",
                    "suggested_fix": f"사용 가능한 컬럼: {list(df.columns)}",
                },
                ensure_ascii=False,
            )
        numeric_cols = [target_column]
    else:
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": "INVALID_SCHEMA",
                    "root_cause": "schema_mismatch",
                    "early_stop": False,
                    "message": "수치형 컬럼이 없어 이상값을 분석할 수 없습니다.",
                    "suggested_fix": "수치형 컬럼이 있는 시트를 선택하거나 target_column 을 지정하세요.",
                },
                ensure_ascii=False,
            )

    outliers: Dict[str, List[Any]] = {}
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty:
            outliers[col] = []
            continue
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            outliers[col] = []
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        mask = (series < lower) | (series > upper)
        outliers[col] = series[mask].tolist()

    total_outlier_count = sum(len(v) for v in outliers.values())

    payload = {
        "columns": numeric_cols,
        "outliers": outliers,
        "total_outlier_count": total_outlier_count,
        "total_rows": int(len(df)),
    }

    return json.dumps({"status": "success", "data": payload}, ensure_ascii=False)

