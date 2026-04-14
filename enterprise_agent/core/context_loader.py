"""전역 에이전트 컨텍스트 로더 — config/agent_context.md를 런타임에 읽어 주입"""
from __future__ import annotations
from pathlib import Path

_CONTEXT_PATH = Path(__file__).parent.parent.parent / "config" / "agent_context.md"
_cache: str | None = None


def get_global_context() -> str:
    """agent_context.md를 읽어 반환. 파일 없으면 빈 문자열."""
    global _cache
    if _cache is None:
        try:
            _cache = _CONTEXT_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            _cache = ""
    return _cache


def inject(system_prompt: str) -> str:
    """시스템 프롬프트 앞에 전역 컨텍스트를 prepend."""
    ctx = get_global_context()
    if not ctx:
        return system_prompt
    return f"{ctx}\n\n---\n\n{system_prompt}"


def reload() -> None:
    """컨텍스트 캐시 초기화 (파일 수정 후 반영 시 사용)."""
    global _cache
    _cache = None
