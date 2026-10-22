# 오늘 뭐 복습하지? — AI 시험 준비 도우미 (myapp.py)
# - 경영과인공지능 시험 대비 맞춤형 복습 앱
# - 2-C단계: Groq 기본 제공자, AI 용어 카드 및 플래시카드, 객관식 실전 퀴즈(문항수/난이도/타이머/1문제씩/오답재풀이)

import importlib
import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from openai import OpenAI

import storage
import ui_theme

importlib.reload(storage)
importlib.reload(ui_theme)

# ── 1. 기본 환경 및 5대 AI 제공자 설정 ─────────────────────────
ENV_FILE = Path(__file__).parent / ".env"


def read_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return values


ENV = read_env(ENV_FILE)

# 사용자 요구에 따라 기본 제공자를 초고속 Groq로 우선 배치
PROVIDERS = {
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": ENV.get("GROQ_API_KEY", ""),
        "default_model": ENV.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
        "models": ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b"],
        "headers": {},
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": ENV.get("GEMINI_API_KEY", ""),
        "default_model": ENV.get("GEMINI_MODEL", "gemini-3.8-flash"),
        "models": ["gemini-3.8-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        "headers": {},
    },
    "한림 HAI": {
        "base_url": ENV.get("HAI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway"),
        "api_key": ENV.get("HAI_API_KEY", ""),
        "default_model": ENV.get("HAI_MODEL", "gemini-3.8-flash"),
        "models": ["gemini-3.8-flash"],
        "headers": {},
    },
    "OpenAI GPT": {
        "base_url": "https://api.openai.com/v1",
        "api_key": ENV.get("OPENAI_API_KEY", ""),
        "default_model": ENV.get("OPENAI_MODEL", "gpt-4o-mini"),
        "models": ["gpt-4o-mini", "gpt-4o"],
        "headers": {},
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": ENV.get("OPENROUTER_API_KEY", ""),
        "default_model": ENV.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"),
        "models": ["meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-chat"],
        "headers": {
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "StudyHelper",
        },
    },
}

