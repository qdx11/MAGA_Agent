import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml


@dataclass
class ToolMeta:
    name: str
    group: str
    description: str
    prerequisites: List[str]
    retry_strategies: List[str]
    entry_point: str
    _fn: Optional[Callable] = None

    def invoke(self, **kwargs) -> str:
        if self._fn is None:
            module_path, func_name = self.entry_point.split(":", 1)
            module = importlib.import_module(module_path)
            self._fn = getattr(module, func_name)
        return self._fn(**kwargs)


class ToolRegistry:
    def __init__(self, config_path: str = "config/tools.yaml") -> None:
        self._tools: Dict[str, ToolMeta] = {}
        self._load(config_path)

    def _load(self, path: str) -> None:
        cfg_path = Path(path)
        if not cfg_path.exists():
            return
        with cfg_path.open() as f:
            config = yaml.safe_load(f) or {}
        for tool_conf in config.get("tools", []):
            meta = ToolMeta(
                name=tool_conf["name"],
                group=tool_conf["group"],
                description=tool_conf.get("description", ""),
                prerequisites=tool_conf.get("prerequisites", []),
                retry_strategies=tool_conf.get("retry_strategies", []),
                entry_point=tool_conf["entry_point"],
            )
            self._tools[meta.name] = meta

    def get(self, name: str) -> ToolMeta:
        return self._tools[name]

    def list_all(self) -> List[ToolMeta]:
        return list(self._tools.values())

    def available_tools_map(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for t in self._tools.values():
            result.setdefault(t.group, []).append(t.name)
        return result

    def prerequisite_rules(self) -> Dict[str, List[str]]:
        return {t.name: t.prerequisites for t in self._tools.values() if t.prerequisites}

    def tool_descriptions_for_planner(self) -> str:
        lines: List[str] = []
        by_group = self.available_tools_map()
        for group, tools in by_group.items():
            lines.append(f"\n[{group}]")
            for name in tools:
                meta = self._tools[name]
                prereqs = ", ".join(meta.prerequisites) if meta.prerequisites else "없음"
                lines.append(f"- {name}: {meta.description} (선행: {prereqs})")
        return "\n".join(lines)

    def retry_strategies_for(self, tool_name: str) -> List[str]:
        return self.get(tool_name).retry_strategies


registry = ToolRegistry()
