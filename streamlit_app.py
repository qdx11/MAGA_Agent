import json
import os
import tempfile
from typing import Optional

import pandas as pd
import streamlit as st

from enterprise_agent.tools.excel.structure_parser import excel_structure_parser
from enterprise_agent.tools.excel.header_detector import header_detector
from enterprise_agent.tools.excel.crosstable_flattener import crosstable_flattener
from enterprise_agent.tools.excel.outlier_detector import outlier_detector
from enterprise_agent.tools.excel.compare_tool import excel_compare_tool


st.set_page_config(page_title="Enterprise Excel Agent Demo", layout="wide")

st.title("🧾 Enterprise Excel Agent Demo (v4)")
st.caption("엑셀 구조 분석 · 크로스테이블 평탄화 · 이상값 탐지 · 버전 비교")


def save_uploaded_file(uploaded_file, suffix: str = ".xlsx") -> str:
    """업로드된 파일을 임시 디렉터리에 저장하고 경로를 반환."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(uploaded_file.read())
    return path


tab_analyze, tab_compare = st.tabs(["📊 단일 파일 분석", "🔀 버전 비교"])


with tab_analyze:
    st.subheader("단일 엑셀 파일 분석")

    uploaded = st.file_uploader("엑셀 파일 업로드", type=["xlsx", "xlsm"])
    if uploaded is not None:
        tmp_path = save_uploaded_file(uploaded)
        st.info(f"임시 경로: `{tmp_path}`")

        # 1) 구조 파싱
        st.markdown("#### 1️⃣ 구조 파싱 (ExcelStructureParser)")
        struct_json = excel_structure_parser.invoke({"file_path": tmp_path})
        struct = json.loads(struct_json)
        if struct["status"] != "success":
            st.error(struct.get("message", "구조 파싱 실패"))
        else:
            sheets = [s["name"] for s in struct["data"].get("sheets", [])]
            if not sheets:
                st.warning("시트가 없습니다.")
            else:
                sheet = st.selectbox("분석할 시트 선택", sheets)

                # 2) 헤더 감지
                st.markdown("#### 2️⃣ 헤더 감지 (HeaderDetector)")
                header_json = header_detector.invoke(
                    {
                        "excel_structure": struct_json,
                        "sheet_name": sheet,
                    }
                )
                header = json.loads(header_json)
                if header["status"] != "success":
                    st.error(header.get("message", "헤더 감지 실패"))
                else:
                    hdata = header["data"]
                    st.write("헤더 행:", hdata["header_rows"])
                    st.write("테이블 타입:", hdata["table_type"], "(신뢰도:", hdata["table_type_confidence"], ")")

                    # 3) 원시 시트 미리보기
                    st.markdown("#### 3️⃣ 원본 시트 미리보기")
                    df_preview = pd.read_excel(tmp_path, sheet_name=sheet, engine="openpyxl")
                    st.dataframe(df_preview.head(20), use_container_width=True)

                    # 4) 크로스테이블 평탄화 (선택)
                    st.markdown("#### 4️⃣ 크로스테이블 평탄화 (CrossTableFlattener)")
                    if hdata["table_type"] == "crosstable":
                        id_col_count = st.number_input(
                            "왼쪽에서 ID 로 쓸 열 개수",
                            min_value=1,
                            max_value=5,
                            value=1,
                            step=1,
                        )
                        if st.button("크로스테이블 평탄화 실행"):
                            flat_json = crosstable_flattener.invoke(
                                {
                                    "excel_structure": struct_json,
                                    "header_info": header_json,
                                    "sheet_name": sheet,
                                    "id_col_count": int(id_col_count),
                                }
                            )
                            flat = json.loads(flat_json)
                            if flat["status"] != "success":
                                st.error(flat.get("message", "평탄화 실패"))
                            else:
                                fdata = flat["data"]
                                st.success("평탄화 완료")
                                df_flat = pd.DataFrame(fdata["rows"])
                                st.dataframe(df_flat.head(50), use_container_width=True)

                                # 5) 이상값 탐지
                                st.markdown("#### 5️⃣ 이상값 탐지 (OutlierDetector)")
                                cols = list(df_flat.columns)
                                num_cols = df_flat.select_dtypes(include="number").columns.tolist()
                                target_col = st.selectbox(
                                    "이상값을 분석할 컬럼 (선택)",
                                    ["(전체 수치 컬럼)"] + num_cols,
                                )
                                if st.button("이상값 분석 실행"):
                                    table_json = json.dumps({"data": fdata}, ensure_ascii=False)
                                    out_json = outlier_detector.invoke(
                                        {
                                            "table_json": table_json,
                                            "target_column": None if target_col == "(전체 수치 컬럼)" else target_col,
                                        }
                                    )
                                    out = json.loads(out_json)
                                    if out["status"] != "success":
                                        st.error(out.get("message", "이상값 분석 실패"))
                                    else:
                                        odata = out["data"]
                                        st.write("분석 컬럼:", odata["columns"])
                                        st.write("총 이상값 개수:", odata["total_outlier_count"])
                                        st.json(odata["outliers"])
                    else:
                        st.info("테이블 타입이 'database' 로 판정되어 평탄화는 생략합니다.")


with tab_compare:
    st.subheader("두 엑셀 버전 비교")

    col1, col2 = st.columns(2)
    with col1:
        up_base = st.file_uploader("기준 파일 업로드 (old)", type=["xlsx", "xlsm"], key="base")
    with col2:
        up_target = st.file_uploader("비교 대상 파일 업로드 (new)", type=["xlsx", "xlsm"], key="target")

    sheet_name = st.text_input("시트 이름", value="Sheet1")
    key_cols_text = st.text_input("키 컬럼들 (콤마로 구분, 비우면 공통 컬럼 전체 사용)", value="")

    if st.button("비교 실행") and up_base is not None and up_target is not None:
        base_path = save_uploaded_file(up_base)
        target_path = save_uploaded_file(up_target)

        key_columns: Optional[list[str]]
        if key_cols_text.strip():
            key_columns = [c.strip() for c in key_cols_text.split(",") if c.strip()]
        else:
            key_columns = None

        comp_json = excel_compare_tool.invoke(
            {
                "base_file": base_path,
                "target_file": target_path,
                "sheet_name": sheet_name,
                "key_columns": key_columns or [],
            }
        )
        comp = json.loads(comp_json)
        if comp["status"] != "success":
            st.error(comp.get("message", "비교 실패"))
        else:
            cdata = comp["data"]
            st.markdown("#### 요약")
            st.json(cdata["summary"])

            with st.expander("➕ 추가된 행 (added_rows)"):
                if cdata["added_rows"]:
                    st.dataframe(pd.DataFrame(cdata["added_rows"]), use_container_width=True)
                else:
                    st.write("없음")

            with st.expander("➖ 삭제된 행 (removed_rows)"):
                if cdata["removed_rows"]:
                    st.dataframe(pd.DataFrame(cdata["removed_rows"]), use_container_width=True)
                else:
                    st.write("없음")

            with st.expander("✏️ 변경된 셀 (changed_cells)"):
                if cdata["changed_cells"]:
                    st.dataframe(pd.DataFrame(cdata["changed_cells"]), use_container_width=True)
                else:
                    st.write("없음")