# ── 2. Streamlit 기본 설정 및 세션 초기화 ──────────────────
st.set_page_config(
    page_title="오늘 뭐 복습하지? — AI 시험 준비 도우미",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "theme_dark" not in st.session_state:
    st.session_state.theme_dark = False
if "selected_topic" not in st.session_state:
    st.session_state.selected_topic = None
if "custom_model_inputs" not in st.session_state:
    st.session_state.custom_model_inputs = {}
if "comparison_history" not in st.session_state:
    st.session_state.comparison_history = {}
if "staging_terms" not in st.session_state:
    st.session_state.staging_terms = None
if "flashcard_index" not in st.session_state:
    st.session_state.flashcard_index = 0
if "flashcard_revealed" not in st.session_state:
    st.session_state.flashcard_revealed = False

# 2-C 퀴즈 세션 객체
if "active_quiz" not in st.session_state:
    st.session_state.active_quiz = None

# ── 3. 단일 읽기 캐시 (렌더링당 1회만 디스크 I/O 실행) ──────
app_data = storage.load_all_app_data()
topics = app_data["topics"]
weak_terms = app_data["weak_terms"]
history = app_data["history"]
stats = app_data["stats"]

topic_names = [t["name"] for t in topics]

if st.session_state.selected_topic not in topic_names:
    st.session_state.selected_topic = topic_names[0] if topic_names else None


# ── 4. 보안 및 친절한 에러 핸들러 ──────────────────────────
def sanitize_error_message(err: Exception) -> str:
    err_str = str(err)
    for p in PROVIDERS.values():
        key = p.get("api_key")
        if key and len(key) > 6 and key in err_str:
            err_str = err_str.replace(key, "[PROTECTED_KEY]")

    lowered = err_str.lower()
    if "401" in err_str or "unauthorized" in lowered or "auth" in lowered:
        return "인증 오류: API 키가 올바르지 않습니다. .env 설정을 확인해 주세요."
    if "404" in err_str or "model" in lowered or "not found" in lowered:
        return "모델 오류: 모델 이름을 확인하거나 다른 모델을 선택하세요."
    if "429" in err_str or "rate limit" in lowered or "quota" in lowered:
        return "요청 한도 도달: 호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
    if "connection" in lowered or "timeout" in lowered or "network" in lowered:
        return "네트워크 오류: 인터넷 연결을 확인해 주세요."
    return f"호출 중 오류 발생: {err_str[:80]}"


# ── 5. AI 호출 함수들 (용어 설명, 카드 생성, 퀴즈 출제) ──────
def explain_term_ai(
    term: str,
    current_topic: str,
    context_text: str,
    provider_name: str,
    model_name: str,
) -> dict:
    provider_info = PROVIDERS.get(provider_name, {})
    api_key = provider_info.get("api_key")
    if not api_key:
        return {"error": "선택한 제공자의 API 키가 설정되어 있지 않습니다."}

    term_key = f"{current_topic}::{term}"
    model_key = f"{provider_name}::{model_name}"
    if term_key in st.session_state.comparison_history:
        if model_key in st.session_state.comparison_history[term_key]:
            return st.session_state.comparison_history[term_key][model_key]

    system_prompt = (
        "너는 경영학과 인공지능 분야의 전문 학습 도우미다.\n"
        "사용자가 제시한 '단 하나의 한국어 용어/어구'에 대해 명확한 한국어 설명과 "
        "제공된 강의 자료 맥락에서의 의미를 안내한다.\n"
        "규칙:\n"
        "1. 제공된 강의 자료에 해당 용어의 직접적인 설명이 없으면 '자료 맥락에서의 의미'에 "
        "'입력한 자료에는 직접적인 설명이 없습니다.'라고 명시하고 일반적인 경영/AI 맥락의 뜻을 안내한다.\n"
        "2. 반드시 아래 JSON 형식으로만 답한다:\n"
        "{\n"
        '  "ko_desc": "간단하고 정확한 한국어 설명 (1~2문장)",\n'
        '  "ru_trans": "러시아어 번역 (내부 보존용)",\n'
        '  "context_meaning": "선택한 주제와 입력된 자료 안에서의 의미"\n'
        "}"
    )

    user_msg = (
        f"[설명 요청 용어]: {term}\n"
        f"[현재 주제]: {current_topic or '일반 경영과인공지능'}\n"
        f"[관련 강의 자료 요약]:\n{context_text[:1200] if context_text else '없음'}"
    )

    t0 = time.time()
    try:
        headers = provider_info.get("headers", {})
        client = OpenAI(
            api_key=api_key,
            base_url=provider_info.get("base_url"),
            default_headers=headers if headers else None,
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
        )
        duration = round(time.time() - t0, 2)
        raw = response.choices[0].message.content.strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start : end + 1])
            res_entry = {
                "provider": provider_name,
                "model": model_name,
                "duration": duration,
                "ko_desc": data.get("ko_desc", ""),
                "context_meaning": data.get("context_meaning", ""),
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
            if term_key not in st.session_state.comparison_history:
                st.session_state.comparison_history[term_key] = {}
            st.session_state.comparison_history[term_key][model_key] = res_entry
            return res_entry
        else:
            return {"error": "모델 응답 형식이 올바르지 않습니다."}
    except Exception as e:
        return {"error": sanitize_error_message(e)}


def generate_terms_ai(
    topic_name: str,
    source: str,
    level: str,
    content: str,
    char_limit: int,
    user_keywords: list,
    provider_name: str,
    model_name: str,
) -> dict:
    provider_info = PROVIDERS.get(provider_name, {})
    api_key = provider_info.get("api_key")
    if not api_key:
        return {"error": "선택한 제공자의 API 키가 설정되어 있지 않습니다."}

    system_prompt = (
        "너는 대학 강의 자료 기반의 시험 대비 개념 요약 전문가다.\n"
        "사용자가 제공한 자료만을 근거로 하여 시험에 출제될 만한 핵심 한국어 용어 카드를 작성한다.\n"
        "[작성 규칙]\n"
        "1. 용어는 반드시 5개에서 8개 사이로 추출한다.\n"
        "2. 반드시 사용자가 입력한 텍스트에만 근거해야 하며, 자료에 없는 개념이나 사실을 절대 지어내지 않는다.\n"
        "3. 각 용어 카드마다 신뢰도(confidence)를 명시한다:\n"
        "   - high: 본문에 명확히 정의와 설명이 있는 경우\n"
        "   - medium: 본문에 언급되어 있으나 설명이 다소 함축적인 경우\n"
        "   - low: 본문 내용만으로 판단하기에 맥락이 부족하거나 추정이 개입된 경우\n"
        "4. 오직 아래 JSON 형식으로만 답한다. 마크다운 따옴표나 사족을 붙이지 않는다:\n"
        "{\n"
        '  "topic": "주제 이름",\n'
        '  "source": "출처",\n'
        '  "terms": [\n'
        "    {\n"
        '      "term": "한국어 용어",\n'
        '      "meaning": "간단한 한국어 뜻 (1~2문장)",\n'
        '      "key_point": "시험에 중요한 핵심 문장 또는 구분점",\n'
        '      "example": "강의 자료에 있는 예시 또는 사용자가 이해하기 쉬운 예시",\n'
        '      "confidence": "high / medium / low"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    sliced_content = content[:char_limit] if char_limit else content[:4000]
    user_msg = (
        f"[주제]: {topic_name}\n"
        f"[출처]: {source}\n"
        f"[학습 수준]: {level}\n"
        f"[사용자 지정 핵심 용어]: {', '.join(user_keywords) if user_keywords else '없음'}\n\n"
        f"[공부 자료 본문]:\n{sliced_content}"
    )

    try:
        headers = provider_info.get("headers", {})
        client = OpenAI(
            api_key=api_key,
            base_url=provider_info.get("base_url"),
            default_headers=headers if headers else None,
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start : end + 1])
            terms = data.get("terms", [])
            if not isinstance(terms, list) or len(terms) == 0:
                return {"error": "생성된 용어 카드 목록이 비어 있습니다."}
            return {"terms": terms, "topic": topic_name}
        else:
            return {"error": "AI 응답 형식 문제: 유효한 JSON을 추출하지 못했습니다."}
    except Exception as e:
        return {"error": sanitize_error_message(e)}


def generate_quiz_ai(
    topic_name: str,
    content: str,
    saved_terms: list,
    weak_terms_list: list,
    num_questions: int,
    difficulty: str,
    scope: str,
    provider_name: str,
    model_name: str,
) -> dict:
    """사용자가 저장한 자료와 카드, 약한 개념만을 바탕으로 4지선다 객관식 퀴즈를 출제합니다."""
    provider_info = PROVIDERS.get(provider_name, {})
    api_key = provider_info.get("api_key")
    if not api_key:
        return {"error": "선택한 제공자의 API 키가 설정되어 있지 않습니다."}

    system_prompt = (
        "너는 대학 경영과인공지능 과목의 시험 출제위원이다.\n"
        "사용자가 제공한 자료, 저장된 용어 카드, 약한 개념만을 근거로 하여 4지선다형 객관식 연습 문제를 출제한다.\n"
        "[출제 규칙]\n"
        f"1. 반드시 정확히 {num_questions}개의 문제를 출제한다.\n"
        "2. 각 문제마다 정확히 4개의 보기(choices)를 구성하고 정답은 단 하나여야 한다.\n"
        "3. answer_index는 정답 보기가 위치한 0, 1, 2, 3 중 하나의 정수여야 하며, 정답 위치가 특정 번호로 쏠리지 않고 고르게 분포되어야 한다.\n"
        "4. '옳은 것은?', '옳지 않은 것은?', '가장 적절한 것은?' 형식을 골고루 섞어 출제한다.\n"
        "5. 반드시 제공된 자료에 있는 사실에만 입각하며, 외래 이론이나 지어낸 수치를 근거로 삼지 않는다.\n"
        "6. 오직 아래 JSON 형식으로만 답한다:\n"
        "{\n"
        '  "topic": "주제 이름",\n'
        '  "questions": [\n'
        "    {\n"
        '      "question": "한국어 문제",\n'
        '      "choices": ["보기 1", "보기 2", "보기 3", "보기 4"],\n'
        '      "answer_index": 0,\n'
        '      "explanation": "짧은 한국어 해설 및 정답인 이유",\n'
        '      "related_term": "관련 용어 (오답 시 복습할 용어)",\n'
        '      "difficulty": "easy / medium / hard"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    terms_summary = "\n".join([f"- {t.get('term')}: {t.get('meaning')}" for t in saved_terms[:10]])
    weak_summary = ", ".join([w.get("term") for w in weak_terms_list if w.get("topic") == topic_name])

    user_msg = (
        f"[주제]: {topic_name}\n"
        f"[출제 문항 수]: {num_questions}문제\n"
        f"[요청 난이도]: {difficulty}\n"
        f"[출제 범위]: {scope}\n"
        f"[저장된 핵심 용어 카드]:\n{terms_summary or '없음'}\n"
        f"[오답 누적 약한 개념]: {weak_summary or '없음'}\n\n"
        f"[공부 자료 본문]:\n{content[:4000]}"
    )

    try:
        headers = provider_info.get("headers", {})
        client = OpenAI(
            api_key=api_key,
            base_url=provider_info.get("base_url"),
            default_headers=headers if headers else None,
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start : end + 1])
            raw_questions = data.get("questions", [])
            valid_questions = []

            for q in raw_questions:
                choices = q.get("choices", [])
                ans_idx = q.get("answer_index")
                if (
                    isinstance(choices, list)
                    and len(choices) == 4
                    and isinstance(ans_idx, int)
                    and 0 <= ans_idx <= 3
                    and q.get("question")
                ):
                    valid_questions.append(q)

            if len(valid_questions) == 0:
                return {"error": "생성된 문항의 보기 형식(4지선다)이 올바르지 않습니다. 다시 시도해 주세요."}
            return {"questions": valid_questions, "topic": topic_name}
        else:
            return {"error": "AI 응답 형식 문제: JSON 구조를 파싱하지 못했습니다."}
    except Exception as e:
        return {"error": sanitize_error_message(e)}


# ── 6. 사이드바 구현 ────────────────────────────────────────
with st.sidebar:
    st.markdown("### 오늘 뭐 복습하지?")
    st.caption("AI 시험 준비 도우미 — 경영과인공지능")

    # 테마 전환
    theme_choice = st.radio(
        "화면 테마",
        ["밝은 모드", "어두운 모드"],
        index=1 if st.session_state.theme_dark else 0,
        horizontal=True,
    )
    st.session_state.theme_dark = (theme_choice == "어두운 모드")
    ui_theme.apply_theme(st.session_state.theme_dark)

    st.markdown("---")
    st.subheader("학습 주제 선택")

    if topic_names:
        current_idx = topic_names.index(st.session_state.selected_topic) if st.session_state.selected_topic in topic_names else 0
        selected_name = st.selectbox(
            "선택된 주제",
            topic_names,
            index=current_idx,
            label_visibility="collapsed",
        )
        st.session_state.selected_topic = selected_name
    else:
        st.info("저장된 주제가 없습니다. '자료 입력' 탭에서 첫 자료를 등록하세요.")

    st.markdown("---")
    st.subheader("저장된 주제 목록")
    if topics:
        for t in topics:
            card_count = len(t.get("saved_terms", []))
            chars = t.get("char_count", 0)
            is_active = "✓ " if t["name"] == st.session_state.selected_topic else "- "
            st.markdown(f"{is_active}**{t['name']}** ({chars:,}자, 카드 {card_count}개)")
    else:
        st.caption("등록된 주제가 없습니다.")

    st.markdown("---")
    st.subheader("약한 개념 요약")
    st.write(f"총 누적 오답: **{len(weak_terms)}개**")
    if weak_terms:
        sorted_weak = sorted(weak_terms, key=lambda x: x.get("wrong_count", 0), reverse=True)
        for idx, item in enumerate(sorted_weak[:3], 1):
            st.markdown(f"{idx}. {item.get('term')} ({item.get('wrong_count')}회 오답)")
    else:
        st.caption("오답 개념이 없습니다.")

    st.markdown("---")
    st.subheader("AI 설정")
    provider_name = st.selectbox("AI 제공자", list(PROVIDERS.keys()), index=0)
    current_provider = PROVIDERS[provider_name]

    available_models = list(current_provider["models"])
    if "[직접 입력]" not in available_models:
        available_models.append("[직접 입력]")

    default_model_choice = current_provider["default_model"]
    default_idx = available_models.index(default_model_choice) if default_model_choice in available_models else 0

    selected_model_option = st.selectbox("모델 선택", available_models, index=default_idx)

    if selected_model_option == "[직접 입력]":
        current_model = st.text_input(
            "모델 이름 직접 입력",
            value=st.session_state.custom_model_inputs.get(provider_name, default_model_choice),
        )
        st.session_state.custom_model_inputs[provider_name] = current_model.strip()
    else:
        current_model = selected_model_option

    st.caption(f"적용 모델: `{current_model}`")

    if current_provider["api_key"]:
        st.success(f"{provider_name} 연결됨")
    else:
        st.warning(f"{provider_name} 키가 설정되어 있지 않습니다.")

    st.markdown("---")
    with st.expander("데이터 초기화", expanded=False):
        st.caption("모든 주제와 퀴즈 기록을 삭제합니다. 실행 전 자동 백업됩니다.")
        confirm_reset = st.checkbox("정말 삭제할까요?")
        if st.button("전체 데이터 삭제", disabled=not confirm_reset):
            backup_folder = storage.backup_and_reset_all_data()
            st.session_state.selected_topic = None
            st.session_state.comparison_history = {}
            st.session_state.staging_terms = None
            st.session_state.active_quiz = None
            st.success(f"초기화되었습니다. (백업: {backup_folder})")
            st.rerun()

current_topic_data = None
if st.session_state.selected_topic:
    for t in topics:
        if t["name"] == st.session_state.selected_topic:
            current_topic_data = t
            break


# ── 7. 모델 결과 비교 렌더링 헬퍼 함수 ──────────────────────
def render_term_comparison(term: str, current_topic_name: str):
    term_key = f"{current_topic_name}::{term}"
    history_dict = st.session_state.comparison_history.get(term_key, {})

    if not history_dict:
        return

    st.markdown("##### 모델별 결과 비교")
    cols = st.columns(len(history_dict))
    for idx, (m_key, res) in enumerate(history_dict.items()):
        with cols[idx]:
            with st.container(border=True):
                st.markdown(f"**{res['provider']}**")
                st.caption(f"모델: `{res['model']}` | 소요: {res['duration']}초")
                st.markdown(f"**한국어 설명:**\n{res['ko_desc']}")
                st.markdown(f"**자료 안에서의 의미:**\n{res['context_meaning']}")
                st.caption(f"요청 시각: {res.get('timestamp', '-')}")

                if st.button("이 설명으로 저장", key=f"save_res_{term}_{m_key}"):
                    storage.add_or_update_weak_term(term, current_topic_name)
                    st.success(f"'{term}'이(가) 약한 개념에 저장되었습니다.")
                    st.rerun()


# ── 8. 메인 화면 탭 구성 ───────────────────────────────────
tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "홈 대시보드",
    "자료 입력",
    "용어 카드",
    "연습 문제",
    "복습 현황",
    "도움말",
])

