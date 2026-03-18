"""
Mock 툴 모음.
실제 구현 전에 그래프 전체가 돌아가는지 확인하는 용도.
나중에 실제 툴로 교체 예정.
"""
import json
import time
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Optional


# ── ExcelStructureParser ─────────────────────────────
class ExcelStructureInput(BaseModel):
    file_path: str = Field(..., description="분석할 엑셀 파일 경로")

@tool(args_schema=ExcelStructureInput)
def excel_structure_parser(file_path: str) -> str:
    """
    [기능] 엑셀 파일의 전체 구조를 파악합니다 (시트, 병합셀, 메모, 데이터 범위).
    [선행 조건] 없음.
    [사용 시점] 엑셀 관련 모든 작업의 첫 번째 단계.
    [반환값] JSON {status, data: {sheets, merged_cells, comments, ...}}
    """
    # TODO: openpyxl 기반 실제 구현으로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "file_path": file_path,
            "sheets": [
                {
                    "name": "측정데이터",
                    "max_row": 50,
                    "max_col": 10,
                    "data_sample": [
                        ["설비", "2024-01", "2024-02", "2024-03"],
                        ["라인A", 1.23, 1.45, 1.12],
                        ["라인B", 2.11, 2.33, 2.05],
                    ],
                    "merged_cell_count": 3,
                    "has_hidden_rows": False,
                }
            ],
            "merged_cells": {"측정데이터": ["A1:C1"]},
            "comments": {"측정데이터": {"B2": "기준값: 1.5 이하"}},
            "hidden_sheets": [],
            "named_ranges": [],
            "data_start": {"row": 2, "col": 1},
        }
    }, ensure_ascii=False)


# ── HeaderDetector ───────────────────────────────────
class HeaderDetectorInput(BaseModel):
    excel_structure: str = Field(..., description="ExcelStructureParser 결과 JSON")
    sheet_name: Optional[str] = Field(None, description="분석할 시트명. None이면 첫 번째 시트.")

@tool(args_schema=HeaderDetectorInput)
def header_detector(excel_structure: str, sheet_name: Optional[str] = None) -> str:
    """
    [기능] 엑셀 시트에서 헤더 행 위치와 테이블 타입을 감지합니다.
    [선행 조건] ExcelStructureParser 결과 필요.
    [사용 시점] 구조 파악 후, 데이터 읽기 전에 호출.
    [반환값] JSON {status, data: {header_rows, table_type, table_type_confidence, data_start_row}}
    """
    # TODO: 실제 구현으로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "header_rows": [1],
            "table_type": "crosstable",
            "table_type_confidence": 0.92,
            "data_start_row": 2,
            "id_columns": ["설비"],
            "value_columns": ["2024-01", "2024-02", "2024-03"],
        }
    }, ensure_ascii=False)


# ── CrossTableFlattener ──────────────────────────────
class CrossTableInput(BaseModel):
    excel_structure: str = Field(..., description="ExcelStructureParser 결과 JSON")
    header_info: str = Field(..., description="HeaderDetector 결과 JSON")
    sheet_name: Optional[str] = Field(None, description="시트명")
    id_col_count: int = Field(1, description="왼쪽에서 ID로 쓸 열 개수")

