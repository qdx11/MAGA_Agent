# MAGA Agent — Manufacturing Analysis General Assistant

## 실행 방법

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
export OPENAI_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.openai.com/v1   # 사내 게이트웨이면 교체
export LLM_MODEL=gpt-4o-mini

# 3. 실행
python run_example.py

# 4. Streamlit UI
streamlit run streamlit_app.py
```

## 구조

```
maga/
├── enterprise_agent/
│   ├── core/
│   │   ├── llm_client.py      # ResilientLLMClient (CircuitBreaker + fallback)
│   │   ├── tool_registry.py   # 동적 Tool Registry (tools.yaml 기반)
│   │   └── tracer.py          # 노드별 실행 시간/에러 추적
│   ├── graph/
│   │   ├── state.py           # AgentState v4
│   │   ├── supervisor.py      # [LLM] 의도 파악
│   │   ├── planner.py         # [LLM] 플랜 생성 + 하드가드
│   │   ├── memory.py          # [코드] 캐시 + Schema 매칭
│   │   ├── executor.py        # [코드] 스텝별 툴 실행
│   │   ├── nodes.py           # [혼합] step_guard, replanner, critic, formatter
│   │   └── builder.py         # 그래프 조립
│   └── tools/
│       ├── excel/
│       │   └── mock_tools.py  # Mock → 실제 구현으로 교체 예정
│       └── mes/
│           └── mock_tools.py  # Mock → 실제 구현으로 교체 예정
├── config/
│   └── tools.yaml             # 툴 등록/선행조건/권한 관리
├── run_example.py             # 실행 진입점
├── streamlit_app.py           # UI
└── requirements.txt
```

## 그래프 흐름

```
supervisor → planner → memory → executor → step_guard
                                    ↑           │
                                    │    ┌───────┴───────┬──────────┐
                                    │    ↓               ↓          ↓
                                    ├─ executor      replanner    critic
                                    │  (다음 스텝)       │          │
                                    │                   ↓    ┌─────┴──────┐
                                    └─────────────── executor replanner  formatter → END
```

## 새 툴 추가 방법

1. `enterprise_agent/tools/` 에 구현체 작성
2. `config/tools.yaml` 에 항목 추가
3. 끝 — 코드 수정 없이 Planner/Executor 자동 반영

## Mock → 실제 구현 교체 순서

1. `ExcelStructureParser` (openpyxl 기반)
2. `HeaderDetector`
3. `CrossTableFlattener`
4. `OutlierDetector`
5. `ExcelCompareTool`
6. `MESQueryTool` (DynamoDB 연결)
