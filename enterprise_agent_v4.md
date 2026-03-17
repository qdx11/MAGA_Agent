# Enterprise AI Agent v4.0

## v3 → v4 핵심 변경점

1. **Tool Registry 동적화** — 툴 목록/선행조건 하드코딩 제거, 레지스트리 기반 자동 생성
2. **스텝별 검증(Step Guard)** — 전체 실행 후 Critic 평가가 아닌, 매 스텝 후 즉시 검증
3. **Re-Planning** — 중간 실패 시 남은 플랜을 재구성
4. **Retry 전략 일반화** — 툴 그룹/에러 타입별 전략 매핑
5. **State 크기 관리** — tool_results sliding window + 요약
6. **관측성(Observability)** — 노드별 트레이싱/로깅
7. **멀티유저/권한** — user_context + ACL 실제 적용
8. **LLM 장애 대응** — timeout, retry, circuit breaker, fallback
9. **MCP 마이그레이션 경로** — in-process 우선, 점진적 MCP 분리
10. **테스트 전략** — 노드 단위 테스트 + Mock LLM

---

## 파일 구조

```
enterprise_agent/
├── core/
│   ├── llm_client.py          # LLM 팩토리 + circuit breaker
│   ├── tool_registry.py       # 동적 Tool Registry (핵심)
│   ├── tracer.py              # 관측성 (트레이싱/로깅)
│   └── auth.py                # 사용자 인증/권한
├── graph/
│   ├── state.py               # AgentState v4
│   ├── supervisor.py          # [LLM] 의도 파악
│   ├── planner.py             # [LLM] 플랜 생성 + 하드가드 (레지스트리 기반)
│   ├── memory.py              # [코드] 캐시 + 스키마 매칭
│   ├── executor.py            # [코드] 스텝별 실행 + Step Guard
│   ├── critic.py              # [LLM] 최종 품질 평가
│   ├── replanner.py           # [LLM] 중간 실패 시 재계획
│   ├── formatter.py           # [LLM] 최종 답변 생성
│   └── builder.py             # 그래프 조립
├── tools/
│   ├── base.py                # Tool 추상 베이스
│   ├── excel/
│   │   ├── structure_parser.py
│   │   └── schema_registry.py
│   ├── mes/
│   │   └── query_tool.py
│   └── ...                    # 새 툴은 여기에 추가 + registry 등록만
├── config/
│   ├── tools.yaml             # Tool Registry 메타데이터
│   ├── retry_strategies.yaml  # 그룹/에러별 재시도 전략
│   └── excel_schemas/
│       ├── measurement_v1.json
│       └── mes_output_v2.json
└── tests/
    ├── test_planner.py
    ├── test_executor.py
    ├── test_critic.py
    └── conftest.py            # Mock LLM fixtures
```

---

## LLM 사용 노드 맵

| 노드 | LLM 사용 | 이유 |
|------|----------|------|
| `supervisor` | O | 자연어 → intent 추출 |
| `planner` | O | intent + tool 목록 → 실행 플랜 생성 |
| `memory` | **X** | 캐시 조회, 스키마 매칭 (코드 로직) |
| `executor` | **X** | 플랜대로 Tool 호출 (코드 로직) |
| `step_guard` | **X** | 각 스텝 결과의 status 코드 기반 분기 (코드 로직) |
| `replanner` | O | 중간 실패 시 남은 플랜 재구성 |
| `critic` | O | 전체 결과 품질 평가 |
| `formatter` | O | 최종 자연어 답변 생성 |

> v3에서 executor retry 시 LLM을 쓰던 `_get_adjusted_params`는 제거.
> 대신 critic의 `fix_instruction`을 **구조화된 JSON**으로 받아서 코드 레벨로 처리.

---

## 1. AgentState v4 (`graph/state.py`)