# ── [탭 0: 홈 대시보드] ─────────────────────────────────────
with tab0:
    st.subheader("학습 대시보드")
    st.caption("경영과인공지능 핵심 강의 자료 기반의 복습 현황")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(label="오늘 복습할 개념", value=f"{stats['total_weak_terms']}개")
    with m2:
        st.metric(label="저장된 주제", value=f"{stats['total_topics']}개")
    with m3:
        st.metric(label="용어 카드", value=f"{stats['total_saved_terms']}개")
    with m4:
        acc_text = f"{stats['overall_accuracy']:.1f}%" if stats['total_questions'] > 0 else "—"
        st.metric(label="퀴즈 정답률", value=acc_text)

    st.markdown("---")

    col_left, col_right = st.columns([1, 1])

    with col_left:
        with st.container(border=True):
            st.markdown("#### 가장 약한 개념 Top 3")
            if stats["top3_weak"]:
                for idx, w in enumerate(stats["top3_weak"], 1):
                    term_str = w.get("term")
                    topic_str = w.get("topic")
                    c_t1, c_t2 = st.columns([4, 1])
                    with c_t1:
                        st.markdown(f"**{idx}. {term_str}** ({w.get('wrong_count')}회 오답)")
                        st.caption(f"주제: {topic_str} | 최근 오답: {w.get('last_wrong_at', '-')}")
                    with c_t2:
                        if st.button("설명", key=f"dash_explain_{idx}"):
                            st.session_state[f"run_dash_explain_{idx}"] = True

                    if st.session_state.get(f"run_dash_explain_{idx}"):
                        ctx_text = ""
                        for t in topics:
                            if t.get("name") == topic_str:
                                ctx_text = t.get("content", "")
                                break
                        with st.spinner(f"{provider_name}({current_model})에게 설명 요청 중..."):
                            exp_data = explain_term_ai(term_str, topic_str, ctx_text, provider_name, current_model)
                        if "error" in exp_data:
                            st.error(exp_data["error"])
                        else:
                            render_term_comparison(term_str, topic_str)
            else:
                st.info("누적된 오답 개념이 없습니다.")

    with col_right:
        with st.container(border=True):
            st.markdown("#### 추천 복습 주제")
            rec = stats["recommended_topic"]
            if rec:
                st.markdown(f"**{rec.get('name')}**")
                st.write(f"- 출처: {rec.get('source', '직접 입력')}")
                st.write(f"- 학습 수준: {rec.get('level', '-')}")
                st.write(f"- 마지막 학습: {rec.get('updated_at', '-')}")
                st.write(f"- 저장된 카드: {len(rec.get('saved_terms', []))}개 (본문 {rec.get('char_count', 0):,}자)")
                st.caption("추천 사유: 오답 개념이 포함되어 있거나 학습한 지 오래된 주제입니다.")
            else:
                st.info("등록된 주제가 없습니다. '자료 입력' 탭에서 새 자료를 등록하세요.")

    with st.container(border=True):
        st.markdown("#### 최근 학습 활동")
        recent = stats["recent_history"]
        if recent:
            for item in reversed(recent):
                st.markdown(
                    f"- {item.get('date')} | **{item.get('topic')}** — "
                    f"정답: {item.get('correct_count')}/{item.get('total_questions')} ({item.get('accuracy', 0):.1f}%) "
                    f"| 오답 용어: {', '.join(item.get('wrong_terms', [])) or '없음'}"
                )
        else:
            st.caption("퀴즈 풀이 기록이 없습니다.")


