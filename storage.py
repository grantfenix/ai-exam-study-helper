# storage.py — 데이터 관리 및 입출력 모듈
# - 원자적(Atomic) 파일 저장, Windows 파일 락 방지, 용어 카드/퀴즈 이력 관리, Markdown 내보내기

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TOPICS_FILE = DATA_DIR / "topics.json"
WEAK_TERMS_FILE = DATA_DIR / "weak_terms.json"
QUIZ_HISTORY_FILE = DATA_DIR / "quiz_history.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json_safe(file_path: Path, default_data: dict) -> dict:
    """JSON 파일을 안전하게 읽고, 파일 부재 또는 손상 시 자동 복구합니다."""
    if not file_path.exists():
        save_json_safe(file_path, default_data)
        return default_data

    try:
        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            return default_data
        return json.loads(content)
    except json.JSONDecodeError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupted_path = file_path.parent / f"{file_path.stem}_corrupted_{timestamp}.json"
        try:
            shutil.copy2(file_path, corrupted_path)
        except Exception:
            pass
        save_json_safe(file_path, default_data)
        return default_data
    except Exception:
        return default_data


def save_json_safe(file_path: Path, data: dict) -> bool:
    """Windows 파일 락을 방지하며 원자적(Atomic)으로 파일을 안전하게 저장합니다."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        temp_dir = file_path.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            temp_path = Path(tf.name)

        if file_path.exists():
            os.replace(temp_path, file_path)
        else:
            shutil.move(temp_path, file_path)
        return True
    except Exception:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False


def load_all_app_data() -> dict:
    """한 번의 호출로 topics, weak_terms, quiz_history를 모두 읽어 디스크 I/O를 최소화합니다."""
    topics_data = load_json_safe(TOPICS_FILE, {"topics": []})
    weak_data = load_json_safe(WEAK_TERMS_FILE, {"weak_terms": []})
    quiz_data = load_json_safe(QUIZ_HISTORY_FILE, {"history": []})

    topics = topics_data.get("topics", [])
    weak_terms = weak_data.get("weak_terms", [])
    history = quiz_data.get("history", [])

    total_topics = len(topics)
    total_saved_terms = sum(len(t.get("saved_terms", [])) for t in topics)
    total_weak_terms = len(weak_terms)
    total_questions = sum(h.get("total_questions", 0) for h in history)
    total_correct = sum(h.get("correct_count", 0) for h in history)
    overall_accuracy = (total_correct / total_questions * 100.0) if total_questions > 0 else 0.0

    sorted_weak = sorted(weak_terms, key=lambda x: x.get("wrong_count", 0), reverse=True)
    top3_weak = sorted_weak[:3]

    recommended_topic = None
    if topics:
        topic_wrong_counts = {}
        for w in weak_terms:
            t_name = w.get("topic")
            topic_wrong_counts[t_name] = topic_wrong_counts.get(t_name, 0) + w.get("wrong_count", 1)

        def topic_sort_key(t):
            w_count = topic_wrong_counts.get(t.get("name"), 0)
            updated = t.get("updated_at", "")
            return (-w_count, updated)

        sorted_topics = sorted(topics, key=topic_sort_key)
        recommended_topic = sorted_topics[0]

    return {
        "topics": topics,
        "weak_terms": weak_terms,
        "history": history,
        "stats": {
            "total_topics": total_topics,
            "total_saved_terms": total_saved_terms,
            "total_weak_terms": total_weak_terms,
            "total_questions": total_questions,
            "overall_accuracy": overall_accuracy,
            "top3_weak": top3_weak,
            "recommended_topic": recommended_topic,
            "recent_history": history[-5:] if history else [],
        },
    }


def get_all_topics() -> list:
    return load_all_app_data().get("topics", [])


def get_weak_terms() -> list:
    return load_all_app_data().get("weak_terms", [])


def get_quiz_history() -> list:
    return load_all_app_data().get("history", [])


def save_topic_data(topic_dict: dict) -> bool:
    """학습 주제 및 본문을 topics.json에 저장합니다."""
    data = load_json_safe(TOPICS_FILE, {"topics": []})
    topics = data.get("topics", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing_index = None
    for idx, t in enumerate(topics):
        if t.get("name") == topic_dict["name"]:
            existing_index = idx
            break

    if existing_index is not None:
        topic_dict["created_at"] = topics[existing_index].get("created_at", now_str)
        topic_dict["updated_at"] = now_str
        if "saved_terms" not in topic_dict:
            topic_dict["saved_terms"] = topics[existing_index].get("saved_terms", [])
        topics[existing_index] = topic_dict
    else:
        topic_dict["created_at"] = now_str
        topic_dict["updated_at"] = now_str
        if "saved_terms" not in topic_dict:
            topic_dict["saved_terms"] = []
        topics.append(topic_dict)

    data["topics"] = topics
    return save_json_safe(TOPICS_FILE, data)


def save_terms_to_topic(topic_name: str, new_terms: list) -> tuple[int, int]:
    """선택한 용어 카드를 주제에 저장하며, 같은 용어가 이미 있으면 중복 건너뜁니다."""
    data = load_json_safe(TOPICS_FILE, {"topics": []})
    topics = data.get("topics", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    target_topic = None
    for t in topics:
        if t.get("name") == topic_name:
            target_topic = t
            break

    if not target_topic:
        return (0, 0)

    existing_terms = target_topic.get("saved_terms", [])
    existing_term_names = {t.get("term", "").strip().lower() for t in existing_terms}

    saved_count = 0
    skipped_count = 0

    for item in new_terms:
        term_clean = item.get("term", "").strip()
        if not term_clean:
            continue
        if term_clean.lower() in existing_term_names:
            skipped_count += 1
            continue

        item["saved_at"] = now_str
        item["study_count"] = 0
        existing_terms.append(item)
        existing_term_names.add(term_clean.lower())
        saved_count += 1

    target_topic["saved_terms"] = existing_terms
    target_topic["updated_at"] = now_str
    save_json_safe(TOPICS_FILE, data)
    return (saved_count, skipped_count)


def record_card_study(topic_name: str, term_name: str, memorized: bool):
    """플래시카드 학습 시 '외웠어요' 또는 '다시 볼게요' 결과를 기록합니다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = load_json_safe(TOPICS_FILE, {"topics": []})
    topics = data.get("topics", [])

    for t in topics:
        if t.get("name") == topic_name:
            for c in t.get("saved_terms", []):
                if c.get("term") == term_name:
                    c["last_studied_at"] = now_str
                    if memorized:
                        c["study_count"] = c.get("study_count", 0) + 1
                    break
            break
    save_json_safe(TOPICS_FILE, data)

    if not memorized:
        add_or_update_weak_term(term_name, topic_name, increment=1)


