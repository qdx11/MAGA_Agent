import json
import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from enterprise_agent.tools.excel.structure_parser import excel_structure_parser
from enterprise_agent.tools.excel.header_detector import header_detector
from enterprise_agent.core.llm_client import create_llm
from enterprise_agent.graph.builder import build_graph
from enterprise_agent.graph.state import make_default_state

# ── 페이지 설정 ──────────────────────────────────────────
st.set_page_config(
    page_title="MAGA — Excel AI Agent",
    page_icon="📊",
    layout="wide",
)

st.title("📊 MAGA — Excel AI Agent")
st.caption("파일을 올리면 구조를 자동 파악하고, 채팅으로 자유롭게 질문하세요.")

# ── 헬퍼 ────────────────────────────────────────────────
def save_uploaded(file) -> str:
    suffix = os.path.splitext(file.name)[-1]
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(file.read())
    return path

def parse_json(s: str) -> dict:
    try:
        return json.loads(s)
    except Exception:
        return {"status": "error", "message": "파싱 실패"}

def parse_file(path: str, filename: str) -> dict:
    """업로드 즉시: 구조 파싱 + 시트별 헤더 감지"""
    result = {"filename": filename, "path": path}

    struct_json = excel_structure_parser.invoke({"file_path": path})
    struct = parse_json(struct_json)
    result["struct_json"] = struct_json
    result["struct"] = struct

    if struct.get("status") != "success":
        result["error"] = struct.get("message", "구조 파악 실패")
        return result

    headers = {}
    for sheet in struct["data"]["sheets"]:
        h_json = header_detector.invoke({
            "excel_structure": struct_json,
            "sheet_name": sheet["name"],
        })
        headers[sheet["name"]] = {
            "json": h_json,
            "data": parse_json(h_json).get("data", {}),
        }
    result["headers"] = headers
    result["sheet_names"] = [s["name"] for s in struct["data"]["sheets"]]
    return result

def build_context(parsed: dict) -> str:
    """파싱 결과 → LLM 컨텍스트 요약 텍스트"""
    if not parsed or parsed.get("error"):
        return ""
    struct = parsed["struct"]["data"]
    lines = [f"파일명: {parsed['filename']}"]
    for sheet in struct["sheets"]:
        name = sheet["name"]
        hdata = parsed["headers"].get(name, {}).get("data", {})
        lines.append(f"\n[시트: {name}]")
        lines.append(f"  크기: {sheet['max_row']}행 × {sheet.get('max_col', sheet.get('max_column', '?'))}열")
        lines.append(f"  데이터 시작: {sheet.get('data_start', {}).get('cell', '?') if isinstance(sheet.get('data_start'), dict) else f"행{sheet['row_index'][0]['row']}" if sheet.get('row_index') else '?'}")
        lines.append(f"  병합셀: {len(sheet.get('merged_cells', []))}개")
        lines.append(f"  테이블 타입: {hdata.get('table_type','?')} (신뢰도 {hdata.get('table_type_confidence',0)*100:.0f}%)")
        lines.append(f"  컬럼: {hdata.get('column_names', [])}")
        comments = struct.get("comments", {}).get(name, {}) or sheet.get("comments", {})
        if comments:
            lines.append(f"  메모: {dict(list(comments.items())[:5])}")
    return "\n".join(lines)

