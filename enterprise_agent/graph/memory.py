from __future__ import annotations
import json
from enterprise_agent.graph.state import AgentState
from enterprise_agent.core.tracer import Tracer

# 간단한 인메모리 캐시 (세션 내 재파싱 방지)
_schema_cache: dict = {}


def memory_node(state: AgentState, tracer: Tracer) -> AgentState:
    """
    LLM 없음 — 코드 로직만.
    1. 이전에 파싱한 파일이면 캐시에서 excel_schema 복원
    2. Schema Registry와 매칭 시도
    """
    with tracer.span("memory") as span:
        updates = {}

        # 캐시 히트 확인
        files = state.get("files", [])
        for f in files:
            if f in _schema_cache:
                updates["excel_schema"] = _schema_cache[f]
                span["output_summary"] = f"cache_hit: {f}"
                break

        # Schema Registry 매칭 (excel_schema가 이미 있을 때)
        excel_schema = state.get("excel_schema") or updates.get("excel_schema")
        if excel_schema:
            matched = _try_match_schema(excel_schema)
            if matched:
                updates["matched_schema"] = matched
                span["output_summary"] = (span.get("output_summary", "") + f" schema_match={matched}").strip()

        if not span.get("output_summary"):
            span["output_summary"] = "no_cache"

        return {**state, **updates}


def cache_schema(file_path: str, schema: dict):
    """ExcelStructureParser 결과를 캐시에 저장 (Executor에서 호출)"""
    _schema_cache[file_path] = schema


def _try_match_schema(excel_schema: dict) -> str | None:
    """
    파싱된 엑셀 구조를 등록된 Schema와 매칭.
    유사도 0.8 이상이면 매칭 성공.
    """
    import difflib
    from pathlib import Path
    import yaml

    schema_dir = Path(__file__).parent.parent.parent / "config" / "excel_schemas"
    if not schema_dir.exists():
        return None

    # 파싱된 헤더 추출
    parsed_headers = []
    for sheet in excel_schema.get("sheets", []):
        sample = sheet.get("data_sample", [])
        if sample:
            parsed_headers.extend([str(h) for h in sample[0] if h is not None])

    best_name, best_score = None, 0.0
    for path in schema_dir.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        expected = schema.get("expected_headers", [])
        if not expected:
            continue
        score = difflib.SequenceMatcher(
            None, sorted(parsed_headers), sorted(expected)
        ).ratio()
        if score > best_score:
            best_score, best_name = score, schema.get("name")

    return best_name if best_score >= 0.8 else None
