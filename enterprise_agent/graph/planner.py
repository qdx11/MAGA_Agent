from __future__ import annotations
import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage
from enterprise_agent.graph.state import AgentState
from enterprise_agent.core.tool_registry import get_registry
from enterprise_agent.core.tracer import Tracer
from enterprise_agent.graph.json_utils import extract_json
from enterprise_agent.core.context_loader import inject

logger = logging.getLogger(__name__)


class PlanValidationError(Exception):
    pass


def validate_plan(plan: dict) -> None:
    """레지스트리 기반 플랜 검증. 하드코딩 없음."""
    registry = get_registry()
    available = registry.available_tools_map()
    all_tools = [t for group in available.values() for t in group]
    prereq_rules = registry.prerequisite_rules()
    step_tools = [step.get("tool", step.get("name", "")) for step in plan.get("steps", [])]

    for i, step in enumerate(plan["steps"]):
        tool = step.get("tool") or step.get("name", "")

        # 존재하지 않는 툴
        if tool not in all_tools:
            raise PlanValidationError(
                f"'{tool}'은 등록되지 않은 툴입니다. 사용 가능: {all_tools}"
            )

        # 선행 조건 미충족
        for prereq in prereq_rules.get(tool, []):
            if prereq not in step_tools[:i]:
                raise PlanValidationError(
                    f"'{tool}' 실행 전에 '{prereq}'가 먼저 실행되어야 합니다."
                )

    # 그룹 진입점 검증: 선행조건 없는 툴이 그룹의 첫 번째여야 함
    for group, tools in available.items():
        group_tools_in_plan = [t for t in step_tools if t in tools]
        if not group_tools_in_plan:
            continue
        entry_tools = [t for t in tools if not prereq_rules.get(t)]
        for et in entry_tools:
            if et in step_tools:
                for gt in group_tools_in_plan:
                    if prereq_rules.get(gt) and step_tools.index(gt) < step_tools.index(et):
                        raise PlanValidationError(
                            f"'{group}' 그룹은 '{et}'가 먼저 실행되어야 합니다."
                        )


def _safe_default_plan(state: AgentState) -> dict:
    """검증 실패 시 안전한 기본 플랜"""
    registry = get_registry()
    intent = state.get("intent", "")
    files = state.get("files", [])

    steps = []
    if files and any(f.endswith((".xlsx", ".xlsm", ".xls")) for f in files):
        excel_tools = registry.by_group("excel")
        # 선행 조건 없는 툴부터 필수 툴만
        required = [t for t in excel_tools if not t.prerequisites][:3]
        for i, t in enumerate(required):
            steps.append({"step": i+1, "tool": t.name, "reason": "기본 분석", "params": {}})
    elif "mes" in intent:
        mes_tools = registry.by_group("mes")
        entry = [t for t in mes_tools if not t.prerequisites]
        if entry:
            steps.append({"step": 1, "tool": entry[0].name, "reason": "MES 조회", "params": {}})

    return {"steps": steps, "total_steps": len(steps), "current_step": 0}


def build_planner_prompt(state: AgentState) -> str:
    registry = get_registry()
    tool_catalog = registry.tool_descriptions_for_planner()
    return f"""당신은 AI 에이전트의 실행 계획을 수립하는 Planner입니다.

[사용 가능한 툴]
{tool_catalog}

[규칙] — 반드시 준수
1. 위 목록에 있는 툴 이름만 사용하세요. 목록에 없는 툴은 절대 사용 금지.
2. 툴 이름은 대소문자 구분하여 정확히 입력하세요 (예: ExcelStructureParser).
3. 선행 조건이 있는 툴은 해당 툴 이후에 배치하세요.
4. 불필요한 툴은 포함하지 마세요.
5. params는 현재 알 수 있는 정보만 포함하세요.
6. "tool" 키 이름을 정확히 사용하세요. "tool_name", "name" 등 다른 키 이름 금지.

[엑셀 분석 특별 규칙]
- ExcelStructureParser 실행 후, 반환된 row_index를 직접 분석하여 헤더 구조를 판단하세요.
- row_index의 각 행을 보고 결정:
  * preview 값이 모두 동일 → 병합 타이틀 행 (헤더 아님)
  * "작성부서:", "기준:", "담당자:" 등 메타 키워드 → 메타 행 (헤더 아님)
  * 날짜("2024-01"), 항목명이 열마다 다양하게 있는 행 → 실제 헤더
  * value_type이 "mixed"이고 숫자 포함 → 데이터 행
- CrossTableFlattener 호출 시 header_rows, data_start_row, id_col_count를 직접 params에 포함하세요.

[현재 파일 목록]
{state.get('files', [])}

[반환 형식] JSON만:
{{
  "steps": [
    {{"step": 1, "tool": "ToolName", "reason": "이유", "params": {{}}}},
    ...
  ],
  "total_steps": N
}}"""


def planner_node(state: AgentState, llm, tracer: Tracer) -> AgentState:
    with tracer.span("planner") as span:
        prompt = build_planner_prompt(state)
        max_attempts = 3
        last_error = ""

        for attempt in range(max_attempts):
            try:
                full_prompt = prompt
                if last_error:
                    full_prompt += f"\n\n[이전 오류 수정 필요]: {last_error}"

                response = llm.invoke([
                    SystemMessage(content=inject(full_prompt)),
                    HumanMessage(content=(
                        f"intent: {state['intent']}\n"
                        f"사용자 요청: {state['messages'][-1].content}"
                    )),
                ])

                plan = extract_json(response.content)
                plan["current_step"] = 0

                logger.debug("LLM 플랜 결과: %s", json.dumps(plan, ensure_ascii=False))
                validate_plan(plan)
                span["output_summary"] = f"steps={plan['total_steps']}"

                return {
                    **state,
                    "plan": plan,
                    "current_step": 0,
                    "trace": state["trace"] + [{
                        "trace_id": span["trace_id"],
                        "node": "planner",
                        "timestamp": span["timestamp"],
                        "duration_ms": span.get("duration_ms", 0),
                        "input_summary": f"intent={state['intent']}",
                        "output_summary": f"steps={plan['total_steps']}",
                        "error": None,
                    }],
                }

            except PlanValidationError as e:
                last_error = str(e)
                if attempt == max_attempts - 1:
                    # 3번 다 실패 → 안전 기본 플랜
                    plan = _safe_default_plan(state)
                    span["output_summary"] = f"fallback_plan steps={plan['total_steps']}"
                    return {**state, "plan": plan, "current_step": 0}

            except Exception as e:
                last_error = f"JSON 파싱 오류: {e}"
                if attempt == max_attempts - 1:
                    plan = _safe_default_plan(state)
                    return {**state, "plan": plan, "current_step": 0}
