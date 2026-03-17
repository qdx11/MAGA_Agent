import time
from typing import Optional

from langchain_openai import ChatOpenAI


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.is_open = False

    def record_success(self) -> None:
        self.failure_count = 0
        self.is_open = False

    def record_failure(self) -> None:
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
    ) -> None:
        self.primary = ChatOpenAI(
            base_url=primary_base_url,
            api_key=primary_api_key,
            model=primary_model,
            timeout=timeout,
            max_retries=0,
        )
        self.fallback = None
        if fallback_base_url and fallback_api_key:
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

        if self.fallback is not None:
            return self.fallback.invoke(messages, **kwargs)

        raise RuntimeError("LLM 서비스에 연결할 수 없습니다.")