def save_quiz_result(topic_name: str, total_q: int, correct_cnt: int, wrong_terms: list, questions: list = None) -> dict:
    """퀴즈 결과를 quiz_history.json에 기록하고 저장된 레코드를 반환합니다."""
    data = load_json_safe(QUIZ_HISTORY_FILE, {"history": []})
    history = data.get("history", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accuracy = (correct_cnt / total_q * 100.0) if total_q > 0 else 0.0

    record = {
        "date": now_str,
        "topic": topic_name,
        "total_questions": total_q,
        "correct_count": correct_cnt,
        "accuracy": round(accuracy, 1),
        "wrong_terms": list(set(wrong_terms)),
    }
    if questions:
        record["questions"] = questions

    history.append(record)
    data["history"] = history
    save_json_safe(QUIZ_HISTORY_FILE, data)
    return record


def add_or_update_weak_term(term: str, topic: str, increment: int = 1) -> bool:
    """약한 개념을 누적 저장합니다."""
    data = load_json_safe(WEAK_TERMS_FILE, {"weak_terms": []})
    weak_terms = data.get("weak_terms", [])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    found = False
    for item in weak_terms:
        if item.get("term") == term and item.get("topic") == topic:
            item["wrong_count"] = item.get("wrong_count", 0) + increment
            item["last_wrong_at"] = now_str
            found = True
            break

    if not found:
        weak_terms.append({
            "term": term,
            "topic": topic,
            "wrong_count": increment,
            "last_wrong_at": now_str,
        })

    data["weak_terms"] = weak_terms
    return save_json_safe(WEAK_TERMS_FILE, data)


def export_topic_to_markdown(topic_name: str) -> str:
    """선택한 주제의 모든 학습 자료, 카드, 약한 개념, 퀴즈 이력을 Markdown 문서로 조립합니다 (API 호출 0회)."""
    app_data = load_all_app_data()
    topics = app_data["topics"]
    weak_terms = app_data["weak_terms"]
    history = app_data["history"]

    target_topic = None
    for t in topics:
        if t.get("name") == topic_name:
            target_topic = t
            break

    if not target_topic:
        return f"# 복습 노트: {topic_name}\n\n저장된 주제 정보를 찾을 수 없습니다."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source = target_topic.get("source", "직접 입력")
    level = target_topic.get("level", "복습 중")
    created_at = target_topic.get("created_at", "-")
    updated_at = target_topic.get("updated_at", "-")
    content = target_topic.get("content", "").strip()
    saved_terms = target_topic.get("saved_terms", [])

    # 해당 주제와 관련된 약한 개념
    related_weak = [w for w in weak_terms if w.get("topic") == topic_name]
    # 해당 주제와 관련된 퀴즈 이력
    related_history = [h for h in history if h.get("topic") == topic_name]

    lines = [
        f"# 복습 노트: {topic_name}",
        f"> **경영과인공지능 시험 대비 AI 도우미 내보내기 문서**  ",
        f"> 생성 일시: `{now_str}`",
        "",
        "---",
        "",
        "## 1. 주제 기본 정보",
        f"- **주제명**: {topic_name}",
        f"- **출처/주차**: `{source}`",
        f"- **학습 수준**: {level}",
        f"- **최초 등록**: {created_at}",
        f"- **최종 수정**: {updated_at}",
        f"- **저장된 본문 글자 수**: {len(content):,}자",
        "",
        "---",
        "",
        "## 2. 공부 자료 원문",
        "```text",
        content or "저장된 본문 내용이 없습니다.",
        "```",
        "",
        "---",
        "",
        f"## 3. 핵심 용어 카드 ({len(saved_terms)}개)",
    ]

    if saved_terms:
        for idx, c in enumerate(saved_terms, 1):
            lines.extend([
                f"### {idx}. {c.get('term')}",
                f"- **뜻**: {c.get('meaning', '-')}",
                f"- **핵심 포인트**: {c.get('key_point', '-')}",
                f"- **예시**: {c.get('example', '-')}",
                f"- **신뢰도**: `{c.get('confidence', 'medium')}` | **학습 횟수**: {c.get('study_count', 0)}회 | **등록일시**: {c.get('saved_at', '-')}",
                "",
            ])
    else:
        lines.append("저장된 용어 카드가 없습니다.\n")

    lines.extend([
        "---",
        "",
        f"## 4. 약한 개념 (오답 및 집중 복습 대상 - {len(related_weak)}개)",
    ])

    if related_weak:
        for idx, w in enumerate(related_weak, 1):
            lines.append(f"- **{idx}. {w.get('term')}**: 총 `{w.get('wrong_count')}회` 오답 (최근 오답: `{w.get('last_wrong_at', '-')}`)")
    else:
        lines.append("- 현재 누적된 약한 개념이 없습니다. (완전 학습 상태)")

    lines.extend([
        "",
        "---",
        "",
        f"## 5. 퀴즈 풀이 이력 ({len(related_history)}회)",
    ])

    if related_history:
        for idx, h in enumerate(reversed(related_history), 1):
            wrong_str = ", ".join(h.get("wrong_terms", [])) or "없음"
            lines.append(
                f"- **{h.get('date')}**: 정답 `{h.get('correct_count')}/{h.get('total_questions')}` "
                f"({h.get('accuracy', 0.0):.1f}%) | 오답 용어: `{wrong_str}`"
            )
    else:
        lines.append("- 퀴즈 풀이 기록이 없습니다.")

    # 6. 최근 저장된 문제와 정답, 해설 (최근 퀴즈에서 문항 데이터가 있는 경우)
    recent_quiz_questions = None
    for h in reversed(related_history):
        if h.get("questions"):
            recent_quiz_questions = h.get("questions")
            break

    lines.extend([
        "",
        "---",
        "",
        "## 6. 저장된 문제와 정답·해설 (최근 퀴즈)",
    ])

    if recent_quiz_questions:
        for q_idx, q in enumerate(recent_quiz_questions, 1):
            lines.extend([
                f"### [문항 {q_idx}] {q.get('question')}",
                f"- **난이도**: `{q.get('difficulty', '보통')}` | **관련 개념**: `{q.get('related_term', '-')}`",
                "",
                "**[보기]**",
            ])
            for opt_idx, opt in enumerate(q.get("options", []), 1):
                marker = " ★ (정답)" if opt_idx == q.get("answer") else ""
                lines.append(f"{opt_idx}. {opt}{marker}")
            lines.extend([
                "",
                f"> **정답**: `{q.get('answer')}번`  ",
                f"> **해설**: {q.get('explanation', '-')}",
                "",
            ])
    else:
        lines.append("- 저장된 최근 퀴즈 문항이 없습니다. (퀴즈 탭에서 문제를 풀면 자동으로 기록됩니다.)")

    lines.extend([
        "",
        "---",
        "*본 문서는 「오늘 뭐 복습하지?」 앱에서 사용자가 직접 입력한 자료와 학습 데이터를 바탕으로 로컬에서 생성되었습니다.*",
    ])

    return "\n".join(lines)


def backup_and_reset_all_data() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = DATA_DIR / f"backup_{timestamp}"
    backup_folder.mkdir(parents=True, exist_ok=True)

    for f in [TOPICS_FILE, WEAK_TERMS_FILE, QUIZ_HISTORY_FILE]:
        if f.exists():
            shutil.copy2(f, backup_folder / f.name)

    save_json_safe(TOPICS_FILE, {"topics": []})
    save_json_safe(WEAK_TERMS_FILE, {"weak_terms": []})
    save_json_safe(QUIZ_HISTORY_FILE, {"history": []})
    return str(backup_folder.name)