# ── [탭 1: 자료 입력] ───────────────────────────────────────
with tab1:
    st.subheader("공부 자료 입력")
    st.caption("수업 슬라이드 내용이나 요약 노트를 입력하고 저장하세요.")

    col_mode1, col_mode2 = st.columns([2, 1])
    with col_mode1:
        is_edit_mode = st.checkbox("현재 선택된 주제 내용 수정하기", value=False if not current_topic_data else True)

    init_name = current_topic_data["name"] if (current_topic_data and is_edit_mode) else ""
    init_source = current_topic_data.get("source", "직접 입력") if (current_topic_data and is_edit_mode) else "직접 입력"
    init_level = current_topic_data.get("level", "복습 중") if (current_topic_data and is_edit_mode) else "복습 중"
    init_content = current_topic_data.get("content", "") if (current_topic_data and is_edit_mode) else ""
    init_keywords = ", ".join(current_topic_data.get("user_keywords", [])) if (current_topic_data and is_edit_mode) else ""
    init_limit = current_topic_data.get("char_limit", 4000) if (current_topic_data and is_edit_mode) else 4000

    col_a, col_b = st.columns([2, 1])
    with col_a:
        topic_input = st.text_input("주제 이름 *", value=init_name, placeholder="예: 자동화와 경제성장")
    with col_b:
        source_input = st.text_input("출처", value=init_source, placeholder="예: W01A, 직접 입력")

    col_c, col_d = st.columns([1, 2])
    with col_c:
        level_input = st.selectbox(
            "학습 수준",
            ["처음 배우는 중", "복습 중", "시험 직전"],
            index=["처음 배우는 중", "복습 중", "시험 직전"].index(init_level) if init_level in ["처음 배우는 중", "복습 중", "시험 직전"] else 1,
        )
    with col_d:
        keywords_input = st.text_input(
            "핵심 용어 직접 추가 (쉼표 구분)",
            value=init_keywords,
            placeholder="예: 대체효과, 생산성효과",
        )

    content_input = st.text_area(
        "공부 자료 본문 *",
        value=init_content,
        height=260,
        placeholder="강의 슬라이드나 요약 텍스트를 입력하세요...",
    )

    current_len = len(content_input.strip())
    st.write(f"현재 글자 수: **{current_len:,}자**")

    char_limit = st.slider(
        "AI 전송 글자 수 제한",
        min_value=500,
        max_value=12000,
        value=init_limit,
        step=500,
        help="텍스트가 이 길이를 초과하면 지정된 길이까지만 전송됩니다.",
    )

    if 0 < current_len < 200:
        st.caption("참고: 자료가 200자 미만으로 짧습니다. 더 긴 내용을 입력하면 더 충실한 카드가 생성됩니다.")

    if st.button("자료 저장", type="primary"):
        if not topic_input.strip():
            st.error("주제 이름을 입력해 주세요.")
        elif not content_input.strip():
            st.error("공부 자료 본문이 비어 있습니다.")
        else:
            kw_list = [k.strip() for k in keywords_input.split(",") if k.strip()]
            new_data = {
                "name": topic_input.strip(),
                "source": source_input.strip() if source_input.strip() else "직접 입력",
                "level": level_input,
                "content": content_input.strip(),
                "char_count": current_len,
                "char_limit": char_limit,
                "user_keywords": kw_list,
            }
            success = storage.save_topic_data(new_data)
            if success:
                st.session_state.selected_topic = topic_input.strip()
                st.success(f"주제 '{topic_input.strip()}' 자료가 저장되었습니다.")
                st.rerun()
            else:
                st.error("데이터 저장 중 오류가 발생했습니다.")

    if current_topic_data:
        st.markdown("---")
        st.markdown("#### 선택된 주제 저장 정보")
        st.write(f"- 최초 저장: {current_topic_data.get('created_at', '-')}")
        st.write(f"- 마지막 수정: {current_topic_data.get('updated_at', '-')}")
        st.write(f"- 저장된 용어 카드: {len(current_topic_data.get('saved_terms', []))}개")


