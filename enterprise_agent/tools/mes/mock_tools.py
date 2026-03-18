import json
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Optional
import os


class MESQueryInput(BaseModel):
    start_date: str = Field(..., description="조회 시작일 YYYY-MM-DD")
    end_date: str = Field(..., description="조회 종료일 YYYY-MM-DD. 최대 90일.")
    line_id: str = Field(..., description="생산 라인 ID. 예: LINE-01")

@tool(args_schema=MESQueryInput)
def mes_query_tool(start_date: str, end_date: str, line_id: str) -> str:
    """
    [기능] 사내 MES에서 생산/품질 데이터를 조회합니다.
    [선행 조건] 없음.
    [사용 시점] 생산 현황, 불량률 조회 요청 시. 날짜 범위 반드시 확인.
    [반환값] JSON {status, data: {records, row_count, line_id, date_range}}
    """
    from datetime import datetime
    MAX_DAYS = int(os.getenv("MES_MAX_DATE_RANGE_DAYS", 90))

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return json.dumps({
            "status": "error", "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "invalid_date_format", "early_stop": False,
            "message": "날짜 형식이 올바르지 않습니다.",
            "suggested_fix": "YYYY-MM-DD 형식을 사용하세요."
        }, ensure_ascii=False)

    if (end - start).days > MAX_DAYS:
        return json.dumps({
            "status": "error", "error_code": "DATE_RANGE_EXCEEDED",
            "root_cause": "date_range_too_large", "early_stop": True,
            "message": f"최대 {MAX_DAYS}일 범위만 허용됩니다.",
            "suggested_fix": f"날짜 범위를 {MAX_DAYS}일 이내로 줄여주세요."
        }, ensure_ascii=False)

    # TODO: 실제 DynamoDB 쿼리로 교체
    return json.dumps({
        "status": "success",
        "data": {
            "records": [
                {"date": "2024-01-01", "line_id": line_id, "production": 150, "defect": 3, "defect_rate": 0.02},
                {"date": "2024-01-02", "line_id": line_id, "production": 142, "defect": 5, "defect_rate": 0.035},
            ],
            "row_count": 2,
            "line_id": line_id,
            "date_range": f"{start_date} ~ {end_date}",
        }
    }, ensure_ascii=False)


class MESFormatterInput(BaseModel):
    mes_result: str = Field(..., description="MESQueryTool 결과 JSON")

@tool(args_schema=MESFormatterInput)
def mes_data_formatter(mes_result: str) -> str:
    """
    [기능] MES 조회 결과를 분석용 포맷으로 정제합니다.
    [선행 조건] MESQueryTool 결과 필요.
    [사용 시점] MES 조회 후 항상 실행.
    [반환값] JSON {status, data: {summary, formatted_records}}
    """
    try:
        data = json.loads(mes_result).get("data", {})
        records = data.get("records", [])
        avg_defect = sum(r.get("defect_rate", 0) for r in records) / len(records) if records else 0

        return json.dumps({
            "status": "success",
            "data": {
                "summary": {
                    "total_records": len(records),
                    "avg_defect_rate": round(avg_defect, 4),
                    "line_id": data.get("line_id"),
                    "date_range": data.get("date_range"),
                },
                "formatted_records": records,
            }
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error", "error_code": "TOOL_EXECUTION_ERROR",
            "root_cause": "unexpected_error", "early_stop": False,
            "message": str(e),
        }, ensure_ascii=False)
