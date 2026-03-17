# 🏭 Enterprise AI Agent v3.0

## 이번에 적용한 것들
1. Plan 하드가드 (LLM 검증 노드 대신 코드 레벨)
2. Critic 피드백 구조 강화 (Self-Reflection)
3. Early Stopping (데이터 문제면 재실행 안 함)
4. 재시도마다 전략 변경
5. Schema 사전 등록 시스템

---

## 📁 변경된 파일 구조

```
enterprise_agent/
├── graph/
│   ├── state.py           # ← 피드백 필드 추가
│   ├── planner.py         # ← 하드가드 추가
│   ├── executor.py        # ← 재시도 전략 변경 로직
│   ├── critic.py          # ← Self-Reflection 강화
│   └── early_stopper.py   # ← NEW
├── tools/
│   └── excel/
│       ├── structure_parser.py
│       └── schema_registry.py  # ← NEW: 사전 등록 시스템
└── config/
    └── excel_schemas/          # ← NEW: 양식 등록 폴더
        ├── measurement_v1.json
        └── mes_output_v2.json
```

---

## 1. State 업데이트 (`graph/state.py`)

```python
from typing import TypedDict, Annotated, List, Optional, Literal
from langgraph.graph.message import add_messages

class CriticFeedback(TypedDict):
    score: float
    passed: bool
    root_cause: str          # "헤더 감지 실패" | "데이터 오염" | "쿼리 범위 초과" 등
    fix_instruction: str     # Executor에게 전달할 수정 지침
    tools_to_rerun: List[str]  # 전체 재실행 말고 특정 툴만
    early_stop: bool         # True면 재실행해도 의미없음

class RetryStrategy(TypedDict):
    attempt: int
    strategy: Literal["default", "adjust_header_row", "try_vision_fallback", "aggregate_only"]
    # 재시도마다 다른 전략 사용

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str
    plan: Optional[dict]
    excel_schema: Optional[dict]
    tool_results: List[dict]
    files: List[str]
    
    # v3 추가 필드
    critic_feedback: Optional[CriticFeedback]   # 구체적인 피드백
    retry_strategy: Optional[RetryStrategy]      # 재시도 전략
    retry_count: int
    early_stopped: bool                          # Early Stop 여부
    matched_schema: Optional[str]               # 매칭된 사전 등록 스키마 이름
    final_answer: Optional[str]
```

---

## 2. Plan 하드가드 (`graph/planner.py`)