# ── [탭 2: 용어 카드 & 플래시카드] ──────────────────────────
with tab2:
    st.subheader("한국어 용어 카드 및 플래시카드 학습")

    if not current_topic_data:
        st.info("사이드바에서 학습할 주제를 먼저 선택하세요.")
    else:
        topic_name = current_topic_data["name"]
        content_text = current_topic_data.get("content", "")
        saved_terms = current_topic_data.get("saved_terms", [])

        st.markdown(
            f"**현재 주제:** `{topic_name}` | "
            f"**출처:** `{current_topic_data.get('source', '직접 입력')}` | "
            f"**수준:** `{current_topic_data.get('level', '-')}` | "
            f"**저장된 카드:** **{len(saved_terms)}개**"
        )

        # ── [플래시카드 암기 모드] ──
        if saved_terms:
            with st.container(border=True):
                st.markdown("#### 플래시카드 암기 학습")

                total_cards = len(saved_terms)
                idx = st.session_state.flashcard_index
                if idx >= total_cards:
                    idx = 0
                    st.session_state.flashcard_index = 0

                cur_card = saved_terms[idx]
                card_term = cur_card.get("term", "")

                st.caption(f"진행 상황: **{idx + 1} / {total_cards} 카드**")
                st.progress((idx + 1) / total_cards)

                with st.container(border=True):
                    st.markdown(f"### {card_term}")

                    if not st.session_state.flashcard_revealed:
                        if st.button("뜻 보기", key=f"reveal_{idx}"):
                            st.session_state.flashcard_revealed = True
                            st.rerun()
                    else:
                        st.markdown(f"**뜻:** {cur_card.get('meaning', '-')}")
                        st.markdown(f"**핵심 포인트:** {cur_card.get('key_point', '-')}")
                        if cur_card.get("example"):
                            st.markdown(f"**예시:** {cur_card.get('example')}")
                        st.caption(f"신뢰도: {cur_card.get('confidence', 'medium')} | 학습 횟수: {cur_card.get('study_count', 0)}회")

                c_prev, c_memo, c_again, c_next = st.columns(4)
                with c_prev:
                    if st.button("◀ 이전 카드", disabled=(idx == 0)):
                        st.session_state.flashcard_index = max(0, idx - 1)
                        st.session_state.flashcard_revealed = False
                        st.rerun()

                with c_memo:
                    if st.button("🟢 외웠어요", type="primary"):
                        storage.record_card_study(topic_name, card_term, memorized=True)
                        st.session_state.flashcard_index = (idx + 1) % total_cards
                        st.session_state.flashcard_revealed = False
                        st.success(f"'{card_term}' 학습 완료 반영!")
                        st.rerun()

                with c_again:
                    if st.button("🔴 다시 볼게요"):
                        storage.record_card_study(topic_name, card_term, memorized=False)
                        st.session_state.flashcard_index = (idx + 1) % total_cards
                        st.session_state.flashcard_revealed = False
                        st.warning(f"'{card_term}'이(가) 약한 개념에 등록되었습니다.")
                        st.rerun()

                with c_next:
                    if st.button("▶ 다음 카드", disabled=(idx == total_cards - 1)):
                        st.session_state.flashcard_index = min(total_cards - 1, idx + 1)
                        st.session_state.flashcard_revealed = False
                        st.rerun()

            st.markdown("---")

        # ── [AI로 새 카드 만들기] ──
        st.markdown("#### AI 용어 카드 생성")
        st.caption(f"선택된 AI 제공자: **{provider_name}** (`{current_model}`) | 전송 글자 수: 최대 {min(len(content_text), current_topic_data.get('char_limit', 4000)):,}자")

        can_generate = bool(content_text.strip()) and bool(current_provider.get("api_key"))

        if not content_text.strip():
            st.warning("공부 자료가 비어 있습니다. '자료 입력' 탭에서 본문을 먼저 저장하세요.")
        if not current_provider.get("api_key"):
            st.warning(f"{provider_name} API 키가 설정되어 있지 않습니다.")

        col_g1, col_g2 = st.columns([1, 3])
        with col_g1:
            if st.button("AI로 카드 만들기", type="primary", disabled=not can_generate):
                with st.spinner(f"{provider_name}({current_model})에서 용어 카드를 추출하고 있습니다..."):
                    result = generate_terms_ai(
                        topic_name=topic_name,
                        source=current_topic_data.get("source", "직접 입력"),
                        level=current_topic_data.get("level", "복습 중"),
                        content=content_text,
                        char_limit=current_topic_data.get("char_limit", 4000),
                        user_keywords=current_topic_data.get("user_keywords", []),
                        provider_name=provider_name,
                        model_name=current_model,
                    )
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.session_state.staging_terms = result.get("terms", [])
                    st.success(f"카드 {len(st.session_state.staging_terms)}개가 생성되었습니다. 아래에서 확인 후 저장하세요.")
                    st.rerun()

        # ── [사용자 검증 및 수정 단계] ──
        if st.session_state.staging_terms:
            st.markdown("---")
            with st.container(border=True):
                st.markdown("#### AI 제안 카드 검토 및 수정")
                st.caption("카드의 내용을 직접 수정하거나 저장할 카드를 선택하세요. (confidence가 low인 카드는 경고 표시 및 기본 체크 해제됨)")

                cards_to_save = []
                for idx, term_card in enumerate(st.session_state.staging_terms):
                    c_term = term_card.get("term", "")
                    c_conf = term_card.get("confidence", "medium")
                    is_low = (c_conf.lower() == "low")

                    with st.container(border=True):
                        col_check, col_meta = st.columns([3, 1])
                        with col_check:
                            is_checked = st.checkbox(
                                f"**용어:** {c_term}",
                                value=(not is_low),
                                key=f"stage_check_{idx}",
                            )
                        with col_meta:
                            if is_low:
                                st.warning("주의: 신뢰도 낮음 (low)")
                            else:
                                st.caption(f"신뢰도: {c_conf}")

                        col_m1, col_m2 = st.columns(2)
                        with col_m1:
                            mod_meaning = st.text_input(
                                "뜻",
                                value=term_card.get("meaning", ""),
                                key=f"stage_mean_{idx}",
                            )
                            mod_keypoint = st.text_input(
                                "핵심 문장",
                                value=term_card.get("key_point", ""),
                                key=f"stage_kp_{idx}",
                            )
                        with col_m2:
                            mod_example = st.text_input(
                                "예시",
                                value=term_card.get("example", ""),
                                key=f"stage_ex_{idx}",
                            )

                        if is_checked:
                            cards_to_save.append({
                                "term": c_term,
                                "meaning": mod_meaning.strip(),
                                "key_point": mod_keypoint.strip(),
                                "example": mod_example.strip(),
                                "confidence": c_conf,
                            })

                col_s1, col_s2 = st.columns([1, 3])
                with col_s1:
                    if st.button("선택한 카드 저장", type="primary"):
                        if not cards_to_save:
                            st.warning("저장할 카드를 최소 1개 이상 선택해 주세요.")
                        else:
                            saved_cnt, skip_cnt = storage.save_terms_to_topic(topic_name, cards_to_save)
                            msg = f"카드 {saved_cnt}개를 저장했습니다."
                            if skip_cnt > 0:
                                msg += f" (이미 존재하는 용어 {skip_cnt}개는 중복 제외됨)"
                            st.success(msg)
                            st.session_state.staging_terms = None
                            st.rerun()
                with col_s2:
                    if st.button("생성 결과 취소"):
                        st.session_state.staging_terms = None
                        st.rerun()

        # ── [저장된 카드 목록 및 설명] ──
        if saved_terms:
            st.markdown("---")
            st.markdown(f"#### 저장된 용어 카드 목록 ({len(saved_terms)}개)")
            for idx, term_item in enumerate(saved_terms, 1):
                t_term = term_item.get("term", "")
                with st.expander(f"{idx}. {t_term}", expanded=False):
                    col_t1, col_t2 = st.columns([4, 1])
                    with col_t1:
                        st.write(f"**뜻:** {term_item.get('meaning')}")
                        st.write(f"**핵심 포인트:** {term_item.get('key_point')}")
                        if term_item.get("example"):
                            st.write(f"**예시:** {term_item.get('example')}")
                        st.caption(f"신뢰도: {term_item.get('confidence', 'medium')} | 학습 횟수: {term_item.get('study_count', 0)}회 | 저장: {term_item.get('saved_at', '-')}")
                    with col_t2:
                        if st.button("설명", key=f"term_card_explain_{idx}"):
                            st.session_state[f"run_explain_card_{idx}"] = True

                    if st.session_state.get(f"run_explain_card_{idx}"):
                        with st.spinner(f"{provider_name}({current_model}) 설명 생성 중..."):
                            exp = explain_term_ai(t_term, topic_name, content_text, provider_name, current_model)
                        if "error" in exp:
                            st.error(exp["error"])
                        else:
                            render_term_comparison(t_term, topic_name)


