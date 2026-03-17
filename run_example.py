from langchain.schema import HumanMessage

from enterprise_agent.core.llm_client import ResilientLLMClient
from enterprise_agent.graph.builder import build_graph
from enterprise_agent.graph.state import AgentState


def main() -> None:
    llm = ResilientLLMClient(
        primary_base_url="https://api.openai.com/v1",  # 사내 게이트웨이로 교체 예정
        primary_api_key="YOUR_API_KEY",
        primary_model="gpt-4o-mini",
    )

    graph = build_graph(llm, session_id="demo")

    state: AgentState = {
        "messages": [HumanMessage(content="엑셀 구조 한번 확인해줘")],
        "intent": "",
        "plan": None,
        "excel_schema": None,
        "files": ["./data/sample.xlsx"],
        "user_context": {
            "user_id": "demo",
            "team": "demo",
            "role": "analyst",
            "permissions": ["excel:read"],
        },
        "tool_results": [],
        "tool_results_summary": None,
        "trace": [],
        "critic_feedback": None,
        "retry_count": 0,
        "early_stopped": False,
        "matched_schema": None,
        "final_answer": None,
        "current_step": 0,
        "needs_replan": False,
        "session_id": "demo",
    }

    app = graph.compile()
    final = app.invoke(state)
    print("FINAL STATE:")
    print(final["final_answer"])


if __name__ == "__main__":
    main()
