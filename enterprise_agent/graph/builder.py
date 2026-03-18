from __future__ import annotations
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from enterprise_agent.graph.state import AgentState
from enterprise_agent.graph.supervisor import supervisor_node
from enterprise_agent.graph.planner import planner_node
from enterprise_agent.graph.memory import memory_node
from enterprise_agent.graph.executor import executor_node
from enterprise_agent.graph.nodes import (
    step_guard,
    replanner_node,
    critic_node,
    should_continue_after_critic,
    formatter_node,
)

from enterprise_agent.core.tracer import Tracer


def build_graph(llm, session_id: str = "default"):
    tracer = Tracer(session_id)

    graph = StateGraph(AgentState)

    # 노드 등록
    #                                          LLM 사용
    graph.add_node("supervisor", lambda s: supervisor_node(s, llm, tracer))   # O
    graph.add_node("planner",    lambda s: planner_node(s, llm, tracer))      # O
    graph.add_node("memory",     lambda s: memory_node(s, tracer))            # X
    graph.add_node("executor",   lambda s: executor_node(s, tracer))          # X
    graph.add_node("step_guard", lambda s: s)   # passthrough, 분기만           # X
    graph.add_node("replanner",  lambda s: replanner_node(s, llm, tracer))    # O
    graph.add_node("critic",     lambda s: critic_node(s, llm, tracer))       # O
    graph.add_node("formatter",  lambda s: formatter_node(s, llm, tracer))    # O

    # 엣지 연결
    graph.set_entry_point("supervisor")
    graph.add_edge("supervisor", "planner")
    graph.add_edge("planner",    "memory")
    graph.add_edge("memory",     "executor")
    graph.add_edge("executor",   "step_guard")

    # Step Guard 분기
    graph.add_conditional_edges("step_guard", step_guard, {
        "executor":  "executor",    # 다음 스텝 계속
        "replanner": "replanner",   # 중간 실패 → 재계획
        "critic":    "critic",      # 전체 완료 → 평가
        "formatter": "formatter",   # early_stop → 종료
    })

    graph.add_edge("replanner", "executor")

    # Critic 분기
    graph.add_conditional_edges("critic", should_continue_after_critic, {
        "replanner": "replanner",   # 품질 미달 → 재계획
        "formatter": "formatter",   # 통과 or 종료
    })

    graph.add_edge("formatter", END)

    return graph.compile(checkpointer=MemorySaver()), tracer
