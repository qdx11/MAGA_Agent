from __future__ import annotations
import json
import time
from enterprise_agent.graph.state import AgentState, StepResult
from enterprise_agent.core.tool_registry import get_registry
from enterprise_agent.core.tracer import Tracer


def executor_node(state: AgentState, tracer: Tracer) -> AgentState:
    """
    한 스텝만 실행하고 step_guard로 넘김.
    LLM 없음 — 코드 로직만.
    """
    with tracer.span("executor") as span:
        plan = state.get("plan")
        if not plan or not plan.get("steps"):
            span["output_summary"] = "no_plan"
            return state

        step_idx = state["current_step"]
        if step_idx >= plan["total_steps"]:
            span["output_summary"] = "all_steps_done"
            return state

        print("executor step:", plan["steps"][step_idx])
        step = plan["steps"][step_idx]
        # replanner가 "tool" 키 없는 이상한 step 만들 수 있음 → 방어
        tool_name = step.get("tool") or step.get("name", "")
        if not tool_name:
            span["output_summary"] = "invalid_step_no_tool"
            return {**state, "current_step": step_idx + 1}
        params = step.get("params", {})

        registry = get_registry()

        # 권한 체크
        user_permissions = state["user_context"].get("permissions", [])
        if not registry.check_permission(tool_name, user_permissions):
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status="error",
                result={
                    "status": "error",
                    "error_code": "PERMISSION_DENIED",
                    "early_stop": True,
                    "message": f"'{tool_name}' 실행 권한이 없습니다.",
                },
                strategy=None,
                timestamp=time.time(),
            )
            span["output_summary"] = f"PERMISSION_DENIED: {tool_name}"
            return {
                **state,
                "tool_results": state["tool_results"] + [result],
                "early_stopped": True,
                "current_step": step_idx + 1,
            }

        # 이전 툴 결과를 params에 자동 주입
        # (선행 툴 결과가 필요한 툴에게 전달)
        params = _inject_previous_results(params, state, tool_name)

        # 툴 실행
        try:
            tool_meta = registry.get(tool_name)
            raw = tool_meta.invoke(**params)
            parsed = json.loads(raw) if isinstance(raw, str) else raw

            status = parsed.get("status", "success")
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status=status,
                result=parsed,
                strategy=None,
                timestamp=time.time(),
            )
            span["output_summary"] = f"{tool_name} → {status}"

            # ExcelStructureParser 결과면 캐시에 저장
            if tool_name == "ExcelStructureParser" and status == "success":
                from enterprise_agent.graph.memory import cache_schema
                for f in state.get("files", []):
                    cache_schema(f, parsed.get("data", {}))

        except Exception as e:
            result = StepResult(
                step=step_idx,
                tool=tool_name,
                status="error",
                result={
                    "status": "error",
                    "error_code": "TOOL_EXECUTION_ERROR",
                    "early_stop": False,
                    "message": str(e),
                    "suggested_fix": "입력값 확인 후 재시도하세요.",
                },
                strategy=None,
                timestamp=time.time(),
            )
            span["output_summary"] = f"{tool_name} → ERROR: {e}"

        updated_results = state["tool_results"] + [result]

        # State 크기 관리: 10개 초과 시 오래된 것 요약
        summary = state.get("tool_results_summary", "")
        if len(updated_results) > 10:
            old = updated_results[:5]
            old_summary = " | ".join(f"[{r['tool']}:{r['status']}]" for r in old)
            summary = (summary + " " + old_summary).strip()
            updated_results = updated_results[5:]

        return {
            **state,
            "tool_results": updated_results,
            "tool_results_summary": summary,
            "current_step": step_idx + 1,
        }


def _inject_previous_results(
    params: dict, state: AgentState, tool_name: str
) -> dict:
    """
    선행 툴 결과를 자동으로 현재 툴 params에 주입.
    툴 파라미터 이름 규칙:
      - excel_structure → ExcelStructureParser 결과
      - header_info    → HeaderDetector 결과
      - flat_table     → CrossTableFlattener 결과
    """
    params = dict(params)  # 복사

    # 파일 경로 자동 주입
    files = state.get("files", [])

    # ExcelCompareTool: base_file, target_file 직접 주입
    if tool_name == "ExcelCompareTool":
        if "base_file" not in params and len(files) >= 1:
            params["base_file"] = files[0]
        if "target_file" not in params and len(files) >= 2:
            params["target_file"] = files[1]
        # file_path는 ExcelCompareTool에 필요없으니 제거
    elif "file_path" not in params and files:
        params["file_path"] = files[0]

    # 이전 툴 결과 자동 매핑
    result_map = {
        r["tool"]: json.dumps(r["result"], ensure_ascii=False)
        for r in state["tool_results"]
        if r["status"] == "success"
    }

    param_aliases = {
        "excel_structure": "ExcelStructureParser",
        "header_info": "HeaderDetector",
        "flat_table": "CrossTableFlattener",
        "mes_result": "MESQueryTool",
    }

    for param_key, source_tool in param_aliases.items():
        if param_key not in params and source_tool in result_map:
            params[param_key] = result_map[source_tool]

    return params
