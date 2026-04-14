"""
DynamoDB 검색 툴
config/db_tables.yaml 의 테이블 정보를 읽어 자동 쿼리
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "config" / "db_tables.yaml"


def _load_table_configs() -> dict[str, dict]:
    """db_tables.yaml에서 테이블 설정 로드. {table_name: config} 딕셔너리 반환."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {t["table_name"]: t for t in data.get("tables", [])}


def dynamodb_search(
    table_name: str,
    conditions: dict | None = None,
    hours: int | None = None,
) -> str:
    """
    DynamoDB 테이블을 검색한다.

    Args:
        table_name: 조회할 테이블명 (db_tables.yaml에 등록된 이름)
        conditions: 추가 검색 조건 dict (없으면 테이블 기본값 사용)
        hours: 시간 범위 오버라이드 (없으면 테이블 default_hours 사용)

    Returns:
        JSON 문자열 {status, data: DataFrame records, row_count}
    """
    try:
        from dynamodb import DynamoDB, load_column_info, build_expressions, items2df
    except ImportError:
        return json.dumps({
            "status": "error",
            "error_code": "MODULE_NOT_FOUND",
            "message": "dynamodb 모듈을 찾을 수 없습니다.",
        }, ensure_ascii=False)

    try:
        table_configs = _load_table_configs()

        if table_name not in table_configs:
            available = list(table_configs.keys())
            return json.dumps({
                "status": "error",
                "error_code": "UNKNOWN_TABLE",
                "message": f"'{table_name}'은 등록되지 않은 테이블입니다. 사용 가능: {available}",
            }, ensure_ascii=False)

        cfg = table_configs[table_name]
        custom_columns = cfg.get("custom_columns", [])
        time_col = cfg.get("time_column")
        default_hours = hours or cfg.get("default_hours")

        # 시간 조건 자동 생성
        final_conditions = dict(conditions or {})
        if time_col and default_hours and time_col not in final_conditions:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=default_hours)
            final_conditions[time_col] = {
                "from": start_time.strftime("%Y%m%d%H%M%S"),
                "to": end_time.strftime("%Y%m%d%H%M%S"),
            }

        # DynamoDB 연결
        db = DynamoDB(
            access_key=os.getenv("AccessKey"),
            secret_key=os.getenv("SecretKey"),
            endpoint=os.getenv("DYNAMODB_ENDPOINT"),
        )
        endpoint = db.endpoint
        schema = load_column_info(table_name=table_name, force={"end_point": endpoint})

        expressions = build_expressions(
            table_name=table_name,
            scheme=schema,
            custom_columns=custom_columns,
            conditions=final_conditions,
            conditions_from_catalog=True,
            limit=True,
        )

        response = db.query(**expressions)
        items = response.get("Items", [])

        df = items2df(items, schema, custom_columns=custom_columns, conditions=final_conditions)

        return json.dumps({
            "status": "success",
            "table": table_name,
            "row_count": len(df),
            "columns": list(df.columns),
            "data": df.head(200).to_dict(orient="records"),
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error_code": "QUERY_FAILED",
            "message": str(e),
        }, ensure_ascii=False)


def list_available_tables() -> str:
    """등록된 테이블 목록과 설명 반환."""
    try:
        configs = _load_table_configs()
        tables = [
            {
                "table_name": name,
                "description": cfg.get("description", ""),
                "columns": cfg.get("custom_columns", []),
                "time_column": cfg.get("time_column"),
                "default_hours": cfg.get("default_hours"),
            }
            for name, cfg in configs.items()
        ]
        return json.dumps({"status": "success", "tables": tables}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