```python
from typing import TypedDict, Annotated, List, Optional, Literal
from langgraph.graph.message import add_messages
import time
import uuid


class UserContext(TypedDict):
    user_id: str
    team: str
    role: Literal["admin", "analyst", "viewer"]
    permissions: List[str]  # ["excel:read", "mes:query", "mes:write", ...]


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
    strategy: Optional[str]     # retry 시 어떤 전략으로 실행했는지
    timestamp: float


class CriticFeedback(TypedDict):
    score: float
    passed: bool
    root_cause: str
    fix_instruction: dict       # v4: 구조화된 JSON (v3의 자유 텍스트에서 변경)
    tools_to_rerun: List[str]
    early_stop: bool


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: str
    plan: Optional[dict]
    excel_schema: Optional[dict]
    files: List[str]

    # v4 핵심 변경
    user_context: UserContext                    # 멀티유저/권한
    tool_results: List[StepResult]               # 구조화된 스텝 결과
    tool_results_summary: Optional[str]          # State 크기 관리용 요약
    trace: List[TraceEntry]                      # 관측성

    critic_feedback: Optional[CriticFeedback]
    retry_count: int
    early_stopped: bool
    matched_schema: Optional[str]
    final_answer: Optional[str]

    # v4 추가
    current_step: int                            # executor가 몇 번째 스텝인지
    needs_replan: bool                           # step_guard가 True로 설정하면 replanner로
    session_id: str                              # 세션 추적용
```

---

## 2. Tool Registry — 하드코딩 제거 (`core/tool_registry.py`)

### 2-1. 설정 파일 (`config/tools.yaml`)

```yaml
tools:
  - name: ExcelStructureParser
    group: excel
    description: "엑셀 파일의 시트/행/열 구조를 파악합니다."
    prerequisites: []
    retry_strategies:
      - adjust_parameters
    entry_point: "tools.excel.structure_parser:excel_structure_parser"

  - name: HeaderDetector
    group: excel
    description: "엑셀 파일에서 헤더 행 위치를 감지합니다."
    prerequisites: [ExcelStructureParser]
    retry_strategies:
      - adjust_parameters
      - try_vision_fallback
    entry_point: "tools.excel.header_detector:header_detector"

  - name: DataTypeInferrer
    group: excel
    description: "각 컬럼의 데이터 타입을 추론합니다."
    prerequisites: [ExcelStructureParser]
    retry_strategies:
      - adjust_parameters
    entry_point: "tools.excel.datatype_inferrer:datatype_inferrer"

  - name: OutlierDetector
    group: excel
    description: "수치 데이터에서 이상값을 감지합니다."
    prerequisites: [ExcelStructureParser, HeaderDetector, DataTypeInferrer]
    retry_strategies:
      - adjust_parameters
      - aggregate_only
    entry_point: "tools.excel.outlier_detector:outlier_detector"

  - name: MESQueryTool
    group: mes
    description: "사내 MES에서 생산/품질 데이터를 조회합니다."
    prerequisites: []
    retry_strategies:
      - adjust_parameters
    entry_point: "tools.mes.query_tool:mes_query_tool"

  - name: MESDataFormatter
    group: mes
    description: "MES 조회 결과를 분석용 포맷으로 변환합니다."
    prerequisites: [MESQueryTool]
    retry_strategies:
      - adjust_parameters
    entry_point: "tools.mes.data_formatter:mes_data_formatter"
```

### 2-2. Registry 구현

```python
import yaml
import importlib
from pathlib import Path
from typing import Dict, List, Optional, Callable


class ToolMeta:
    def __init__(self, config: dict):
        self.name: str = config["name"]
        self.group: str = config["group"]
        self.description: str = config["description"]
        self.prerequisites: List[str] = config.get("prerequisites", [])
        self.retry_strategies: List[str] = config.get("retry_strategies", [])
        self._entry_point: str = config["entry_point"]
        self._fn: Optional[Callable] = None

    def invoke(self, **kwargs) -> str:
        if self._fn is None:
            module_path, func_name = self._entry_point.rsplit(":", 1)
            module = importlib.import_module(module_path)
            self._fn = getattr(module, func_name)
        return self._fn(**kwargs)


class ToolRegistry:
    def __init__(self, config_path: str = "config/tools.yaml"):
        self._tools: Dict[str, ToolMeta] = {}
        self._load(config_path)

    def _load(self, path: str):
        with open(path) as f:
            config = yaml.safe_load(f)
        for tool_conf in config["tools"]:
            meta = ToolMeta(tool_conf)
            self._tools[meta.name] = meta

    def get(self, name: str) -> ToolMeta:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered")
        return self._tools[name]

    def list_all(self) -> List[ToolMeta]:
        return list(self._tools.values())

    def by_group(self, group: str) -> List[ToolMeta]:
        return [t for t in self._tools.values() if t.group == group]

    def available_tools_map(self) -> Dict[str, List[str]]:
        """v3의 AVAILABLE_TOOLS 자동 생성"""
        result: Dict[str, List[str]] = {}
        for t in self._tools.values():
            result.setdefault(t.group, []).append(t.name)
        return result

    def prerequisite_rules(self) -> Dict[str, List[str]]:
        """v3의 PREREQUISITE_RULES 자동 생성"""
        return {
            t.name: t.prerequisites
            for t in self._tools.values()
            if t.prerequisites
        }

    def tool_descriptions_for_planner(self) -> str:
        """Planner 프롬프트에 주입할 툴 카탈로그 텍스트"""
        lines = []
        for group, tools in self.available_tools_map().items():
            lines.append(f"\n[{group} 그룹]")
            for name in tools:
                meta = self._tools[name]
                prereqs = ", ".join(meta.prerequisites) if meta.prerequisites else "없음"
                lines.append(f"  - {name}: {meta.description} (선행: {prereqs})")
        return "\n".join(lines)

    def retry_strategies_for(self, tool_name: str) -> List[str]:
        """특정 툴이 지원하는 재시도 전략 목록"""
        return self.get(tool_name).retry_strategies


# 싱글톤
registry = ToolRegistry()
```

