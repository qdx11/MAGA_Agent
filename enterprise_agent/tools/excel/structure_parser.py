import json
import os
from typing import Any, Dict, List

from langchain_core.tools import tool
from openpyxl import load_workbook
from pydantic import BaseModel, Field


class ExcelStructureParserInput(BaseModel):
    file_path: str = Field(
        ...,
        description=(
            "분석할 엑셀 파일 상대 경로. 예: './data/measurement.xlsx'. "
            "절대 경로(/)나 상위 경로(..)는 사용 불가."
        ),
    )


def _build_style_hint(cell) -> Dict[str, Any]:
    bold = bool(getattr(cell.font, "bold", False))
    fill = getattr(cell, "fill", None)
    fg = None
    if fill and getattr(fill, "fgColor", None):
        fg = getattr(fill.fgColor, "rgb", None)
    horizontal = getattr(getattr(cell, "alignment", None), "horizontal", None)
    is_header_hint = bold or (fg not in (None, "00000000", "FFFFFFFF"))
    return {
        "row": cell.row,
        "col": cell.column,
        "bold": bold,
        "bg": fg,
        "horizontal": horizontal,
        "is_header_hint": bool(is_header_hint),
    }


@tool(args_schema=ExcelStructureParserInput)
def excel_structure_parser(file_path: str) -> str:
    """[기능]
    엑셀 파일의 원본 구조를 분석하고, 헤더/크로스테이블 감지에 필요한 최소 정보만 추출합니다.

    [선행 조건]
    없음.

    [사용 시점]
    - 엑셀 기반 분석/비교/이상값 탐지 툴을 실행하기 전에 항상 먼저 호출합니다.
    - 데이터 시작 행/열, 멀티헤더, 병합셀, 코멘트 등을 파악해야 할 때 사용합니다.

    [반환값]
    성공: {"status":"success","data":{...}}
    실패: {"status":"error","error_code":"...","early_stop":bool,...}
    """
    # 보안: 경로 검증
    if ".." in file_path or file_path.startswith("/"):
        return json.dumps(
            {
                "status": "error",
                "error_code": "PATH_TRAVERSAL",
                "root_cause": "security_violation",
                "early_stop": True,
                "message": "허용되지 않은 경로 접근입니다.",
                "suggested_fix": "상대 경로(./data/...)를 사용하세요.",
            },
            ensure_ascii=False,
        )

    if not os.path.exists(file_path):
        return json.dumps(
            {
                "status": "error",
                "error_code": "FILE_NOT_FOUND",
                "root_cause": "invalid_path",
                "early_stop": False,
                "message": f"{file_path} 파일을 찾을 수 없습니다.",
                "suggested_fix": "파일 경로가 올바른지 확인하세요.",
            },
            ensure_ascii=False,
        )

    try:
        wb = load_workbook(file_path, data_only=True)
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

    sheets: List[Dict[str, Any]] = []

    for ws in wb.worksheets:
        # used range 계산
        min_row = ws.min_row or 1
        max_row = ws.max_row or 0
        min_col = ws.min_column or 1
        max_col = ws.max_column or 0

        # 상단 일부만 샘플링 (헤더/패턴 파악용)
        sample_limit = min(max_row, min_row + 19)  # 최대 20행
        data_sample: List[Dict[str, Any]] = []
        for r in range(min_row, sample_limit + 1):
            row_cells = ws[r]
            values = [c.value for c in row_cells[min_col - 1 : max_col]]
            if any(v is not None for v in values):
                data_sample.append({"row": r, "values": values})

        # 코멘트가 있는 셀만 추출
        comments: Dict[str, str] = {}
        for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
            for cell in row:
                if cell.comment and cell.comment.text:
                    key = f"R{cell.row}C{cell.column}"
                    comments[key] = cell.comment.text

        # 병합 영역 정보 + 값 채우기 힌트
        merged_cells = []
        for mr in ws.merged_cells.ranges:
            merged_cells.append(
                {
                    "min_row": mr.min_row,
                    "max_row": mr.max_row,
                    "min_col": mr.min_col,
                    "max_col": mr.max_col,
                }
            )

        # 스타일 힌트: 상단 몇 행만, 값이 있는 셀만
        style_hints: List[Dict[str, Any]] = []
        for r in range(min_row, sample_limit + 1):
            for c in range(min_col, max_col + 1):
                cell = ws.cell(row=r, column=c)
                if cell.value is None:
                    continue
                hint = _build_style_hint(cell)
                if hint["is_header_hint"]:
                    style_hints.append(hint)

        sheets.append(
            {
                "name": ws.title,
                "used_range": {
                    "min_row": min_row,
                    "max_row": max_row,
                    "min_col": min_col,
                    "max_col": max_col,
                },
                "data_sample": data_sample,
                "merged_cells": merged_cells,
                "comments": comments,
                "style_hints": style_hints,
            }
        )

    data = {"file_path": file_path, "sheets": sheets}

    return json.dumps({"status": "success", "data": data}, ensure_ascii=False)
