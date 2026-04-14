"""
OutlierDetector — IQR 기반 이상값 감지
ExcelCompareTool — 두 파일 버전 비교
"""
import json
from typing import Optional, List
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════
# OutlierDetector
# ══════════════════════════════════════════════════════

class OutlierInput(BaseModel):
    flat_table: str = Field(
        ...,
        description="crosstable_flattener 또는 DB형 테이블 JSON."
    )
    target_column: Optional[str] = Field(
        None,
        description="분석할 컬럼명. None이면 전체 수치 컬럼 분석."
    )
    method: str = Field(
        "IQR",
        description="이상값 감지 방법. IQR 또는 zscore."
    )


@tool(args_schema=OutlierInput)
def outlier_detector(
    flat_table: str,
    target_column: Optional[str] = None,
    method: str = "IQR",
) -> str:
    """
    [기능]
    수치 데이터에서 이상값을 감지합니다.
    IQR 또는 Z-score 방식 선택 가능.
    source_cell로 원본 엑셀 위치 역추적 가능.

    [선행 조건]
    crosstable_flattener 또는 DB형 테이블 결과 필요.

    [사용 시점]
    데이터 검증, "이상값 찾아줘", "이상한 값 있어?" 요청 시.

    [반환값]
    JSON {status, data: {outliers, total_outlier_count, statistics, method}}
    """
    try:
        import pandas as pd
        import numpy as np

        table_data = json.loads(flat_table)
        if table_data.get("status") != "success":
            return json.dumps({
                "status": "error",
                "error_code": "MISSING_PREREQUISITE",
                "root_cause": "missing_prerequisite",
                "early_stop": False,
                "message": "유효한 flat_table이 필요합니다.",
                "suggested_fix": "crosstable_flattener를 먼저 실행하세요.",
            }, ensure_ascii=False)

        rows = table_data["data"]["rows"]
        if not rows:
            return json.dumps({
                "status": "error",
                "error_code": "TOOL_EXECUTION_ERROR",
                "root_cause": "empty_data",
                "early_stop": False,
                "message": "데이터가 없습니다.",
            }, ensure_ascii=False)

        df = pd.DataFrame(rows)

        # 분석할 컬럼 선택
        # crosstable flatten이면 "값" 컬럼이 핵심
        value_col = table_data["data"].get("value_column", "값")
        numeric_cols = df.select_dtypes(include=[float, int]).columns.tolist()

        if target_column:
            if target_column not in df.columns:
                return json.dumps({
                    "status": "error",
                    "error_code": "TOOL_EXECUTION_ERROR",
                    "root_cause": "invalid_column",
                    "early_stop": False,
                    "message": f"'{target_column}' 컬럼이 없습니다.",
                    "suggested_fix": f"사용 가능한 수치 컬럼: {numeric_cols}",
                }, ensure_ascii=False)
            analyze_cols = [target_column]
        else:
            analyze_cols = numeric_cols

        all_outliers = []
        statistics = {}

        for col in analyze_cols:
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(series) < 4:
                continue

            if method == "IQR":
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                mask = (df[col] < lower) | (df[col] > upper)
            else:  # zscore
                mean = series.mean()
                std = series.std()
                if std == 0:
                    continue
                z_scores = (pd.to_numeric(df[col], errors="coerce") - mean) / std
                mask = z_scores.abs() > 3
                lower = mean - 3 * std
                upper = mean + 3 * std

            statistics[col] = {
                "mean": round(float(series.mean()), 4),
                "std": round(float(series.std()), 4),
                "min": round(float(series.min()), 4),
                "max": round(float(series.max()), 4),
                "q1": round(float(series.quantile(0.25)), 4),
                "q3": round(float(series.quantile(0.75)), 4),
                "lower_bound": round(float(lower), 4),
                "upper_bound": round(float(upper), 4),
                "outlier_count": int(mask.sum()),
            }

            # 이상값 행 추출 (source_cell 포함)
            outlier_rows = df[mask].copy()
            for _, row in outlier_rows.iterrows():
                outlier_info = {
                    "column": col,
                    "value": row[col],
                    "source_cell": row.get("source_cell", ""),
                    "source_row": row.get("source_row", ""),
                }
                # ID 컬럼들도 포함
                id_cols = table_data["data"].get("id_columns", [])
                for id_col in id_cols:
                    if id_col in row:
                        outlier_info[id_col] = row[id_col]
                if "항목" in row:
                    outlier_info["항목"] = row["항목"]

                all_outliers.append(outlier_info)

        return json.dumps({
            "status": "success",
            "data": {
                "outliers": all_outliers,
                "total_outlier_count": len(all_outliers),
                "analyzed_columns": analyze_cols,
                "method": method,
                "statistics": statistics,
            }
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "unexpected_error",
            "early_stop": False,
            "message": str(e),
            "suggested_fix": "입력 데이터를 확인하세요.",
        }, ensure_ascii=False)