# ── [탭 3: 객관식 연습 문제] ─────────────────────────────────
with tab3:
    st.subheader("객관식 연습 문제 및 실전 풀이")

    if not current_topic_data:
        st.info("사이드바에서 먼저 학습할 주제를 선택하세요.")
    else:
        topic_name = current_topic_data["name"]
        content_text = current_topic_data.get("content", "")
        saved_terms = current_topic_data.get("saved_terms", [])

        # ── [상태 A: 퀴즈 미진행 상태 (출제 옵션 설정)] ──
        if st.session_state.active_quiz is None:
            st.markdown(f"**현재 주제:** `{topic_name}` | 저장된 카드: **{len(saved_terms)}개**")

            with st.container(border=True):
                st.markdown("#### 퀴즈 출제 옵션 설정")

                col_opt1, col_opt2 = st.columns(2)
                with col_opt1:
                    q_num = st.radio("문제 수", [5, 10], format_func=lambda x: f"{x}문제", horizontal=True)
                    q_diff = st.radio("난이도", ["쉬움", "보통", "어려움", "섞어서"], horizontal=True, index=1)
                with col_opt2:
                    q_time = st.radio("제한 시간", ["없음", "5분", "10분"], horizontal=True, index=0)
                    q_scope = st.radio("출제 범위", ["전체 자료에서 출제", "저장된 용어 카드에서 출제", "약한 개념 위주로 출제"])

                st.caption(f"적용 AI 제공자: **{provider_name}** (`{current_model}`)")

                can_make_quiz = bool(content_text.strip()) and bool(current_provider.get("api_key"))

                if not content_text.strip():
                    st.warning("공부 자료 본문이 비어 있어 문제를 출제할 수 없습니다.")
                if not current_provider.get("api_key"):
                    st.warning(f"{provider_name} API 키가 설정되어 있지 않습니다.")

                if st.button("AI로 문제 만들기", type="primary", disabled=not can_make_quiz):
                    limit_seconds = 0
                    if q_time == "5분":
                        limit_seconds = 300
                    elif q_time == "10분":
                        limit_seconds = 600

                    with st.spinner(f"{provider_name}({current_model})에서 객관식 {q_num}문항을 출제하고 있습니다..."):
                        quiz_res = generate_quiz_ai(
                            topic_name=topic_name,
                            content=content_text,
                            saved_terms=saved_terms,
                            weak_terms_list=weak_terms,
                            num_questions=q_num,
                            difficulty=q_diff,
                            scope=q_scope,
                            provider_name=provider_name,
                            model_name=current_model,
                        )

                    if "error" in quiz_res:
                        st.error(quiz_res["error"])
                    else:
                        st.session_state.active_quiz = {
                            "topic": topic_name,
                            "questions": quiz_res.get("questions", []),
                            "current_index": 0,
                            "user_answers": {},  # {q_idx: choice_idx}
                            "submitted": {},     # {q_idx: bool}
                            "start_time": time.time(),
                            "time_limit_sec": limit_seconds,
                            "is_finished": False,
                            "wrong_questions": [],
                            "saved_record": None,
                        }
                        st.success("문제가 성공적으로 출제되었습니다! 아래에서 문제를 풀어보세요.")
                        st.rerun()

        # ── [상태 B: 퀴즈 진행 중 상태 (한 문제씩 실전 풀이)] ──
        elif not st.session_state.active_quiz.get("is_finished", False):
            quiz = st.session_state.active_quiz
            q_list = quiz["questions"]
            total_q = len(q_list)
            cur_idx = quiz["current_index"]
            q_item = q_list[cur_idx]

            # 타이머 계산 (선택된 경우)
            limit_sec = quiz.get("time_limit_sec", 0)
            time_is_up = False
            timer_text = ""
            if limit_sec > 0:
                elapsed = time.time() - quiz["start_time"]
                remaining = max(0, int(limit_sec - elapsed))
                rem_m = remaining // 60
                rem_s = remaining % 60
                timer_text = f"⏱️ 남은 시간: **{rem_m:02d}:{rem_s:02d}**"
                if remaining <= 0:
                    time_is_up = True

            col_h1, col_h2 = st.columns([3, 1])
            with col_h1:
                st.caption(f"진행 상황: **{cur_idx + 1} / {total_q} 문제** | 주제: `{quiz['topic']}`")
                st.progress((cur_idx + 1) / total_q)
            with col_h2:
                if timer_text:
                    st.markdown(timer_text)

            if time_is_up:
                st.warning("⚠️ 제한 시간이 종료되었습니다! 현재까지 푼 문제로 자동 채점합니다.")
                # 자동 완료 처리
                correct_cnt = 0
                wrong_terms_list = []
                wrong_qs = []
                for idx_q, q_obj in enumerate(q_list):
                    user_ans = quiz["user_answers"].get(idx_q)
                    if user_ans == q_obj["answer_index"]:
                        correct_cnt += 1
                    else:
                        rel = q_obj.get("related_term", "").strip()
                        if rel:
                            wrong_terms_list.append(rel)
                            storage.add_or_update_weak_term(rel, quiz["topic"])
                        wrong_qs.append(q_obj)

                record = storage.save_quiz_result(
                    quiz["topic"], total_q, correct_cnt, wrong_terms_list, questions=quiz.get("questions")
                )
                quiz["is_finished"] = True
                quiz["wrong_questions"] = wrong_qs
                quiz["saved_record"] = record
                st.rerun()

            # 현재 문제 카드 (네이티브 컨테이너)
            with st.container(border=True):
                st.markdown(f"#### Q{cur_idx + 1}. {q_item['question']}")

                choices = q_item["choices"]
                prev_choice = quiz["user_answers"].get(cur_idx, None)

                # 라디오 버튼 답안 선택
                selected_choice = st.radio(
                    "보기를 선택하세요",
                    choices,
                    index=prev_choice if prev_choice is not None else None,
                    key=f"quiz_radio_{cur_idx}",
                )

                if selected_choice is not None:
                    quiz["user_answers"][cur_idx] = choices.index(selected_choice)

                # 정답 확인 버튼
                has_submitted = quiz["submitted"].get(cur_idx, False)

                col_sub, col_next = st.columns([1, 4])
                with col_sub:
                    if st.button("정답 확인", key=f"btn_sub_{cur_idx}", disabled=has_submitted):
                        if quiz["user_answers"].get(cur_idx) is None:
                            st.warning("먼저 보기를 선택해 주세요.")
                        else:
                            quiz["submitted"][cur_idx] = True
                            user_ans_idx = quiz["user_answers"][cur_idx]
                            corr_idx = q_item["answer_index"]
                            if user_ans_idx != corr_idx:
                                rel_term = q_item.get("related_term", "").strip()
                                if rel_term:
                                    storage.add_or_update_weak_term(rel_term, quiz["topic"])
                            st.rerun()

                # 해설 표시 영역 (제출된 경우)
                if has_submitted:
                    user_ans_idx = quiz["user_answers"][cur_idx]
                    corr_idx = q_item["answer_index"]
                    if user_ans_idx == corr_idx:
                        st.success(f"정답입니다! (정답: {corr_idx + 1}번)")
                    else:
                        st.error(f"틀렸습니다. 정답은 **{corr_idx + 1}번** ({choices[corr_idx]}) 입니다.")

                    st.markdown(f"**해설:** {q_item.get('explanation', '-')}")
                    if q_item.get("related_term"):
                        st.caption(f"관련 핵심 개념: **{q_item.get('related_term')}** (오답 시 약한 개념에 자동 저장됨)")

                # 다음 문제 버튼
                with col_next:
                    is_last = (cur_idx == total_q - 1)
                    btn_label = "결과 확인" if is_last else "다음 문제 ▶"

                    if st.button(btn_label, key=f"btn_next_{cur_idx}", type="primary"):
                        if is_last:
                            # 퀴즈 최종 채점 및 저장
                            correct_cnt = 0
                            wrong_terms_list = []
                            wrong_qs = []
                            for idx_q, q_obj in enumerate(q_list):
                                u_ans = quiz["user_answers"].get(idx_q)
                                if u_ans == q_obj["answer_index"]:
                                    correct_cnt += 1
                                else:
                                    rel = q_obj.get("related_term", "").strip()
                                    if rel:
                                        wrong_terms_list.append(rel)
                                        storage.add_or_update_weak_term(rel, quiz["topic"])
                                    wrong_qs.append(q_obj)

                            record = storage.save_quiz_result(
                    quiz["topic"], total_q, correct_cnt, wrong_terms_list, questions=quiz.get("questions")
                )
                            quiz["is_finished"] = True
                            quiz["wrong_questions"] = wrong_qs
                            quiz["saved_record"] = record
                            st.rerun()
                        else:
                            quiz["current_index"] += 1
                            st.rerun()

        # ── [상태 C: 퀴즈 종료 및 결과 화면 (오답 재풀이)] ──
        else:
            quiz = st.session_state.active_quiz
            rec = quiz.get("saved_record", {})
            total_q = quiz.get("saved_record", {}).get("total_questions", len(quiz["questions"]))
            correct_cnt = rec.get("correct_count", 0)
            accuracy = rec.get("accuracy", 0.0)
            wrong_qs = quiz.get("wrong_questions", [])

            st.markdown("#### 퀴즈 결과")

            col_res1, col_res2, col_res3 = st.columns(3)
            with col_res1:
                st.metric("총 문제 수", f"{total_q}문제")
            with col_res2:
                st.metric("정답 수", f"{correct_cnt}문제", delta=f"{accuracy:.1f}%")
            with col_res3:
                st.metric("틀린 문제 수", f"{len(wrong_qs)}문제")

            if accuracy == 100.0:
                st.success("축하합니다! 모든 문제를 맞히셨습니다!")
            elif accuracy >= 70.0:
                st.info("우수한 성적입니다. 틀린 문제를 복습하여 완성도를 높여보세요.")
            else:
                st.warning("약한 개념이 다수 발견되었습니다. 오답 해설을 확인하고 다시 풀어보세요.")

            # 오답 해설 복습 목록
            if wrong_qs:
                st.markdown("---")
                st.markdown(f"#### 틀린 문제 및 해설 복습 ({len(wrong_qs)}문제)")
                for w_idx, w_obj in enumerate(wrong_qs, 1):
                    with st.expander(f"오답 Q{w_idx}. {w_obj['question']}", expanded=True):
                        st.markdown(f"**정답:** {w_obj['answer_index'] + 1}번 ({w_obj['choices'][w_obj['answer_index']]})")
                        st.markdown(f"**해설:** {w_obj.get('explanation', '-')}")
                        if w_obj.get("related_term"):
                            st.caption(f"약한 개념 등록 완료: **{w_obj.get('related_term')}**")

            st.markdown("---")
            c_retry, c_new = st.columns([2, 1])

            # 틀린 문제만 다시 풀기 (API 호출 0회!)
            with c_retry:
                if wrong_qs:
                    if st.button("🔄 틀린 문제만 다시 풀기", type="primary"):
                        st.session_state.active_quiz = {
                            "topic": quiz["topic"],
                            "questions": wrong_qs,
                            "current_index": 0,
                            "user_answers": {},
                            "submitted": {},
                            "start_time": time.time(),
                            "time_limit_sec": 0,
                            "is_finished": False,
                            "wrong_questions": [],
                            "saved_record": None,
                        }
                        st.success("오답 재도전 세션이 시작되었습니다! (API 호출 없음)")
                        st.rerun()

            with c_new:
                if st.button("새 퀴즈 만들기"):
                    st.session_state.active_quiz = None
                    st.rerun()


