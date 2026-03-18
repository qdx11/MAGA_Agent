from __future__ import annotations
import time
import uuid
from typing import Annotated, List, Literal, Optional
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class UserContext(TypedDict):
    user_id: str
    team: str
    role: Literal["admin", "analyst", "viewer"]
    permissions: List[str]  # ["excel:read", "mes:query", ...]


class TraceEntry(TypedDict):
    trace_id: str
    node: str
    timestamp: float
    duration_ms: float
    input_summary: str
    output_summary: str
    error: Optional[str]


class StepResult(TypedDict):
    step: int
    tool: str
    status: Literal["success", "error"]
    result: dict
    strategy: Optional[str]
    timestamp: float


class CriticFeedback(TypedDict):
    score: float
    passed: bool
    root_cause: str
    fix_instruction: dict   # 구조화된 JSON
    tools_to_rerun: List[str]
    early_stop: bool


class AgentState(TypedDict):
    # 기본
    messages: Annotated[list, add_messages]
    intent: str
    plan: Optional[dict]
    files: List[str]
    final_answer: Optional[str]

    # 실행 상태
    current_step: int
    needs_replan: bool
    retry_count: int
    early_stopped: bool

    # 결과 관리
    tool_results: List[StepResult]
    tool_results_summary: Optional[str]  # sliding window 요약

    # 엑셀 전용
    excel_schema: Optional[dict]
    matched_schema: Optional[str]

    # v4 신규
    user_context: UserContext
    trace: List[TraceEntry]
    critic_feedback: Optional[CriticFeedback]
    session_id: str


def make_default_state(
    message: str,
    files: List[str] = [],
    user_id: str = "demo",
    role: Literal["admin", "analyst", "viewer"] = "analyst",
    permissions: Optional[List[str]] = None,
) -> AgentState:
    """초기 State 생성 헬퍼"""
    from langchain_core.messages import HumanMessage
    return AgentState(
        messages=[HumanMessage(content=message)],
        intent="",
        plan=None,
        files=files,
        final_answer=None,
        current_step=0,
        needs_replan=False,
        retry_count=0,
        early_stopped=False,
        tool_results=[],
        tool_results_summary=None,
        excel_schema=None,
        matched_schema=None,
        user_context=UserContext(
            user_id=user_id,
            team="default",
            role=role,
            permissions=permissions or ["excel:read", "mes:query"],
        ),
        trace=[],
        critic_feedback=None,
        session_id=str(uuid.uuid4())[:8],
    )