# ══════════════════════════════════════════════════════
# ExcelCompareTool
# ══════════════════════════════════════════════════════

class ExcelCompareInput(BaseModel):
    base_file: str = Field(..., description="기준 파일 경로 (old)")
    target_file: str = Field(..., description="비교 대상 파일 경로 (new)")
    sheet_name: str = Field("Sheet1", description="비교할 시트명")
    key_columns: Optional[List[str]] = Field(
        None,
        description="행을 식별할 키 컬럼 목록. None이면 공통 컬럼 전체 사용."
    )
    header_row: int = Field(1, description="헤더 행 번호. 기본 1.")


@tool(args_schema=ExcelCompareInput)
def excel_compare_tool(
    base_file: str,
    target_file: str,
    sheet_name: str = "Sheet1",
    key_columns: Optional[List[str]] = None,
    header_row: int = 1,
) -> str:
    """
    [기능]
    두 엑셀 파일의 변경사항을 비교합니다.
    추가/삭제된 행과 변경된 셀을 모두 감지합니다.

    [선행 조건]
    없음. 두 파일 경로만 있으면 됩니다.

    [사용 시점]
    "저번이랑 뭐가 달라졌어?", 버전 비교 요청 시.

    [반환값]
    JSON {status, data: {summary, added_rows, removed_rows, changed_cells}}
    """
    try:
        import pandas as pd

        import os as _os
        for fp in [base_file, target_file]:
            _real = _os.path.realpath(fp)
            _abs = _os.path.abspath(fp)
            if _real != _abs or ".." in _os.path.normpath(fp).split(_os.sep):
                return json.dumps({
                    "status": "error",
                    "error_code": "PATH_TRAVERSAL",
                    "root_cause": "security_violation",
                    "early_stop": True,
                    "message": f"허용되지 않은 경로: {fp}",
                }, ensure_ascii=False)

        df_base = pd.read_excel(
            base_file, sheet_name=sheet_name,
            header=header_row - 1, dtype=str
        ).fillna("")

        df_target = pd.read_excel(
            target_file, sheet_name=sheet_name,
            header=header_row - 1, dtype=str
        ).fillna("")

        # 공통 컬럼만 비교
        common_cols = list(set(df_base.columns) & set(df_target.columns))
        added_cols = list(set(df_target.columns) - set(df_base.columns))
        removed_cols = list(set(df_base.columns) - set(df_target.columns))

        df_base = df_base[common_cols]
        df_target = df_target[common_cols]

        # 키 컬럼 설정
        if key_columns:
            valid_keys = [k for k in key_columns if k in common_cols]
        else:
            valid_keys = common_cols

        # 행 비교
        base_tuples = set(df_base.apply(tuple, axis=1))
        target_tuples = set(df_target.apply(tuple, axis=1))

        added_tuples = target_tuples - base_tuples
        removed_tuples = base_tuples - target_tuples

        added_rows = df_target[
            df_target.apply(tuple, axis=1).isin(added_tuples)
        ].to_dict("records")

        removed_rows = df_base[
            df_base.apply(tuple, axis=1).isin(removed_tuples)
        ].to_dict("records")

        # 셀 레벨 변경 감지 (키 기준 매칭)
        changed_cells = []
        if valid_keys and len(valid_keys) < len(common_cols):
            df_base_idx = df_base.set_index(valid_keys)
            df_target_idx = df_target.set_index(valid_keys)
            common_idx = df_base_idx.index.intersection(df_target_idx.index)

            for idx in common_idx:
                base_row = df_base_idx.loc[idx]
                target_row = df_target_idx.loc[idx]
                value_cols = [c for c in common_cols if c not in valid_keys]
                for col in value_cols:
                    bval = base_row[col] if col in base_row else ""
                    tval = target_row[col] if col in target_row else ""
                    if str(bval) != str(tval):
                        key_info = {k: idx[i] if isinstance(idx, tuple) else idx
                                    for i, k in enumerate(valid_keys)}
                        changed_cells.append({
                            **key_info,
                            "column": col,
                            "old_value": bval,
                            "new_value": tval,
                        })

        return json.dumps({
            "status": "success",
            "data": {
                "summary": {
                    "added_rows": len(added_rows),
                    "removed_rows": len(removed_rows),
                    "changed_cells": len(changed_cells),
                    "added_columns": added_cols,
                    "removed_columns": removed_cols,
                    "base_file": base_file,
                    "target_file": target_file,
                    "sheet_name": sheet_name,
                },
                "added_rows": added_rows[:50],    # 최대 50행
                "removed_rows": removed_rows[:50],
                "changed_cells": changed_cells[:100],  # 최대 100개
            }
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "unexpected_error",
            "early_stop": False,
            "message": str(e),
            "suggested_fix": "파일 경로와 시트명을 확인하세요.",
        }, ensure_ascii=False)