> **새 Tool 추가 시**: `config/tools.yaml`에 항목 추가 + `tools/` 폴더에 구현체.
> 코드 수정 없이 Planner/Executor/Validator 모두 자동 반영.

---

## 3. Planner 하드가드 — 레지스트리 연동 (`graph/planner.py`)

```python
from core.tool_registry import registry


class PlanValidationError(Exception):
    pass


def validate_plan(plan: dict) -> None:
    """레지스트리 기반 자동 검증. 하드코딩된 룰 없음."""
    available = registry.available_tools_map()
    all_tools = [t for group in available.values() for t in group]
    prereq_rules = registry.prerequisite_rules()
    step_tools = [step["tool"] for step in plan["steps"]]

    for i, step in enumerate(plan["steps"]):
        tool = step["tool"]

        if tool not in all_tools:
            raise PlanValidationError(
                f"'{tool}'은 등록되지 않은 툴입니다. "
                f"사용 가능: {all_tools}"
            )

        for prereq in prereq_rules.get(tool, []):
            if prereq not in step_tools[:i]:
                raise PlanValidationError(
                    f"'{tool}' 실행 전에 '{prereq}'가 필요합니다."
                )

    # 그룹별 진입점 검증: 해당 그룹 툴이 있으면 선행조건 없는 첫 툴이 맨 앞인지
    for group, tools in available.items():
        group_tools_in_plan = [t for t in step_tools if t in tools]
        if not group_tools_in_plan:
            continue
        first_tool = group_tools_in_plan[0]
        if prereq_rules.get(first_tool):
            entry_tools = [t for t in tools if not prereq_rules.get(t)]
            if entry_tools and step_tools.index(first_tool) < step_tools.index(entry_tools[0]):
                pass  # entry tool이 뒤에 있으면 문제
            # 진입점 툴이 그룹 내 첫 번째인지 확인
            for et in entry_tools:
                if et in step_tools and step_tools.index(et) > step_tools.index(first_tool):
                    raise PlanValidationError(
                        f"'{group}' 그룹은 '{et}'가 먼저 실행되어야 합니다."
                    )


def build_planner_prompt() -> str:
    """레지스트리에서 툴 카탈로그를 동적으로 주입"""
    tool_catalog = registry.tool_descriptions_for_planner()
    return f"""당신은 AI 에이전트의 실행 계획을 수립하는 플래너입니다.

[사용 가능한 툴]
{tool_catalog}

[규칙]
1. 반드시 위 툴만 사용하세요.
2. 선행 조건이 명시된 툴은 해당 선행 툴 이후에 배치하세요.
3. 각 스텝은 {{"step": N, "tool": "ToolName", "reason": "이유", "params": {{}}}} 형태.

JSON 배열로만 응답:
{{"steps": [...], "total_steps": N}}
"""


def planner_node(state: AgentState, llm, tracer) -> AgentState:
    with tracer.span("planner"):
        prompt = build_planner_prompt()
        max_attempts = 3

        for attempt in range(max_attempts):
            response = llm.invoke([
                SystemMessage(content=prompt),
                HumanMessage(content=(
                    f"의도: {state['intent']}\n"
                    f"파일: {state['files']}\n"
                    f"요청: {state['messages'][-1].content}"
                ))
            ])

            plan = json.loads(response.content)
            plan["current_step"] = 0

            try:
                validate_plan(plan)
                return {**state, "plan": plan, "current_step": 0}
            except PlanValidationError as e:
                if attempt == max_attempts - 1:
                    return {**state, "plan": _safe_default_plan(state), "current_step": 0}
                prompt += f"\n\n[이전 오류]: {str(e)}\n반드시 수정하세요."
```