@tool(args_schema=CrossTableInput)
def crosstable_flattener(
    excel_structure: str,
    header_info: str,
    sheet_name: Optional[str] = None,
    id_col_count: int = 1,
) -> str:
    """
    [기능] 크로스테이블 형태의 엑셀 데이터를 DB형(행-열 정규화)으로 변환합니다.
    [선행 조건] ExcelStructureParser, HeaderDetector 결과 필요.
    [사용 시점] table_type이 crosstable일 때.
    [반환값] JSON {status, data: {rows: [...], columns: [...], source_info}}
    """
    # TODO: 실제 구현으로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "rows": [
                {"설비": "라인A", "날짜": "2024-01", "측정값": 1.23, "source_cell": "Sheet1!R2C2"},
                {"설비": "라인A", "날짜": "2024-02", "측정값": 1.45, "source_cell": "Sheet1!R2C3"},
                {"설비": "라인A", "날짜": "2024-03", "측정값": 1.12, "source_cell": "Sheet1!R2C4"},
                {"설비": "라인B", "날짜": "2024-01", "측정값": 2.11, "source_cell": "Sheet1!R3C2"},
                {"설비": "라인B", "날짜": "2024-02", "측정값": 2.33, "source_cell": "Sheet1!R3C3"},
                {"설비": "라인B", "날짜": "2024-03", "측정값": 2.05, "source_cell": "Sheet1!R3C4"},
            ],
            "columns": ["설비", "날짜", "측정값"],
            "total_rows": 6,
        }
    }, ensure_ascii=False)


# ── OutlierDetector ──────────────────────────────────
class OutlierInput(BaseModel):
    flat_table: str = Field(..., description="CrossTableFlattener 또는 DB형 테이블 JSON")
    target_column: Optional[str] = Field(None, description="분석할 컬럼. None이면 전체 수치 컬럼.")

@tool(args_schema=OutlierInput)
def outlier_detector(flat_table: str, target_column: Optional[str] = None) -> str:
    """
    [기능] 수치 데이터에서 IQR 방식으로 이상값을 감지합니다.
    [선행 조건] CrossTableFlattener 또는 DB형 테이블 필요.
    [사용 시점] 데이터 검증, 이상값 분석 요청 시.
    [반환값] JSON {status, data: {outliers, total_outlier_count, columns}}
    """
    # TODO: 실제 구현으로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "outliers": {
                "측정값": [2.33]  # 라인B 2024-02
            },
            "total_outlier_count": 1,
            "columns": ["측정값"],
            "method": "IQR",
            "outlier_details": [
                {
                    "column": "측정값",
                    "value": 2.33,
                    "설비": "라인B",
                    "날짜": "2024-02",
                    "source_cell": "Sheet1!R3C3",
                }
            ]
        }
    }, ensure_ascii=False)


# ── ExcelCompareTool ─────────────────────────────────
class ExcelCompareInput(BaseModel):
    base_file: str = Field(..., description="기준 파일 경로 (old)")
    target_file: str = Field(..., description="비교 대상 파일 경로 (new)")
    sheet_name: str = Field("Sheet1", description="비교할 시트명")
    key_columns: Optional[list] = Field(None, description="키 컬럼 목록")

@tool(args_schema=ExcelCompareInput)
def excel_compare_tool(
    base_file: str,
    target_file: str,
    sheet_name: str = "Sheet1",
    key_columns: Optional[list] = None,
) -> str:
    """
    [기능] 두 엑셀 파일의 변경사항을 비교합니다 (추가/삭제/변경).
    [선행 조건] 없음.
    [사용 시점] 버전 비교 요청 시.
    [반환값] JSON {status, data: {summary, added_rows, removed_rows, changed_cells}}
    """
    # TODO: 실제 구현으로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "summary": {
                "added_rows": 2,
                "removed_rows": 0,
                "changed_cells": 3,
                "base_file": base_file,
                "target_file": target_file,
            },
            "added_rows": [
                {"설비": "라인C", "2024-01": 1.55, "2024-02": 1.60},
                {"설비": "라인D", "2024-01": 3.10, "2024-02": 3.05},
            ],
            "removed_rows": [],
            "changed_cells": [
                {"row_key": "라인A", "column": "2024-02", "old_value": 1.45, "new_value": 1.50},
                {"row_key": "라인B", "column": "2024-01", "old_value": 2.11, "new_value": 2.15},
                {"row_key": "라인B", "column": "2024-03", "old_value": 2.05, "new_value": 2.00},
            ],
        }
    }, ensure_ascii=False)