```python
# LLM 검증 노드 대신 코드 레벨에서 규칙 강제
# → 빠르고, LLM hallucination 없음

AVAILABLE_TOOLS = {
    "excel": [
        "ExcelStructureParser",
        "SheetMapper",
        "HeaderDetector",
        "DataTypeInferrer",
        "CrossTableFlattener",
        "ExcelCompareTool",
        "OutlierDetector",
        "TrendAnalyzer",
    ],
    "pdf": ["PDFTextExtractor", "PDFVisionAnalyzer"],
    "mes": ["MESQueryTool", "MESDataFormatter", "ReportGenerator"],
    "rag": ["DocumentIndexer", "DocumentRetriever"],
}

# 순서 강제 규칙
PREREQUISITE_RULES = {
    "HeaderDetector":      ["ExcelStructureParser"],  # 반드시 구조파악 먼저
    "DataTypeInferrer":    ["ExcelStructureParser"],
    "CrossTableFlattener": ["ExcelStructureParser", "HeaderDetector"],
    "ExcelCompareTool":    ["ExcelStructureParser", "HeaderDetector", "DataTypeInferrer"],
    "OutlierDetector":     ["ExcelStructureParser", "HeaderDetector", "DataTypeInferrer"],
    "TrendAnalyzer":       ["ExcelStructureParser", "HeaderDetector", "DataTypeInferrer"],
    "MESDataFormatter":    ["MESQueryTool"],
    "ReportGenerator":     ["MESQueryTool", "MESDataFormatter"],
}

class PlanValidationError(Exception):
    pass

def validate_plan(plan: dict) -> None:
    """
    LLM이 만든 계획을 코드 레벨에서 검증.
    잘못됐으면 즉시 예외 발생 → Planner가 재생성.
    """
    all_tools = [t for group in AVAILABLE_TOOLS.values() for t in group]
    step_tools = [step["tool"] for step in plan["steps"]]

    for i, step in enumerate(plan["steps"]):
        tool = step["tool"]

        # 1. 존재하지 않는 툴 사용 금지
        if tool not in all_tools:
            raise PlanValidationError(
                f"'{tool}'은 존재하지 않는 툴입니다. "
                f"사용 가능: {all_tools}"
            )

        # 2. 선행 툴 없이 실행 금지
        prerequisites = PREREQUISITE_RULES.get(tool, [])
        executed_before = step_tools[:i]
        for prereq in prerequisites:
            if prereq not in executed_before:
                raise PlanValidationError(
                    f"'{tool}'을 실행하려면 '{prereq}'가 먼저 실행되어야 합니다."
                )

    # 3. 엑셀 관련 툴이 있으면 반드시 ExcelStructureParser 1번
    excel_tools_in_plan = [t for t in step_tools if t in AVAILABLE_TOOLS["excel"]]
    if excel_tools_in_plan and step_tools[0] != "ExcelStructureParser":
        raise PlanValidationError(
            "엑셀 작업은 반드시 ExcelStructureParser가 첫 번째여야 합니다."
        )

def planner_node(state: AgentState, llm) -> AgentState:
    max_attempts = 3
    for attempt in range(max_attempts):
        response = llm.invoke([
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=f"의도: {state['intent']}\n파일: {state['files']}\n요청: {state['messages'][-1].content}")
        ])
        
        import json
        plan = json.loads(response.content)
        plan["current_step"] = 0

        try:
            validate_plan(plan)  # 하드가드
            return {**state, "plan": plan}
        except PlanValidationError as e:
            if attempt == max_attempts - 1:
                # 3번 다 실패하면 기본 안전 플랜으로 폴백
                return {**state, "plan": _safe_default_plan(state)}
            # 실패 이유를 LLM에게 피드백해서 재생성
            PLANNER_PROMPT += f"\n\n[이전 계획 오류]: {str(e)}\n위 오류를 반드시 수정하세요."

def _safe_default_plan(state: AgentState) -> dict:
    """검증 실패 시 안전한 기본 플랜"""
    if state["files"] and any(f.endswith(".xlsx") for f in state["files"]):
        return {
            "steps": [
                {"step": 1, "tool": "ExcelStructureParser", "reason": "기본 구조 파악"},
                {"step": 2, "tool": "HeaderDetector", "reason": "헤더 감지"},
                {"step": 3, "tool": "DataTypeInferrer", "reason": "타입 추론"},
            ],
            "total_steps": 3,
            "current_step": 0
        }
    return {"steps": [], "total_steps": 0, "current_step": 0}
```

---

## 3. Critic Self-Reflection 강화 (`graph/critic.py`)