---

## 4. Executor + Step Guard (`graph/executor.py`)

> v3: 전체 플랜 실행 → Critic 평가 (중간 실패 감지 불가)
> v4: **매 스텝 실행 후 Step Guard가 즉시 검증** → 실패 시 replanner로

```python
import time
from core.tool_registry import registry


def executor_node(state: AgentState, tracer) -> AgentState:
    """한 스텝만 실행하고 Step Guard로 넘김"""
    with tracer.span("executor"):
        plan = state["plan"]
        step_idx = state["current_step"]

        if step_idx >= plan["total_steps"]:
            return {**state, "current_step": step_idx}

        step = plan["steps"][step_idx]
        tool_name = step["tool"]
        params = step.get("params", {})

        # 권한 체크
        user = state["user_context"]
        tool_meta = registry.get(tool_name)
        required_perm = f"{tool_meta.group}:read"
        if required_perm not in user.get("permissions", []):
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status="error",
                result={"error_code": "PERMISSION_DENIED", "early_stop": True},
                strategy=None,
                timestamp=time.time(),
            )
            return {
                **state,
                "tool_results": state["tool_results"] + [result],
                "early_stopped": True,
            }

        # Tool 실행
        try:
            raw = tool_meta.invoke(**params)
            parsed = json.loads(raw)
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status=parsed.get("status", "success"),
                result=parsed,
                strategy=None,
                timestamp=time.time(),
            )
        except Exception as e:
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status="error",
                result={"error_code": "TOOL_EXECUTION_ERROR", "message": str(e)},
                strategy=None,
                timestamp=time.time(),
            )

        updated_results = state["tool_results"] + [result]

        # State 크기 관리: 결과가 10개 초과 시 오래된 것 요약
        summary = state.get("tool_results_summary")
        if len(updated_results) > 10:
            old = updated_results[:5]
            summary_text = "; ".join(
                f"[{r['tool']}:{r['status']}]" for r in old
            )
            summary = (summary or "") + " | " + summary_text
            updated_results = updated_results[5:]

        return {
            **state,
            "tool_results": updated_results,
            "tool_results_summary": summary,
            "current_step": step_idx + 1,
        }


def step_guard(state: AgentState) -> str:
    """
    매 스텝 후 실행. LLM 없이 코드 로직으로 분기 결정.

    반환값:
      "executor"   → 다음 스텝 계속
      "replanner"  → 중간 실패, 남은 플랜 재구성 필요
      "critic"     → 모든 스텝 완료, 최종 평가로
      "formatter"  → early_stop, 바로 종료
    """
    if state.get("early_stopped"):
        return "formatter"

    last_result = state["tool_results"][-1] if state["tool_results"] else None

    if last_result and last_result["status"] == "error":
        error_code = last_result["result"].get("error_code", "")
        early_stop = last_result["result"].get("early_stop", False)

        if early_stop:
            return "formatter"

        # 재시도 가능한 에러 → replanner에서 남은 플랜 수정
        return "replanner"

    # 모든 스텝 완료 여부
    plan = state["plan"]
    if state["current_step"] >= plan["total_steps"]:
        return "critic"

    return "executor"
```

---

## 5. Replanner — 중간 실패 시 재계획 (`graph/replanner.py`)

