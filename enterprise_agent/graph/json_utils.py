"""JSON 추출 공통 유틸 — LLM 응답에서 JSON 파싱 (Qwen-Coder 대응 강화)"""
from __future__ import annotations
import json
import re


def _try_parse(s: str) -> dict | None:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_json(content: str) -> dict:
    """
    LLM 응답에서 JSON 객체를 추출한다.
    Qwen-Coder처럼 코드블록·설명 텍스트가 섞인 출력을 적극 처리.

    시도 순서:
    1. ```json ... ``` 블록
    2. ``` ... ``` 블록 (언어 태그 없음)
    3. 중괄호 기준 가장 바깥 JSON 객체 (탐욕적 매칭)
    4. 중괄호 기준 첫 번째 완결된 JSON 객체 (괄호 카운팅)
    5. 전체 문자열 파싱
    """
    content = content.strip()

    # 1. ```json ... ``` 블록
    match = re.search(r"```json\s*([\s\S]+?)```", content, re.IGNORECASE)
    if match:
        result = _try_parse(match.group(1).strip())
        if result is not None:
            return result

    # 2. ``` ... ``` 블록 (언어 태그 없음)
    match = re.search(r"```\s*([\s\S]+?)```", content)
    if match:
        result = _try_parse(match.group(1).strip())
        if result is not None:
            return result

    # 3. 괄호 카운팅으로 완결된 첫 JSON 객체 추출
    start = content.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(content[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    result = _try_parse(content[start:i + 1])
                    if result is not None:
                        return result
                    break

    # 4. 전체 문자열 파싱 (마지막 시도)
    return json.loads(content)
