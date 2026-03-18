"""
CrossTableFlattener — 실제 구현
크로스테이블 → DB형(행-열 정규화) 변환.
source_cell 추적 포함.
"""
import json
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import openpyxl


def _fill_merged_cells(ws) -> dict:
    merged_values = {}
    for merged_range in ws.merged_cells.ranges:
        first_val = ws.cell(merged_range.min_row, merged_range.min_col).value
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for col in range(merged_range.min_col, merged_range.max_col + 1):
                merged_values[(row, col)] = first_val
    return merged_values


class CrossTableInput(BaseModel):
    excel_structure: str = Field(..., description="excel_structure_parser 결과 JSON")
    header_info: str = Field(..., description="header_detector 결과 JSON")
    sheet_name: Optional[str] = Field(None, description="시트명. None이면 header_info의 시트 사용.")
    id_col_count: int = Field(1, description="왼쪽에서 ID로 쓸 열 개수. 기본 1.")


@tool(args_schema=CrossTableInput)
def crosstable_flattener(
    excel_structure: str,
    header_info: str,
    sheet_name: Optional[str] = None,
    id_col_count: int = 1,
) -> str:
    """
    [기능]
    크로스테이블 형태의 엑셀을 DB형(행-열 정규화)으로 변환합니다.
    source_cell 정보를 유지하여 원본 위치 역추적 가능.

    [선행 조건]
    excel_structure_parser, header_detector 결과 필요.

    [사용 시점]
    table_type이 crosstable일 때. 분석 전에 호출.

    [반환값]
    JSON {status, data: {rows, columns, total_rows, id_columns, value_column}}
    각 row에 source_cell 포함.
    """
    try:
        struct = json.loads(excel_structure)
        header = json.loads(header_info)

        if struct.get("status") != "success" or header.get("status") != "success":
            return json.dumps({
                "status": "error",
                "error_code": "MISSING_PREREQUISITE",
                "root_cause": "missing_prerequisite",
                "early_stop": False,
                "message": "excel_structure와 header_info가 모두 필요합니다.",
                "suggested_fix": "excel_structure_parser와 header_detector를 먼저 실행하세요.",
            }, ensure_ascii=False)

        file_path = struct["data"]["file_path"]
        hdata = header["data"]
        target_sheet = sheet_name or hdata["sheet_name"]

        # 파일 다시 열기 (원본 데이터 읽기)
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb[target_sheet]
        merged_values = _fill_merged_cells(ws)

        # 코멘트 수집
        comments = {}
        for row in ws.iter_rows():
            for cell in row:
                if cell.comment:
                    comments[(cell.row, cell.column)] = cell.comment.text

        data_start_row = hdata["data_start_row"]
        data_start_col = hdata["data_start_col"]
        header_rows = hdata["header_rows"]
        column_names = hdata["column_names"]

        # 헤더에서 ID 컬럼명과 값 컬럼명(날짜/항목) 분리
        id_col_names = column_names[:id_col_count]
        value_col_names = column_names[id_col_count:]

        # 데이터 읽기 + flatten
        rows = []
        for excel_row in range(data_start_row, ws.max_row + 1):
            # ID 값 읽기
            id_values = {}
            for col_offset, id_name in enumerate(id_col_names):
                col_idx = data_start_col + col_offset
                val = merged_values.get((excel_row, col_idx))
                if val is None:
                    val = ws.cell(excel_row, col_idx).value
                id_values[id_name] = val

            # 모든 ID가 None이면 빈 행 → 스킵
            if all(v is None for v in id_values.values()):
                continue

            # 각 값 컬럼을 별도 행으로 flatten
            for col_offset, val_col_name in enumerate(value_col_names):
                col_idx = data_start_col + id_col_count + col_offset
                value = merged_values.get((excel_row, col_idx))
                if value is None:
                    value = ws.cell(excel_row, col_idx).value

                # source_cell 추적
                cell_letter = ws.cell(excel_row, col_idx).column_letter
                source_cell = f"{target_sheet}!{cell_letter}{excel_row}"

                row_data = {
                    **id_values,
                    "항목": val_col_name,
                    "값": value,
                    "source_cell": source_cell,
                    "source_row": excel_row,
                    "source_col": col_idx,
                }

                # 해당 셀 코멘트 추가
                comment = comments.get((excel_row, col_idx))
                if comment:
                    row_data["comment"] = comment

                rows.append(row_data)

        wb.close()

        columns = id_col_names + ["항목", "값", "source_cell"]

        return json.dumps({
            "status": "success",
            "data": {
                "rows": rows,
                "columns": columns,
                "total_rows": len(rows),
                "id_columns": id_col_names,
                "value_column": "값",
                "dimension_column": "항목",
                "sheet_name": target_sheet,
            }
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "unexpected_error",
            "early_stop": False,
            "message": str(e),
            "suggested_fix": "입력 파라미터를 확인하세요.",
        }, ensure_ascii=False)