```python
from typing import Literal

# 실패 원인 분류 (Early Stop 결정에 사용)
EARLY_STOP_CAUSES = {
    "data_corrupted",      # 원본 데이터 자체가 오염
    "file_not_readable",   # 파일 손상
    "schema_mismatch",     # 등록된 스키마와 완전히 다른 구조
    "permission_denied",   # 권한 문제
}

CRITIC_PROMPT = """당신은 AI 에이전트 답변의 품질을 평가하는 비평가입니다.

[평가 기준]
- 0.9 이상: 완벽
- 0.7~0.9: 양호, 통과
- 0.7 미만: 재실행 필요

[실패 원인 분류] - root_cause는 반드시 아래 중 하나:
- "header_detection_failed": 헤더 행을 잘못 잡음
- "wrong_sheet_selected": 잘못된 시트 분석
- "data_range_too_large": 데이터 범위가 너무 넓음
- "data_corrupted": 원본 데이터 오염 (재실행해도 안 됨)
- "file_not_readable": 파일 손상 (재실행해도 안 됨)
- "schema_mismatch": 구조 파악 자체가 틀림
- "incomplete_analysis": 분석이 충분하지 않음
- "wrong_tool_used": 잘못된 툴 사용

[fix_instruction 작성 규칙]
- 구체적으로 작성 ("다시 해" 금지)
- 예: "HeaderDetector를 row=2가 아닌 row=3부터 재실행하세요"
- 예: "Sheet2가 아닌 Sheet1을 대상으로 분석하세요"

[tools_to_rerun]
- 전체 재실행 금지, 실패한 툴부터만 재실행
- 예: ["HeaderDetector", "DataTypeInferrer", "OutlierDetector"]

JSON으로만 응답:
{
  "score": 0.65,
  "passed": false,
  "root_cause": "header_detection_failed",
  "fix_instruction": "HeaderDetector를 row=3부터 재실행하세요. 현재 결과에서 헤더가 데이터로 포함되어 있습니다.",
  "tools_to_rerun": ["HeaderDetector", "DataTypeInferrer"],
  "early_stop": false
}
"""

def critic_node(state: AgentState, llm) -> AgentState:
    import json

    context = f"""
원래 질문: {state['messages'][0].content}
실행한 툴 순서: {[r['tool'] for r in state['tool_results']]}
각 툴 결과 요약: {str(state['tool_results'])[:3000]}
현재 재시도 횟수: {state['retry_count']}
"""
    response = llm.invoke([
        SystemMessage(content=CRITIC_PROMPT),
        HumanMessage(content=context)
    ])

    feedback: CriticFeedback = json.loads(response.content)

    # Early Stop 조건 강제 (LLM이 early_stop=False로 해도 원인이 해당되면 강제 종료)
    if feedback["root_cause"] in EARLY_STOP_CAUSES:
        feedback["early_stop"] = True

    return {**state, "critic_feedback": feedback}


def should_retry(state: AgentState) -> str:
    feedback = state["critic_feedback"]

    # Early Stop: 데이터 문제면 재실행 의미없음
    if feedback["early_stop"]:
        return "formatter"

    # 통과
    if feedback["passed"]:
        return "formatter"

    # 최대 재시도 초과
    if state["retry_count"] >= 3:
        return "formatter"

    # 재시도
    return "executor"
```

---

## 4. 재시도 전략 변경 (`graph/executor.py`)

