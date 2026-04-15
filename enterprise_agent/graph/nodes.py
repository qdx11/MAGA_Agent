from __future__ import annotations
import json
from langchain_core.messages import SystemMessage, HumanMessage
from enterprise_agent.graph.state import AgentState, CriticFeedback
from enterprise_agent.core.tool_registry import get_registry
from enterprise_agent.core.tracer import Tracer
from enterprise_agent.graph.json_utils import extract_json
from enterprise_agent.core.context_loader import inject


# ══════════════════════════════════════════════════════
# Step Guard — LLM 없음, 코드 로직으로 분기
# ══════════════════════════════════════════════════════

def step_guard(state: AgentState) -> str:
    """
    매 스텝 후 실행. 다음 목적지 결정.
    반환값: "executor" | "replanner" | "critic" | "formatter"
    """
    if state.get("early_stopped"):
        return "formatter"

    plan = state.get("plan", {})
    total_steps = plan.get("total_steps", 0)
    current_step = state.get("current_step", 0)

    # 플랜이 없거나 스텝이 0개 → 바로 formatter (일반 대화)
    if total_steps == 0:
        return "formatter"

    # 안전 상한: 최대 15 스텝 초과 시 강제 종료
    MAX_STEPS = 15
    if current_step >= MAX_STEPS:
        return "formatter"

    # 모든 스텝 완료 → critic
    if current_step >= total_steps:
        return "critic"

    tool_results = state.get("tool_results", [])
    if not tool_results:
        return "executor"

    last = tool_results[-1]

    if last["status"] == "error":
        early_stop = last["result"].get("early_stop", False)

        if early_stop:
            return "formatter"

        # 재시도 횟수 초과
        if state["retry_count"] >= 3:
            return "formatter"

        return "replanner"

    return "executor"


# ══════════════════════════════════════════════════════
# Replanner — 중간 실패 시 남은 플랜 재구성
# ══════════════════════════════════════════════════════

REPLANNER_PROMPT = """당신은 AI 에이전트의 Replanner입니다.
이전 스텝이 실패했습니다. 실패 정보와 남은 스텝을 보고 대안 계획을 수립하세요.

[재시도 전략 가이드]
- adjust_parameters: 파라미터를 수정하여 재시도
- try_vision_fallback: 파일을 이미지로 변환 후 Vision 분석
- aggregate_only: 원본 분석 포기, 집계 결과만 반환
- skip: 해당 툴 건너뛰고 다음 진행

[출력 규칙] — 반드시 준수
- 반드시 raw JSON만 반환하세요. 설명 텍스트, 마크다운, 코드블록(```) 절대 금지.
- 첫 글자가 { 이어야 합니다.
- 각 step에 반드시 "tool" 키가 있어야 합니다. "action", "parameters" 같은 키 사용 금지.

{"steps": [{"step":1, "tool":"ToolName", "reason":"이유", "params":{}}], "total_steps": N}"""


def replanner_node(state: AgentState, llm, tracer: Tracer) -> AgentState:
    with tracer.span("replanner") as span:
        if state["retry_count"] >= 3:
            span["output_summary"] = "max_retry_exceeded"
            return {**state, "early_stopped": True}

        tool_results = state.get("tool_results", [])
        if not tool_results:
            return state

        last_failure = tool_results[-1]
        failed_tool = last_failure["tool"]
        error_info = last_failure["result"]

        registry = get_registry()
        available_strategies = registry.retry_strategies_for(failed_tool)
        retry_count = state["retry_count"]
        strategy = (
            available_strategies[retry_count]
            if retry_count < len(available_strategies)
            else "skip"
        )

        remaining_steps = state["plan"]["steps"][state["current_step"]:]

        if strategy == "skip":
            new_steps = [s for s in remaining_steps if s["tool"] != failed_tool]
            span["output_summary"] = f"skip {failed_tool}"
        else:
            try:
                prompt = f"""실패 정보:
- 툴: {failed_tool}
- 에러: {json.dumps(error_info, ensure_ascii=False)}
- 적용 전략: {strategy}
- 남은 스텝: {json.dumps(remaining_steps, ensure_ascii=False)}

위 전략을 반영하여 남은 스텝을 재구성하세요."""

                response = llm.invoke([
                    SystemMessage(content=inject(REPLANNER_PROMPT)),
                    HumanMessage(content=prompt),
                ])
                new_plan = extract_json(response.content)
                new_steps = new_plan["steps"]
                span["output_summary"] = f"strategy={strategy} new_steps={len(new_steps)}"
            except Exception as e:
                # 재계획 실패 → skip 전략으로 폴백
                new_steps = [s for s in remaining_steps if s["tool"] != failed_tool]
                span["output_summary"] = f"replan_failed→skip: {e}"

        updated_plan = {
            "steps": state["plan"]["steps"][:state["current_step"]] + new_steps,
            "total_steps": state["current_step"] + len(new_steps),
        }

        return {
            **state,
            "plan": updated_plan,
            "retry_count": state["retry_count"] + 1,
        }


# ══════════════════════════════════════════════════════
# Critic — 최종 품질 평가
# ══════════════════════════════════════════════════════

EARLY_STOP_CAUSES = {
    "data_corrupted",
    "file_not_readable",
    "schema_mismatch",
    "permission_denied",
}

