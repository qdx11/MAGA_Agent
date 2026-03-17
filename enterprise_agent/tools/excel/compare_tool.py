import json
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ExcelCompareInput(BaseModel):
    base_file: str = Field(
        ...,
        description="비교 기준이 되는 엑셀 파일 상대 경로. 예: './data/version_old.xlsx'",
    )
    target_file: str = Field(
        ...,
        description="비교 대상 엑셀 파일 상대 경로. 예: './data/version_new.xlsx'",
    )
    sheet_name: str = Field(
        "Sheet1",
        description="비교할 시트 이름. 두 파일 모두 동일한 시트 이름이어야 합니다.",
    )
    key_columns: List[str] = Field(
        default_factory=list,
        description=(
            "행을 식별하는 키 컬럼 목록. 지정하지 않으면 공통 컬럼 전체를 키로 사용합니다. "
            "예: ['날짜','라인','설비']"
        ),
    )


@tool(args_schema=ExcelCompareInput)
def excel_compare_tool(
    base_file: str,
    target_file: str,
    sheet_name: str = "Sheet1",
    key_columns: Optional[List[str]] = None,
) -> str:
    \"\"\"[기능]
    두 개의 엑셀 파일(같은 시트 구조)을 비교하여 추가/삭제/변경된 행과 값의 차이를 요약합니다.

    [선행 조건]
    없음. (구조 파악을 하고 싶다면 ExcelStructureParser 를 별도로 사용할 수 있습니다.)

    [사용 시점]
    - 버전이 다른 두 엑셀 파일에서 무엇이 바뀌었는지 알고 싶을 때.
    - 라인/설비/품번별 생산/측정 데이터의 변경 내역을 점검할 때.

    [반환값]
    성공: {
      "status":"success",
      "data":{
        "summary":{...},
        "added_rows":[...],
        "removed_rows":[...],
        "changed_cells":[
          {
            "key": {"날짜": "...", "라인": "..."},
            "column": "측정값",
            "old": 10,
            "new": 12
          },
          ...
        ]
      }
    }
    실패: {"status":"error","error_code":"...","early_stop":bool,...}
    \"\"\"
    try:
        df_base = pd.read_excel(base_file, sheet_name=sheet_name, engine="openpyxl")
        df_target = pd.read_excel(target_file, sheet_name=sheet_name, engine="openpyxl")
    except FileNotFoundError as exc:
        missing = str(exc).split("'")[1] if "'" in str(exc) else str(exc)
        return json.dumps(
            {
                "status": "error",
                "error_code": "FILE_NOT_FOUND",
                "root_cause": "invalid_path",
                "early_stop": False,
                "message": f"{missing} 파일을 찾을 수 없습니다.",
                "suggested_fix": "파일 경로와 시트 이름이 올바른지 확인하세요.",
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover
        return json.dumps(
            {
                "status": "error",
                "error_code": "FILE_NOT_READABLE",
                "root_cause": "file_not_readable",
                "early_stop": True,
                "message": f"엑셀 파일을 읽는 중 오류가 발생했습니다: {exc}",
                "suggested_fix": "파일이 손상되었는지 또는 암호화되었는지 확인하세요.",
            },
            ensure_ascii=False,
        )

    if df_base.empty and df_target.empty:
        return json.dumps(
            {
                "status": "error",
                "error_code": "INVALID_SCHEMA",
                "root_cause": "schema_mismatch",
                "early_stop": False,
                "message": "두 시트 모두 데이터가 비어 있습니다.",
                "suggested_fix": "데이터가 있는 시트를 선택하세요.",
            },
            ensure_ascii=False,
        )

    # 키 컬럼 결정
    if not key_columns:
        common_cols = sorted(set(df_base.columns) & set(df_target.columns))
        if not common_cols:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": "INVALID_SCHEMA",
                    "root_cause": "schema_mismatch",
                    "early_stop": False,
                    "message": "두 파일 사이에 공통 컬럼이 없습니다.",
                    "suggested_fix": "시트 구조가 동일한지 확인하세요.",
                },
                ensure_ascii=False,
            )
        key_cols = common_cols
    else:
        missing_keys = [k for k in key_columns if k not in df_base.columns or k not in df_target.columns]
        if missing_keys:
            return json.dumps(
                {
                    "status": "error",
                    "error_code": "INVALID_SCHEMA",
                    "root_cause": "schema_mismatch",
                    "early_stop": False,
                    "message": f"키 컬럼 {missing_keys} 를 두 시트에서 모두 찾을 수 없습니다.",
                    "suggested_fix": "key_columns 를 공통 컬럼으로 설정하세요.",
                },
                ensure_ascii=False,
            )
        key_cols = key_columns

    # 중복 키 방지를 위해 키 컬럼 조합이 유일하다고 가정 (사내 양식 설계 전제)
    df_base_keyed = df_base.set_index(key_cols)
    df_target_keyed = df_target.set_index(key_cols)

    # outer join 으로 키 합집합 생성
    all_index = df_base_keyed.index.union(df_target_keyed.index)
    base_aligned = df_base_keyed.reindex(all_index)
    target_aligned = df_target_keyed.reindex(all_index)

    # 추가/삭제 행
    added_mask = base_aligned.isna().all(axis=1) & ~target_aligned.isna().all(axis=1)
    removed_mask = ~base_aligned.isna().all(axis=1) & target_aligned.isna().all(axis=1)

    def _reset_index_rows(df: pd.DataFrame, mask) -> List[Dict[str, Any]]:
        tmp = df[mask].reset_index()
        return tmp.where(pd.notnull(tmp), None).to_dict(orient="records")

    added_rows = _reset_index_rows(target_aligned, added_mask)
    removed_rows = _reset_index_rows(base_aligned, removed_mask)

    # 변경된 셀
    common_mask = ~base_aligned.isna().all(axis=1) & ~target_aligned.isna().all(axis=1)
    base_common = base_aligned[common_mask]
    target_common = target_aligned[common_mask]

    changed_cells: List[Dict[str, Any]] = []
    value_cols = [c for c in base_common.columns if c in target_common.columns]

    for idx in base_common.index:
        base_row = base_common.loc[idx]
        target_row = target_common.loc[idx]
        for col in value_cols:
            old = base_row[col]
            new = target_row[col]
            if pd.isna(old) and pd.isna(new):
                continue
            if (pd.isna(old) and not pd.isna(new)) or (not pd.isna(old) and pd.isna(new)) or old != new:
                key_dict: Dict[str, Any]
                if isinstance(idx, tuple):
                    key_dict = {k: v for k, v in zip(key_cols, idx)}
                else:
                    key_dict = {key_cols[0]: idx}
                changed_cells.append(
                    {
                        "key": key_dict,
                        "column": col,
                        "old": None if pd.isna(old) else old,
                        "new": None if pd.isna(new) else new,
                    }
                )

    summary = {
        "added_rows": len(added_rows),
        "removed_rows": len(removed_rows),
        "changed_cells": len(changed_cells),
        "total_base_rows": int(len(df_base)),
        "total_target_rows": int(len(df_target)),
        "key_columns": key_cols,
    }

    payload = {
        "summary": summary,
        "added_rows": added_rows,
        "removed_rows": removed_rows,
        "changed_cells": changed_cells,
    }

    return json.dumps({"status": "success", "data": payload}, ensure_ascii=False)