```python
# 핵심: 매 재시도마다 다른 전략 사용
# 같은 방법으로 3번 하는 건 의미없음

RETRY_STRATEGIES = {
    1: "adjust_parameters",   # 1차: 파라미터 조정 (헤더 행 위치 등)
    2: "try_vision_fallback", # 2차: Vision 모델로 폴백
    3: "aggregate_only",      # 3차: 원본 분석 포기, 집계만
}

def executor_node(state: AgentState, tools: dict, llm) -> AgentState:
    feedback = state.get("critic_feedback")
    retry_count = state["retry_count"]

    # 재시도인 경우: 피드백 기반으로 실행
    if feedback and not feedback["passed"] and retry_count > 0:
        strategy = RETRY_STRATEGIES.get(retry_count, "aggregate_only")
        return _execute_with_strategy(state, tools, llm, feedback, strategy)

    # 최초 실행: 플랜대로
    return _execute_plan(state, tools, llm)


def _execute_with_strategy(state, tools, llm, feedback, strategy):
    """재시도 전략별 실행"""
    results = list(state["tool_results"])  # 기존 결과 유지

    if strategy == "adjust_parameters":
        # Critic의 fix_instruction을 LLM에게 전달해서 파라미터 재설정
        for tool_name in feedback["tools_to_rerun"]:
            tool = tools[tool_name]
            
            # fix_instruction 기반으로 파라미터 재조정
            adjusted_params = _get_adjusted_params(tool_name, feedback["fix_instruction"], llm)
            result = tool.invoke(adjusted_params)
            results.append({"tool": tool_name, "result": result, "strategy": strategy})

    elif strategy == "try_vision_fallback":
        # 엑셀 → 이미지 변환 후 Vision으로 구조 파악
        if state["files"]:
            vision_result = _excel_vision_fallback(state["files"][0], llm)
            results.append({"tool": "VisionFallback", "result": vision_result, "strategy": strategy})

    elif strategy == "aggregate_only":
        # 원본 분석 포기, 집계 결과만
        for file in state["files"]:
            agg_result = _get_aggregated_only(file)
            results.append({"tool": "AggregateOnly", "result": agg_result, "strategy": strategy})

    return {
        **state,
        "tool_results": results,
        "retry_count": state["retry_count"] + 1,
        "retry_strategy": {"attempt": state["retry_count"] + 1, "strategy": strategy}
    }


def _get_adjusted_params(tool_name: str, fix_instruction: str, llm) -> dict:
    """fix_instruction에서 파라미터 추출"""
    prompt = f"""
다음 수정 지침에서 툴 파라미터를 추출하세요.
툴: {tool_name}
수정 지침: {fix_instruction}

JSON으로만: {{"param_name": "value", ...}}
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    import json
    return json.loads(response.content)
```

---

## 5. Schema 사전 등록 시스템 (`tools/excel/schema_registry.py`)

```python
import json
import os
from pathlib import Path
from typing import Optional
import difflib

SCHEMA_DIR = Path("config/excel_schemas")

class SchemaRegistry:
    """
    자주 쓰는 엑셀 양식을 미리 등록해두는 시스템.
    엑셀이 들어오면 등록된 스키마와 매칭 → 맞으면 추론 스킵.
    """

    def __init__(self):
        self.schemas = {}
        self._load_all()

    def _load_all(self):
        """config/excel_schemas/ 폴더의 JSON 파일 전부 로드"""
        if not SCHEMA_DIR.exists():
            return
        for path in SCHEMA_DIR.glob("*.json"):
            with open(path) as f:
                schema = json.load(f)
                self.schemas[schema["name"]] = schema

    def register(self, name: str, schema: dict):
        """새 스키마 등록 (관리자용)"""
        schema["name"] = name
        SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SCHEMA_DIR / f"{name}.json", "w") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)
        self.schemas[name] = schema
        print(f"스키마 등록 완료: {name}")

    def match(self, parsed_structure: dict) -> Optional[dict]:
        """
        파싱된 엑셀 구조와 등록된 스키마 매칭 시도.
        유사도 0.8 이상이면 매칭 성공.
        """
        parsed_headers = self._extract_headers(parsed_structure)

        best_match = None
        best_score = 0.0

        for name, schema in self.schemas.items():
            score = self._similarity(parsed_headers, schema.get("expected_headers", []))
            if score > best_score:
                best_score = score
                best_match = schema

        if best_score >= 0.8:
            print(f"스키마 매칭 성공: {best_match['name']} (유사도: {best_score:.2f})")
            return best_match
        
        print(f"매칭된 스키마 없음 (최고 유사도: {best_score:.2f}) → 추론 방식으로 진행")
        return None

    def _extract_headers(self, structure: dict) -> list:
        headers = []
        for sheet in structure.get("sheets", []):
            sample = sheet.get("data_sample", [])
            if sample:
                headers.extend([str(h) for h in sample[0] if h is not None])
        return headers

    def _similarity(self, a: list, b: list) -> float:
        if not a or not b:
            return 0.0
        matcher = difflib.SequenceMatcher(None, sorted(a), sorted(b))
        return matcher.ratio()


# 싱글톤
registry = SchemaRegistry()
```

