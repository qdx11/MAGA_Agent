from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from enterprise_agent.core.llm_client import ResilientLLMClient
from enterprise_agent.core.tracer import Tracer
from enterprise_agent.graph.state import AgentState


MAX_RETRY = 3


def supervisor_node(state: AgentState, llm: ResilientLLMClient, tracer: Tracer) -> AgentState:
    """최소 구현: intent만 추출."""
    with tracer.span("supervisor"):
        last = state["messages"][-1]
        intent = getattr(last, "content", str(last))
        return {**state, "intent": intent}


def planner_node(state: AgentState, llm: ResilientLLMClient, tracer: Tracer) -> AgentState:
    """현재는 단일 ExcelStructureParser 스텝으로 고정된 기본 플랜."""
    with tracer.span("planner"):
        plan = {
            "steps": [
                {"step": 1, "tool": "ExcelStructureParser", "reason": "기본 구조 파악"}
            ],
            "total_steps": 1,
        }
        # 초기화 시 retry 관련 필드도 리셋
        return {
            **state,
            "plan": plan,
            "current_step": 0,
            "retry_count": 0,
            "early_stopped": False,
        }


def memory_node(state: AgentState, tracer: Tracer) -> AgentState:
    """v4에서 캐시/스키마 매칭이 들어갈 자리. 지금은 패스."""
    with tracer.span("memory"):
        return state


def executor_node(state: AgentState, tracer: Tracer) -> AgentState:
    """플랜의 현재 스텝 1개만 실행."""
    import json
    import time

    from enterprise_agent.core.tool_registry import registry

    with tracer.span("executor"):
        plan = state["plan"] or {"steps": [], "total_steps": 0}
        idx = state["current_step"]
        if idx >= plan["total_steps"]:
            return state

        step = plan["steps"][idx]
        tool_name = step["tool"]
        meta = registry.get(tool_name)

        # 간단 권한 체크 예시 (excel 그룹 read 권한)
        user = state["user_context"]
        if meta.group == "excel" and "excel:read" not in user.get("permissions", []):
            parsed = {
                "status": "error",
                "error_code": "PERMISSION_DENIED",
                "root_cause": "permission_denied",
                "early_stop": True,
                "message": "엑셀 읽기 권한이 없습니다.",
                "suggested_fix": "관리자에게 권한을 요청하세요.",
            }
        else:
            raw = (
                meta.invoke(file_path=state["files"][0])
                if state["files"]
                else meta.invoke(file_path="./data/dummy.xlsx")
            )
            parsed = json.loads(raw)

        result = {
            "step": idx,
            "tool": tool_name,
            "status": parsed.get("status", "success"),
            "result": parsed,
            "strategy": None,
            "timestamp": time.time(),
        }
        results = state["tool_results"] + [result]
        return {**state, "tool_results": results, "current_step": idx + 1}


def step_guard(state: AgentState) -> str:
    """
    매 스텝 실행 후 분기.
    - 모든 스텝 완료 → formatter
    - retry 상한 도달 또는 early_stopped → formatter
    - 그 외 → executor 계속
    """
    # retry 상한 체크 (Replanner 도입 시에도 여기서 공통으로 막아줌)
    if state["retry_count"] >= MAX_RETRY or state.get("early_stopped"):
        return "formatter"

    plan = state.get("plan") or {"steps": [], "total_steps": 0}
    if state["current_step"] >= plan.get("total_steps", 0):
        return "formatter"
    return "executor"


def formatter_node(state: AgentState, llm: ResilientLLMClient, tracer: Tracer) -> AgentState:
    """최종 응답 생성. 지금은 간단한 요약 문자열만."""
    with tracer.span("formatter"):
        steps = len(state["tool_results"])
        last_status = state["tool_results"][-1]["status"] if state["tool_results"] else "none"
        summary = f"steps={steps}, last_status={last_status}"
        return {**state, "final_answer": summary}


def build_graph(llm: ResilientLLMClient, session_id: str):
    tracer = Tracer(session_id)
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", lambda s: supervisor_node(s, llm, tracer))
    graph.add_node("planner", lambda s: planner_node(s, llm, tracer))
    graph.add_node("memory", lambda s: memory_node(s, tracer))
    graph.add_node("executor", lambda s: executor_node(s, tracer))
    graph.add_node("step_guard", lambda s: s)
    graph.add_node("formatter", lambda s: formatter_node(s, llm, tracer))

    graph.set_entry_point("supervisor")
    graph.add_edge("supervisor", "planner")
    graph.add_edge("planner", "memory")
    graph.add_edge("memory", "executor")
    graph.add_edge("executor", "step_guard")

    graph.add_conditional_edges(
        "step_guard",
        step_guard,
        {
            "executor": "executor",
            "formatter": "formatter",
        },
    )

    graph.add_edge("formatter", END)

    return graph.compile(checkpointer=MemorySaver())