```python
def replanner_node(state: AgentState, llm, tracer) -> AgentState:
    """
    Step Guard에서 중간 실패 감지 시 호출.
    실패한 스텝 + 남은 플랜을 보고 대안 플랜 생성.
    retry_count >= 3이면 재계획 포기 → formatter로.
    """
    with tracer.span("replanner"):
        if state["retry_count"] >= 3:
            return {**state, "early_stopped": True}

        last_failure = state["tool_results"][-1]
        failed_tool = last_failure["tool"]
        error_info = last_failure["result"]
        remaining_steps = state["plan"]["steps"][state["current_step"]:]

        # 해당 툴이 지원하는 재시도 전략 조회
        available_strategies = registry.retry_strategies_for(failed_tool)
        used_count = state["retry_count"]
        strategy = (
            available_strategies[used_count]
            if used_count < len(available_strategies)
            else "skip"
        )

        if strategy == "skip":
            # 이 툴의 전략을 모두 소진 → 남은 스텝만으로 진행
            new_steps = [s for s in remaining_steps if s["tool"] != failed_tool]
        else:
            # LLM에게 수정된 플랜 요청
            prompt = f"""이전 스텝 실패:
- 툴: {failed_tool}
- 에러: {json.dumps(error_info, ensure_ascii=False)}
- 적용할 전략: {strategy}
- 남은 스텝: {json.dumps(remaining_steps, ensure_ascii=False)}

위 전략을 반영하여 남은 스텝을 재구성하세요.
JSON으로만: {{"steps": [...], "total_steps": N}}"""

            response = llm.invoke([HumanMessage(content=prompt)])
            new_plan = json.loads(response.content)
            new_steps = new_plan["steps"]

        updated_plan = {
            "steps": state["plan"]["steps"][:state["current_step"]] + new_steps,
            "total_steps": state["current_step"] + len(new_steps),
        }

        return {
            **state,
            "plan": updated_plan,
            "retry_count": state["retry_count"] + 1,
        }
```

---

## 6. Critic — 최종 평가 (`graph/critic.py`)

v3 대비 변경:
- `fix_instruction`이 **구조화된 JSON** (자유 텍스트 아님)
- `tool_results_summary`도 같이 전달하여 context window 관리

```python
EARLY_STOP_CAUSES = {
    "data_corrupted",
    "file_not_readable",
    "schema_mismatch",
    "permission_denied",
}

CRITIC_PROMPT = """당신은 AI 에이전트 실행 결과의 품질을 평가하는 비평가입니다.

[평가 기준]
- 0.9 이상: 완벽
- 0.7~0.9: 양호, 통과
- 0.7 미만: 재실행 필요

[fix_instruction은 반드시 아래 JSON 구조]:
{
  "target_tools": ["ToolName"],
  "action": "adjust_parameters | retry | skip",
  "params_override": {"param": "value"}
}
"다시 해" 같은 자유 텍스트 금지.

JSON으로만 응답:
{
  "score": 0.65,
  "passed": false,
  "root_cause": "header_detection_failed",
  "fix_instruction": {"target_tools": ["HeaderDetector"], "action": "adjust_parameters", "params_override": {"header_row": 3}},
  "tools_to_rerun": ["HeaderDetector", "DataTypeInferrer"],
  "early_stop": false
}
"""


def critic_node(state: AgentState, llm, tracer) -> AgentState:
    with tracer.span("critic"):
        # State 크기 관리: 요약 + 최근 결과만 전달
        summary = state.get("tool_results_summary", "없음")
        recent = state["tool_results"][-5:]  # 최근 5개만

        context = f"""원래 질문: {state['messages'][0].content}
실행 이력 요약: {summary}
최근 툴 결과: {json.dumps([r for r in recent], ensure_ascii=False, default=str)[:3000]}
재시도 횟수: {state['retry_count']}
"""
        response = llm.invoke([
            SystemMessage(content=CRITIC_PROMPT),
            HumanMessage(content=context)
        ])

        feedback = json.loads(response.content)

        if feedback["root_cause"] in EARLY_STOP_CAUSES:
            feedback["early_stop"] = True

        return {**state, "critic_feedback": feedback}


def should_continue_after_critic(state: AgentState) -> str:
    feedback = state["critic_feedback"]

    if feedback["early_stop"] or feedback["passed"] or state["retry_count"] >= 3:
        return "formatter"

    # 재시도: replanner를 통해 플랜 수정 후 다시 실행
    return "replanner"
```

---

## 7. LLM Client — 장애 대응 (`core/llm_client.py`)