---

## 6. 스키마 등록 예시 (`config/excel_schemas/measurement_v1.json`)

```json
{
  "name": "measurement_v1",
  "description": "측정 데이터 표준 양식 v1",
  "table_type": "crosstable",
  "expected_headers": ["날짜", "라인", "설비", "측정값", "기준값", "판정", "작업자"],
  "header_row": 2,
  "data_start_row": 3,
  "key_columns": {
    "date_col": "날짜",
    "value_col": "측정값",
    "judgment_col": "판정"
  },
  "analysis_defaults": {
    "outlier_method": "IQR",
    "trend_window": 7
  }
}
```

```json
{
  "name": "mes_output_v2",
  "description": "MES 출력 데이터 v2",
  "table_type": "database",
  "expected_headers": ["공정", "품번", "수량", "불량수", "불량률", "생산일", "라인"],
  "header_row": 1,
  "data_start_row": 2,
  "key_columns": {
    "date_col": "생산일",
    "value_col": "불량률"
  },
  "analysis_defaults": {
    "outlier_method": "zscore",
    "trend_window": 30
  }
}
```

---

## 7. Schema 매칭을 Memory 노드에 통합 (`graph/memory.py`)

```python
from tools.excel.schema_registry import registry

def memory_node(state: AgentState) -> AgentState:
    updates = {}

    # 1. 이미 파싱된 파일이면 재파싱 스킵
    if state.get("excel_schema"):
        cached_path = state["excel_schema"].get("file_path")
        if cached_path in state["files"]:
            print(f"캐시 히트: {cached_path} 재파싱 스킵")
            return state

    # 2. 파싱된 구조가 있으면 스키마 매칭 시도
    if state.get("excel_schema"):
        matched = registry.match(state["excel_schema"])
        if matched:
            updates["matched_schema"] = matched["name"]
            # 매칭된 스키마 정보를 excel_schema에 병합
            updates["excel_schema"] = {
                **state["excel_schema"],
                "matched_schema": matched,
                # 사전 등록된 헤더 위치, 타입 정보 주입
                "header_rows": [matched["header_row"]],
                "data_start_row": matched["data_start_row"],
            }

    return {**state, **updates}
```

---

## 8. 그래프 최종 조립

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_graph(llm):
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", lambda s: supervisor_node(s, llm))
    graph.add_node("planner",    lambda s: planner_node(s, llm))    # 하드가드 포함
    graph.add_node("memory",     memory_node)                        # 스키마 매칭 포함
    graph.add_node("executor",   lambda s: executor_node(s, TOOLS, llm))
    graph.add_node("critic",     lambda s: critic_node(s, llm))     # Self-Reflection
    graph.add_node("formatter",  lambda s: formatter_node(s, llm))

    graph.set_entry_point("supervisor")
    graph.add_edge("supervisor", "planner")
    graph.add_edge("planner",    "memory")
    graph.add_edge("memory",     "executor")
    graph.add_edge("executor",   "critic")

    graph.add_conditional_edges("critic", should_retry, {
        "executor":  "executor",   # 재시도 (전략 변경됨)
        "formatter": "formatter"   # 통과 or Early Stop
    })

    graph.add_edge("formatter", END)

    return graph.compile(checkpointer=MemorySaver())
```

---

## v2 → v3 변경 요약

| 항목 | v2 | v3 |
|------|----|----|
| Plan 검증 | ❌ 없음 | ✅ 코드 레벨 하드가드 |
| Critic 피드백 | ❌ "다시 해" | ✅ 원인/수정지침/재실행툴 명시 |
| Early Stop | ❌ 3번 무조건 재실행 | ✅ 데이터 오염이면 즉시 종료 |
| 재시도 전략 | ❌ 동일 방법 반복 | ✅ 매 회 전략 변경 |
| 엑셀 스키마 | ❌ 매번 추론 | ✅ 사전 등록 + 캐시 매칭 |
