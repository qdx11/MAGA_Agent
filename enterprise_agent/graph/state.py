from typing import Annotated, List, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages


class UserContext(TypedDict):
    user_id: str
    team: str
    role: Literal["admin", "analyst", "viewer"]
    permissions: List[str]


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
    fix_instruction: dict
    tools_to_rerun: List[str]
    early_stop: bool


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str
    plan: Optional[dict]
    excel_schema: Optional[dict]
    files: List[str]

    user_context: UserContext
    tool_results: List[StepResult]
    tool_results_summary: Optional[str]
    trace: List[TraceEntry]

    critic_feedback: Optional[CriticFeedback]
    retry_count: int
    early_stopped: bool
    matched_schema: Optional[str]
    final_answer: Optional[str]

    current_step: int
    needs_replan: bool
    session_id: str