# ── [탭 4: 복습 현황] ───────────────────────────────────────
with tab4:
    st.subheader("복습 현황 및 학습 노트 내보내기")
    st.caption("로컬 데이터 기반 분석 및 Markdown 문서 다운로드 (API 호출 없음)")

    # Markdown 내보내기 다운로드 영역 (API 호출 0회)
    if current_topic_data:
        t_name = current_topic_data["name"]
        md_text = storage.export_topic_to_markdown(t_name)
        safe_filename = f"복습_{t_name.replace(' ', '_')}.md"

        with st.container(border=True):
            col_exp1, col_exp2 = st.columns([3, 1])
            with col_exp1:
                st.markdown(f"**현재 선택 주제:** `{t_name}`")
                st.caption("주제 원문, 용어 카드, 약한 개념, 퀴즈 이력을 통합 정리한 Markdown 파일로 다운로드합니다.")
            with col_exp2:
                st.download_button(
                    label="📥 Markdown 내보내기",
                    data=md_text,
                    file_name=safe_filename,
                    mime="text/markdown",
                    type="primary",
                )

    subtab1, subtab2, subtab3 = st.tabs(["추천 복습 순서", "약한 개념 목록", "퀴즈 기록"])

    with subtab1:
        st.markdown("#### 우선 복습 개념")
        if weak_terms:
            sorted_weak = sorted(weak_terms, key=lambda x: (x.get("wrong_count", 0), x.get("last_wrong_at", "")), reverse=True)
            for i, w in enumerate(sorted_weak[:5], 1):
                col_sw1, col_sw2 = st.columns([4, 1])
                with col_sw1:
                    st.markdown(f"**{i}. {w.get('term')}** ({w.get('wrong_count')}회 오답)")
                    st.caption(f"사유: 누적 오답 빈도 최다 | 관련 주제: {w.get('topic')}")
                with col_sw2:
                    if st.button("설명", key=f"rev_explain_{i}"):
                        st.session_state[f"run_rev_explain_{i}"] = True

                if st.session_state.get(f"run_rev_explain_{i}"):
                    ctx_text = ""
                    for t in topics:
                        if t.get("name") == w.get("topic"):
                            ctx_text = t.get("content", "")
                            break
                    with st.spinner(f"{provider_name}({current_model}) 설명 요청 중..."):
                        exp = explain_term_ai(w.get("term"), w.get("topic"), ctx_text, provider_name, current_model)
                    if "error" in exp:
                        st.error(exp["error"])
                    else:
                        render_term_comparison(w.get("term"), w.get("topic"))
        else:
            st.info("누적된 약한 개념이 없습니다.")

    with subtab2:
        st.markdown("#### 약한 개념 상세 목록")
        if weak_terms:
            for item in weak_terms:
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.write(f"**{item.get('term')}** ({item.get('topic')})")
                with c2:
                    st.write(f"오답: {item.get('wrong_count')}회")
                with c3:
                    st.caption(f"최근: {item.get('last_wrong_at', '-')}")
        else:
            st.info("오답 개념이 없습니다.")

    with subtab3:
        st.markdown("#### 최근 퀴즈 기록")
        if history:
            for h in reversed(history[-10:]):
                acc = h.get("accuracy", 0.0)
                st.markdown(
                    f"- {h.get('date')} | **{h.get('topic')}** — "
                    f"정답: {h.get('correct_count')}/{h.get('total_questions')} ({acc:.1f}%) "
                    f"| 오답 용어: {', '.join(h.get('wrong_terms', [])) or '없음'}"
                )
        else:
            st.info("퀴즈 기록이 없습니다.")