```python
import time
from langchain_openai import ChatOpenAI
from typing import Optional


class CircuitBreaker:
    """연속 N회 실패 시 일정 시간 차단"""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.is_open = False

    def record_success(self):
        self.failure_count = 0
        self.is_open = False

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True

    def can_proceed(self) -> bool:
        if not self.is_open:
            return True
        if time.time() - self.last_failure_time > self.recovery_timeout:
            self.is_open = False
            return True
        return False


class ResilientLLMClient:
    """
    사내 LLM API 연결 + 장애 대응.
    - timeout per request
    - retry with backoff
    - circuit breaker
    - optional fallback LLM
    """

    def __init__(
        self,
        primary_base_url: str,
        primary_api_key: str,
        primary_model: str = "gpt-4o",
        fallback_base_url: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        fallback_model: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.primary = ChatOpenAI(
            base_url=primary_base_url,
            api_key=primary_api_key,
            model=primary_model,
            timeout=timeout,
            max_retries=0,  # 자체 retry 사용
        )
        self.fallback = None
        if fallback_base_url:
            self.fallback = ChatOpenAI(
                base_url=fallback_base_url,
                api_key=fallback_api_key,
                model=fallback_model or primary_model,
                timeout=timeout,
                max_retries=0,
            )
        self.max_retries = max_retries
        self.breaker = CircuitBreaker()

    def invoke(self, messages, **kwargs):
        # Primary 시도
        if self.breaker.can_proceed():
            for attempt in range(self.max_retries + 1):
                try:
                    result = self.primary.invoke(messages, **kwargs)
                    self.breaker.record_success()
                    return result
                except Exception:
                    if attempt < self.max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    self.breaker.record_failure()

        # Fallback 시도
        if self.fallback:
            return self.fallback.invoke(messages, **kwargs)

        raise RuntimeError("LLM 서비스 불가: primary/fallback 모두 실패")
```

---

## 8. Tracer — 관측성 (`core/tracer.py`)

```python
import time
import logging
import uuid
from contextlib import contextmanager

logger = logging.getLogger("enterprise_agent")


class Tracer:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.entries = []

    @contextmanager
    def span(self, node_name: str, input_summary: str = ""):
        trace_id = str(uuid.uuid4())[:8]
        start = time.time()
        logger.info(f"[{self.session_id}] START {node_name} (trace={trace_id})")

        entry = {
            "trace_id": trace_id,
            "node": node_name,
            "timestamp": start,
            "input_summary": input_summary,
        }

        try:
            yield entry
            entry["error"] = None
        except Exception as e:
            entry["error"] = str(e)
            raise
        finally:
            elapsed = (time.time() - start) * 1000
            entry["duration_ms"] = elapsed
            self.entries.append(entry)
            status = "OK" if not entry.get("error") else f"ERR: {entry['error']}"
            logger.info(
                f"[{self.session_id}] END {node_name} "
                f"({elapsed:.0f}ms) {status}"
            )

    def summary(self) -> str:
        lines = [f"Session: {self.session_id}"]
        total = 0.0
        for e in self.entries:
            ms = e.get("duration_ms", 0)
            total += ms
            status = "OK" if not e.get("error") else "FAIL"
            lines.append(f"  {e['node']:15s} {ms:8.0f}ms  {status}")
        lines.append(f"  {'TOTAL':15s} {total:8.0f}ms")
        return "\n".join(lines)
```

---

## 9. 그래프 조립 (`graph/builder.py`)

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from core.tool_registry import registry
from core.llm_client import ResilientLLMClient
from core.tracer import Tracer


def build_graph(llm: ResilientLLMClient, session_id: str):
    tracer = Tracer(session_id)
    graph = StateGraph(AgentState)

    #                    LLM 사용 여부
    graph.add_node("supervisor", lambda s: supervisor_node(s, llm, tracer))    # O
    graph.add_node("planner",    lambda s: planner_node(s, llm, tracer))       # O
    graph.add_node("memory",     lambda s: memory_node(s, tracer))             # X
    graph.add_node("executor",   lambda s: executor_node(s, tracer))           # X
    graph.add_node("step_guard", lambda s: s)  # 분기만 담당 (passthrough)     # X
    graph.add_node("replanner",  lambda s: replanner_node(s, llm, tracer))     # O
    graph.add_node("critic",     lambda s: critic_node(s, llm, tracer))        # O
    graph.add_node("formatter",  lambda s: formatter_node(s, llm, tracer))     # O

    # 흐름
    graph.set_entry_point("supervisor")
    graph.add_edge("supervisor", "planner")
    graph.add_edge("planner",    "memory")
    graph.add_edge("memory",     "executor")
    graph.add_edge("executor",   "step_guard")

    # Step Guard 분기
    graph.add_conditional_edges("step_guard", step_guard, {
        "executor":  "executor",    # 다음 스텝
        "replanner": "replanner",   # 중간 실패 → 재계획
        "critic":    "critic",      # 전체 완료 → 최종 평가
        "formatter": "formatter",   # early stop
    })

    graph.add_edge("replanner", "executor")

    # Critic 분기
    graph.add_conditional_edges("critic", should_continue_after_critic, {
        "replanner": "replanner",   # 품질 미달 → 재계획
        "formatter": "formatter",   # 통과 or 종료
    })

    graph.add_edge("formatter", END)

    return graph.compile(checkpointer=MemorySaver())
