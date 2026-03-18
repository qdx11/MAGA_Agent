"""
MAGA Agent 실행 예시.

실행:
  pip install -r requirements.txt
  cp .env.example .env   # API 키 설정
  python run_example.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from enterprise_agent.core.llm_client import create_llm
from enterprise_agent.graph.builder import build_graph
from enterprise_agent.graph.state import make_default_state


def run(message: str, files: list = []):
    llm = create_llm()
    graph, tracer = build_graph(llm, session_id="demo")

    state = make_default_state(
        message=message,
        files=files,
        user_id="demo_user",
        role="analyst",
        permissions=["excel:read", "mes:query"],
    )

    config = {"configurable": {"thread_id": "demo"}}
    final = graph.invoke(state, config=config)

    print("\n" + "="*60)
    print("📊 최종 답변:")
    print("="*60)
    print(final.get("final_answer", "답변 없음"))
    print(tracer.summary())
    return final


if __name__ == "__main__":
    run(
        message="이 엑셀 파일의 측정 데이터를 분석해줘. 이상값이 있는지 확인해줘.",
        files=["./data/measurement_v1.xlsx"],
    )