# ── [탭 5: 도움말] ─────────────────────────────────────────
with tab5:
    st.subheader("이용 가이드")
    st.markdown(
        r"""
### 1. 학습 사이클
1. **[홈 대시보드]**에서 오늘의 학습 현황(오답 개념 수, 정답률, 추천 주제)을 확인합니다.
2. **[자료 입력]** 탭에서 시험 범위의 수업 슬라이드 내용이나 요약 노트를 입력하고 **자료 저장**을 누릅니다.
3. **[용어 카드]** 탭에서 **AI로 카드 만들기**로 5~8개의 핵심 용어를 추출하여 검토 및 수정한 뒤 저장하고, **플래시카드 암기 모드**로 외웁니다.
4. **[연습 문제]** 탭에서 4지선다 객관식 문제를 풀며 취약점을 진단합니다. (틀린 문제만 다시 풀기 지원)
5. **[복습 현황]** 탭에서 오답 통계를 점검합니다.

---

### 2. 객관식 연습 문제 이용 원칙
- **문제 출제**: 문제 수(5/10), 난이도(쉬움/보통/어려움/섞어서), 제한 시간(없음/5분/10분), 출제 범위를 선택하고 **AI로 문제 만들기**를 누릅니다.
- **실전 풀이**: 1문제씩 풀며 답을 선택하고 **「정답 확인」**을 누르면 정답 여부와 해설이 즉시 표시됩니다.
- **오답 자동 누적**: 틀린 문제의 핵심 용어는 자동으로 약한 개념에 저장되어 취약점으로 관리됩니다.
- **틀린 문제만 다시 풀기**: 퀴즈 종료 후 **「🔄 틀린 문제만 다시 풀기」**를 누르면 API 호출 없이 오답들만 즉시 다시 풀 수 있습니다.

---

### 3. API 호출 원칙 (절대 규칙 준수)
- **API가 호출되는 경우 (오직 버튼 클릭 시):**
  - `[용어 카드]` 탭의 **「AI로 카드 만들기」** 버튼 클릭 시
  - 용어 옆의 **「설명」** 버튼 클릭 시
  - `[연습 문제]` 탭의 **「AI로 문제 만들기」** 버튼 클릭 시
- **API가 호출되지 않는 경우:**
  - 문제 답 선택, 정답 확인, 다음 문제 이동, 틀린 문제 다시 풀기, 타이머 동작, 플래시카드 넘기기, 테마 전환, 슬라이더 변경, 자료 저장, 복습 현황 조회
"""
    )
