"""JSON 추출 공통 유틸 — LLM 응답에서 JSON 파싱"""
from __future__ import annotations
import json
import re


def extract_json(content: str) -> dict:
    """
    LLM 응답에서 JSON 객체를 추출한다.
    코드 블록(```json ... ```) 또는 순수 JSON 모두 처리.
    """
    content = content.strip()

    # ```json ... ``` 또는 ``` ... ``` 블록 우선 시도
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", content)
    if match:
        return json.loads(match.group(1).strip())

    # 중괄호로 시작하는 JSON 객체 직접 추출
    match = re.search(r"\{[\s\S]+\}", content)
    if match:
        return json.loads(match.group(0))

    # 마지막 시도: 전체 파싱
    return json.loads(content)
