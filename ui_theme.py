# ui_theme.py — 초경량 테마 스타일시트 모듈
# - HTML 태그 오류 원천 차단: Streamlit 네이티브 컴포넌트(st.container, st.metric) 전용
# - 과도한 리플로우를 유발하는 DOM 조작 배제, 깔끔하고 차분한 학술 스타일링

import streamlit as st


def apply_theme(is_dark: bool = False):
    """최소한의 가벼운 CSS만 주입하여 브라우저 렌더링 지연(Lag)을 방지합니다."""
    if is_dark:
        bg_card = "#1e293b"
        border_c = "#334155"
        text_sub = "#94a3b8"
        accent_c = "#38bdf8"
    else:
        bg_card = "#ffffff"
        border_c = "#e2e8f0"
        text_sub = "#64748b"
        accent_c = "#2563eb"

    css = f"""
    <style>
        /* 폰트 및 여백 최적화 */
        html, body, [class*="css"] {{
            font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 15px;
        }}
        
        /* 버튼 가시성 및 크기 */
        div.stButton > button {{
            border-radius: 8px;
            font-weight: 600;
            padding: 6px 16px;
        }}
        div.stButton > button[kind="primary"] {{
            background-color: {accent_c} !important;
            border-color: {accent_c} !important;
            color: #ffffff !important;
        }}

        /* 메트릭 카드 상하 여백 */
        div[data-testid="stMetric"] {{
            background-color: {bg_card};
            border: 1px solid {border_c};
            padding: 14px 18px;
            border-radius: 10px;
        }}
        div[data-testid="stMetricLabel"] {{
            font-size: 13px !important;
            color: {text_sub} !important;
            font-weight: 600;
        }}
        div[data-testid="stMetricValue"] {{
            font-size: 26px !important;
            font-weight: 700;
        }}

        /* 설명 박스 스타일 */
        .term-explain-box {{
            background-color: {bg_card};
            border-left: 4px solid {accent_c};
            border-radius: 6px;
            padding: 12px 16px;
            margin: 8px 0;
            font-size: 14px;
            line-height: 1.6;
        }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
