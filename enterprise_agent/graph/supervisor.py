from __future__ import annotations
import json
from langchain_core.messages import SystemMessage, HumanMessage
from enterprise_agent.graph.state import AgentState
from enterprise_agent.core.tracer import Tracer

SUPERVISOR_PROMPT = """당신은 사내 AI 에이전트의 Supervisor입니다.
사용자 메시지를 분석하여 intent를 JSON으로 반환하세요.

[Intent 종류]
- excel_analysis   : 엑셀 파일 분석 (통계, 이상값, 트렌드)
- excel_compare    : 두 엑셀 파일 버전 비교
- excel_read       : 엑셀 파일 내용 읽기/질문 답변
- mes_query        : MES 생산/품질 데이터 조회
- report           : 보고서 생성
- rag              : 문서 검색 및 Q&A
- unknown          : 판단 불가

[반환 형식] JSON만:
{
  "intent": "excel_analysis",
  "confidence": 0.95,
  "reasoning": "사용자가 엑셀 파일 분석을 요청함",
  "files_needed": true,
  "key_entities": ["이상값", "측정 데이터"]
}"""


def supervisor_node(state: AgentState, llm, tracer: Tracer) -> AgentState:
    with tracer.span("supervisor", state["messages"][-1].content[:50]) as span:
        response = llm.invoke([
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=state["messages"][-1].content),
        ])

        try:
            result = json.loads(response.content)
            intent = result.get("intent", "unknown")
        except Exception:
            # JSON 파싱 실패 시 텍스트에서 intent 추출 시도
            content = response.content.lower()
            if "excel" in content or "엑셀" in content:
                intent = "excel_analysis"
            elif "mes" in content:
                intent = "mes_query"
            else:
                intent = "unknown"

        span["output_summary"] = f"intent={intent}"

        return {
            **state,
            "intent": intent,
            "trace": state["trace"] + [{
                "trace_id": span["trace_id"],
                "node": "supervisor",
                "timestamp": span["timestamp"],
                "duration_ms": span.get("duration_ms", 0),
                "input_summary": state["messages"][-1].content[:100],
                "output_summary": f"intent={intent}",
                "error": span.get("error"),
            }],
        }