def show_file_info(parsed: dict):
    """파싱 결과 UI 표시"""
    if parsed.get("error"):
        st.error(f"파싱 실패: {parsed['error']}")
        return

    struct = parsed["struct"]["data"]
    sheets = struct["sheets"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("시트 수", struct["sheet_count"])
    c2.metric("형식", struct["file_format"].upper())
    c3.metric("병합셀", sum(len(s.get("merged_cells", [])) for s in sheets))
    c4.metric("메모", sum(len(s.get("comments", {})) for s in sheets))

    if struct.get("hidden_sheets"):
        st.warning(f"숨긴 시트: {struct['hidden_sheets']}")

    for sheet in sheets:
        name = sheet["name"]
        hdata = parsed["headers"].get(name, {}).get("data", {})
        with st.expander(
            f"📋 {name} — {sheet['max_row']}행 × {sheet.get('max_col', sheet.get('max_column', '?'))}열",
            expanded=(len(sheets) == 1),
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**데이터 시작**: `{sheet.get('data_start', {}).get('cell', '?') if isinstance(sheet.get('data_start'), dict) else f"행{sheet['row_index'][0]['row']}" if sheet.get('row_index') else '?'}`")
                st.markdown(f"**테이블 타입**: `{hdata.get('table_type','?')}` "
                           f"({hdata.get('table_type_confidence',0)*100:.0f}%)")
                if hdata.get("is_multi_header"):
                    st.markdown("**멀티헤더 감지** ✅")
            with col2:
                cols = hdata.get("column_names", [])
                if cols:
                    st.markdown(f"**컬럼**: {', '.join(str(c) for c in cols[:8])}"
                               f"{'...' if len(cols)>8 else ''}")

            comments = struct.get("comments", {}).get(name, {}) or sheet.get("comments", {})
            if comments:
                st.markdown("**메모:**")
                for coord, text in list(comments.items())[:5]:
                    st.caption(f"`{coord}`: {text}")

            try:
                df_raw = pd.read_excel(parsed["path"], sheet_name=name, header=None)
                # Arrow 직렬화 에러 방지: 모든 컬럼 str 변환
                df_display = df_raw.head(10).astype(str)
                st.dataframe(df_display, use_container_width=True)
            except Exception:
                pass


# ── 파일 업로드 ──────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    file1 = st.file_uploader(
        "📂 파일 1 (분석 / 기준 파일)",
        type=["xlsx", "xlsm", "xls"],
        key="file1",
    )
with col2:
    file2 = st.file_uploader(
        "📂 파일 2 (버전 비교 시 업로드)",
        type=["xlsx", "xlsm", "xls"],
        key="file2",
    )

if not file1:
    st.info("📂 엑셀 파일을 업로드하면 AI가 자동으로 구조를 파악합니다.")
    st.stop()

# ── 업로드 즉시 파싱 (파일 바뀌면 재파싱) ────────────────
key1 = f"{file1.name}_{file1.size}" if file1 else None
if not key1:
    st.stop()
key2 = f"{file2.name}_{file2.size}" if file2 else None

if st.session_state.get("key1") != key1:
    st.session_state.key1 = key1
    st.session_state.parsed1 = None
    st.session_state.messages = []  # 파일 바뀌면 대화 초기화

if file2 and st.session_state.get("key2") != key2:
    st.session_state.key2 = key2
    st.session_state.parsed2 = None

if st.session_state.get("parsed1") is None:
    with st.spinner(f"📐 {file1.name} 구조 파악 중..."):
        path1 = save_uploaded(file1)
        st.session_state.parsed1 = parse_file(path1, file1.name)
        st.session_state.path1 = path1

parsed1 = st.session_state.parsed1
path1 = st.session_state.path1

parsed2, path2 = None, None
if file2:
    if st.session_state.get("parsed2") is None:
        with st.spinner(f"📐 {file2.name} 구조 파악 중..."):
            path2 = save_uploaded(file2)
            st.session_state.parsed2 = parse_file(path2, file2.name)
            st.session_state.path2 = path2
    parsed2 = st.session_state.parsed2
    path2 = st.session_state.path2

st.divider()

# ── 파싱 결과 표시 ────────────────────────────────────────
if parsed2:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"#### 📄 {parsed1['filename']}")
        show_file_info(parsed1)
    with col2:
        st.markdown(f"#### 📄 {parsed2['filename']}")
        show_file_info(parsed2)
else:
    st.markdown(f"#### 📄 {parsed1['filename']}")
    show_file_info(parsed1)

st.divider()

# ── 채팅 ─────────────────────────────────────────────────
if parsed2:
    st.markdown("### 💬 질문하기")
    st.caption("예: 뭐가 달라졌어? / 두 파일 비교해줘 / 이상값 찾아줘")
else:
    st.markdown("### 💬 질문하기")
    st.caption("예: 이상값 찾아줘 / 3월 데이터 보여줘 / 전체 요약해줘")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("질문을 입력하세요..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("분석 중..."):
            try:
                # 파싱 결과를 컨텍스트로 주입 → 재파싱 불필요
                ctx = build_context(parsed1)
                if parsed2:
                    ctx += f"\n\n[비교 파일 구조]\n{build_context(parsed2)}"

                full_msg = f"[파일 구조 정보 — 이미 파싱 완료]\n{ctx}\n\n[사용자 질문]\n{prompt}"

                if "llm" not in st.session_state:
                    st.session_state.llm = create_llm()
                if "graph" not in st.session_state:
                    st.session_state.graph, st.session_state.tracer = build_graph(
                        st.session_state.llm, session_id="streamlit"
                    )
                graph = st.session_state.graph
                tracer = st.session_state.tracer

                state = make_default_state(
                    message=full_msg,
                    files=[path1] + ([path2] if path2 else []),
                    user_id="demo",
                    role="analyst",
                    permissions=["excel:read", "mes:query"],
                )
                # 이미 파싱된 구조 주입 → ExcelStructureParser 재실행 방지
                state["excel_schema"] = parsed1["struct"].get("data")

                config = {"configurable": {"thread_id": "streamlit"}}
                final = graph.invoke(state, config=config)
                answer = final.get("final_answer", "답변을 생성하지 못했습니다.")

            except Exception as e:
                answer = f"오류 발생: {e}"

        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