CRITIC_PROMPT = """당신은 AI 에이전트 실행 결과의 품질을 평가합니다.

[평가 기준]
- 0.9 이상: 완벽
- 0.7~0.9: 양호, 통과
- 0.7 미만: 재실행 필요

[fix_instruction 구조] — 자유 텍스트 금지:
{"target_tools": ["ToolName"], "action": "adjust_parameters | retry | skip", "params_override": {"param": "value"}}

[root_cause 종류]
header_detection_failed / data_corrupted / wrong_sheet /
incomplete_analysis / wrong_tool_used / missing_data /
file_not_readable / schema_mismatch / permission_denied

[출력 규칙] — 반드시 준수
- 반드시 raw JSON만 반환하세요. 설명 텍스트, 마크다운, 코드블록(```) 절대 금지.
- 첫 글자가 { 이어야 합니다.

{"score": 0.85, "passed": true, "root_cause": "", "fix_instruction": {}, "tools_to_rerun": [], "early_stop": false}"""


def critic_node(state: AgentState, llm, tracer: Tracer) -> AgentState:
    with tracer.span("critic") as span:
        summary = state.get("tool_results_summary", "없음")
        recent = state["tool_results"][-5:]

        context = f"""원래 질문: {state['messages'][0].content}
실행 이력 요약: {summary}
최근 툴 결과: {json.dumps([r for r in recent], ensure_ascii=False, default=str)[:3000]}
재시도 횟수: {state['retry_count']}
파일: {state.get('files', [])}"""

        try:
            response = llm.invoke([
                SystemMessage(content=inject(CRITIC_PROMPT)),
                HumanMessage(content=context),
            ])
            feedback: CriticFeedback = extract_json(response.content)
        except Exception:
            # 파싱 실패 시 기본값 (통과)
            feedback = CriticFeedback(
                score=0.7,
                passed=True,
                root_cause="",
                fix_instruction={},
                tools_to_rerun=[],
                early_stop=False,
            )

        # Early Stop 강제 (LLM이 놓쳐도 코드에서 잡음)
        if feedback.get("root_cause") in EARLY_STOP_CAUSES:
            feedback["early_stop"] = True

        span["output_summary"] = f"score={feedback.get('score')} passed={feedback.get('passed')}"
        return {**state, "critic_feedback": feedback}


def should_continue_after_critic(state: AgentState) -> str:
    feedback = state.get("critic_feedback", {})
    if not feedback:
        return "formatter"
    if feedback.get("early_stop") or feedback.get("passed") or state["retry_count"] >= 3:
        return "formatter"
    return "replanner"


# ══════════════════════════════════════════════════════
# Formatter — 최종 답변 생성
# ══════════════════════════════════════════════════════

FORMATTER_PROMPT = """당신은 AI 에이전트의 Formatter입니다.
툴 실행 결과를 사용자가 이해하기 쉬운 자연어 답변으로 정리하세요.

규칙:
- 수치는 소수점 2자리까지
- 이상값은 별도 강조
- 표 형태로 정리할 수 있으면 마크다운 표 사용
- 출처 데이터 명시 (어떤 파일, 어떤 시트)
- early_stopped=True인 경우 실패 이유를 친절하게 설명"""

CHAT_FORMATTER_PROMPT = """당신은 사내 AI 어시스턴트입니다.
사용자의 말에 자연스럽고 친근하게 한국어로 대화하세요.
엑셀 분석, MES 데이터 조회, 보고서 생성 등을 도울 수 있다고 안내할 수 있습니다.
불필요한 코드, 마크다운 블록 없이 자연스러운 텍스트로 답하세요."""


def formatter_node(state: AgentState, llm, tracer: Tracer) -> AgentState:
    with tracer.span("formatter") as span:
        tool_results = state.get("tool_results", [])
        early_stopped = state.get("early_stopped", False)
        intent = state.get("intent", "unknown")

        # 일반 대화 또는 툴 결과 없음 → 직접 대화 응답
        is_chat_mode = (intent == "general_chat") or (
            not tool_results and not early_stopped
        )

        if is_chat_mode:
            try:
                response = llm.invoke([
                    SystemMessage(content=inject(CHAT_FORMATTER_PROMPT)),
                    HumanMessage(content=state["messages"][-1].content),
                ])
                final_answer = response.content
            except Exception as e:
                final_answer = f"안녕하세요! 무엇을 도와드릴까요? (오류: {e})"
        else:
            context = f"""사용자 질문: {state['messages'][0].content}
조기 종료: {early_stopped}
툴 실행 결과:
{json.dumps(tool_results, ensure_ascii=False, default=str)[:4000]}
Critic 평가: {json.dumps(state.get('critic_feedback', {}), ensure_ascii=False)}"""

            try:
                response = llm.invoke([
                    SystemMessage(content=inject(FORMATTER_PROMPT)),
                    HumanMessage(content=context),
                ])
                final_answer = response.content
            except Exception as e:
                final_answer = f"답변 생성 중 오류가 발생했습니다: {e}"

        span["output_summary"] = f"mode={'chat' if is_chat_mode else 'tool'} {len(final_answer)}chars"
        return {**state, "final_answer": final_answer}
