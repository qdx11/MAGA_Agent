from __future__ import annotations
import os
import httpx
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()  # .env 파일 자동 로드


def create_llm() -> ChatOpenAI:
    """
    환경변수 기반 LLM 생성.
    사내 프록시 우회 (no_proxy=*) + SSL 인증서 설정 포함.

    .env 파일에 아래 변수 설정:
        LLM_API_KEY=sk-xxx
        LLM_BASE_URL=https://api.openai.com/v1   # 사내 게이트웨이면 교체
        LLM_MODEL=gpt-4o-mini
        LLM_CERT_PATH=                            # 빈 값이면 verify=False
    """

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # 사내 프록시 우회가 필요한 경우 LLM_CERT_PATH 환경변수 설정
    cert_path = os.getenv("LLM_CERT_PATH")
    if cert_path or os.getenv("LLM_NO_PROXY"):
        os.environ["no_proxy"] = "*"
        http_client = httpx.Client(verify=cert_path or False, proxy=None)
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            http_client=http_client,
            timeout=60,
            max_retries=2,
        )

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=60,
        max_retries=2,
    )
