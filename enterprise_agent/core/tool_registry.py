from __future__ import annotations
import importlib
from pathlib import Path
from typing import Callable, Dict, List, Optional
import yaml


class ToolMeta:
    def __init__(self, config: dict):
        self.name: str = config["name"]
        self.group: str = config["group"]
        self.description: str = config["description"]
        self.prerequisites: List[str] = config.get("prerequisites", [])
        self.retry_strategies: List[str] = config.get("retry_strategies", [])
        self.required_permissions: List[str] = config.get("required_permissions", [])
        self._entry_point: str = config["entry_point"]
        self._fn: Optional[Callable] = None

    def invoke(self, **kwargs) -> str:
        if self._fn is None:
            module_path, func_name = self._entry_point.rsplit(":", 1)
            module = importlib.import_module(module_path)
            self._fn = getattr(module, func_name)
        # StructuredTool이면 .invoke() 사용, 일반 함수면 직접 호출
        from langchain_core.tools import BaseTool
        if isinstance(self._fn, BaseTool):
            return self._fn.invoke(kwargs)
        return self._fn(**kwargs)

    def __repr__(self):
        return f"ToolMeta(name={self.name}, group={self.group})"


class ToolRegistry:
    def __init__(self, config_path: Optional[str] = None):
        self._tools: Dict[str, ToolMeta] = {}
        if config_path is None:
            # 기본 경로: 이 파일 기준으로 config/tools.yaml
            base = Path(__file__).parent.parent.parent
            config_path = str(base / "config" / "tools.yaml")
        self._load(config_path)

    def _load(self, path: str):
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for tool_conf in config.get("tools", []):
            meta = ToolMeta(tool_conf)
            self._tools[meta.name] = meta

    # ── 조회 ──────────────────────────────────────
    def get(self, name: str) -> ToolMeta:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered. Available: {list(self._tools)}")
        return self._tools[name]

    def list_all(self) -> List[ToolMeta]:
        return list(self._tools.values())

    def by_group(self, group: str) -> List[ToolMeta]:
        return [t for t in self._tools.values() if t.group == group]

    # ── Planner 지원 ──────────────────────────────
    def available_tools_map(self) -> Dict[str, List[str]]:
        """그룹 → 툴 이름 목록"""
        result: Dict[str, List[str]] = {}
        for t in self._tools.values():
            result.setdefault(t.group, []).append(t.name)
        return result

    def prerequisite_rules(self) -> Dict[str, List[str]]:
        """툴 이름 → 선행 툴 목록"""
        return {
            t.name: t.prerequisites
            for t in self._tools.values()
            if t.prerequisites
        }

    def tool_descriptions_for_planner(self) -> str:
        """Planner 프롬프트에 주입할 툴 카탈로그"""
        lines = []
        for group, tools in self.available_tools_map().items():
            lines.append(f"\n[{group} 그룹]")
            for name in tools:
                meta = self._tools[name]
                prereqs = ", ".join(meta.prerequisites) if meta.prerequisites else "없음"
                lines.append(f"  - {name}: {meta.description} (선행: {prereqs})")
        return "\n".join(lines)

    def retry_strategies_for(self, tool_name: str) -> List[str]:
        return self.get(tool_name).retry_strategies

    def check_permission(self, tool_name: str, user_permissions: List[str]) -> bool:
        """사용자가 해당 툴을 실행할 권한이 있는지 확인"""
        required = self.get(tool_name).required_permissions
        if not required:
            return True
        return any(p in user_permissions for p in required)


# 싱글톤
_registry: Optional[ToolRegistry] = None

def get_registry(config_path: Optional[str] = None) -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry(config_path)
    return _registry