```

### 그래프 흐름도

```
supervisor ──→ planner ──→ memory ──→ executor ──→ step_guard
                                         ↑            │
                                         │      ┌─────┴──────┬──────────┐
                                         │      ↓            ↓          ↓
                                         ├─ executor    replanner    critic
                                         │   (다음스텝)     │          │
                                         │                  │    ┌─────┴─────┐
                                         │                  ↓    ↓           ↓
                                         └──────────── executor  replanner  formatter ──→ END
                                                                    │
                                                                    ↓
                                                                 executor
```

- **executor ↔ step_guard**: 매 스텝 루프
- **step_guard → replanner → executor**: 중간 실패 복구
- **critic → replanner → executor**: 최종 평가 후 재시도
- **step_guard/critic → formatter → END**: 종료 경로

---

## 10. 테스트 전략

### 10-1. Mock LLM (`tests/conftest.py`)

```python
import pytest
from unittest.mock import MagicMock


class MockLLM:
    """테스트용 LLM. 미리 정한 응답을 순서대로 반환."""

    def __init__(self, responses: list):
        self.responses = responses
        self.call_count = 0

    def invoke(self, messages, **kwargs):
        resp = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        mock_result = MagicMock()
        mock_result.content = resp
        return mock_result


@pytest.fixture
def mock_llm():
    def _factory(responses):
        return MockLLM(responses)
    return _factory
```

### 10-2. 테스트 범위

| 대상 | 테스트 방식 | LLM 필요 |
|------|------------|----------|
| `validate_plan()` | 유효/무효 플랜 직접 전달 | X |
| `step_guard()` | 다양한 StepResult 주입 → 분기 확인 | X |
| `executor_node()` | Mock Tool + Mock State → 결과 검증 | X |
| `planner_node()` | Mock LLM 응답 → 하드가드 통과 여부 | O (Mock) |
| `critic_node()` | Mock LLM 응답 → early_stop 강제 검증 | O (Mock) |
| `replanner_node()` | 실패 시나리오 → 재구성된 플랜 검증 | O (Mock) |
| E2E | 실제 LLM + 샘플 파일 → 전체 흐름 | O (실제) |

---

## v3 → v4 변경 요약

| 항목 | v3 | v4 |
|------|----|----|
| Tool 목록 | 코드에 하드코딩 | `config/tools.yaml` + 동적 Registry |
| 선행조건 규칙 | 코드에 하드코딩 | Registry에서 자동 생성 |
| 검증 시점 | 전체 실행 후 Critic만 | **매 스텝 후 Step Guard** + 최종 Critic |
| 중간 실패 | 전체 재실행 | **Replanner**가 남은 플랜만 수정 |
| Retry 전략 | 전역 하드코딩 (1→2→3) | **툴별** `retry_strategies` in YAML |
| Executor LLM 의존 | retry 시 LLM 호출 | retry 시에도 **코드 레벨** (구조화된 fix_instruction) |
| State 크기 | 무한 누적 | **sliding window** + 요약 |
| 관측성 | 없음 | **Tracer** (노드별 로깅/타이밍) |
| 멀티유저 | 없음 | **UserContext** + 권한 체크 |
| LLM 장애 | 전체 중단 | **CircuitBreaker** + fallback LLM |
| 테스트 | 없음 | **MockLLM** + 노드 단위 테스트 |
| MCP 전환 | 미고려 | **Registry entry_point** → MCP endpoint로 교체 가능 |
