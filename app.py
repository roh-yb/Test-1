"""쇼핑 리스트 Streamlit 앱 (PRD.md 참고)."""

import html
import re
import time
import uuid

import streamlit as st

import storage
from storage import MEMO_MAX, NAME_MAX, QTY_MAX, QTY_MIN, StorageError

# 아이템 데이터 모델 (PRD 5장)
# {
#     "id": str,          # uuid4 문자열
#     "name": str,        # 1~50자, 필수
#     "quantity": int,    # 1~999, 기본 1
#     "memo": str,        # 최대 100자
#     "checked": bool,    # 구매 완료 여부
#     "created_at": str,  # ISO 8601 (UTC)
# }

FILTERS = ["전체", "미완료", "완료"]
QTY_WARNING = f"수량은 {QTY_MIN}~{QTY_MAX} 사이로 입력해 주세요."
DOUBLE_SUBMIT_SEC = 1.0  # 추가 직후 이 시간 안의 빈 제출은 연타로 보고 무시

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")


class Abort(Exception):
    """transaction 안에서 저장 없이 중단할 때 사용."""


# ---------------------------------------------------------------------------
# 상태 / 유틸
# ---------------------------------------------------------------------------
def init_state() -> None:
    """세션 상태 초기화.

    리스트는 세션에 캐시하지 않고 매 실행마다 파일에서 새로 읽는다.
    → 다른 탭/기기에서 바꾼 내용이 바로 반영되고, 서로 덮어쓰지 않는다.
    """
    st.session_state.setdefault("editing_id", None)
    st.session_state.setdefault("add_msg", None)   # (level, text) 추가 폼 아래 표시
    st.session_state.setdefault("edit_msg", None)  # (level, text) 편집 중인 행에 표시할 경고
    st.session_state.setdefault("top_msgs", [])    # (level, text) 상단 표시 (저장 오류 등)
    st.session_state.setdefault("last_add_ts", 0.0)
    st.session_state["items"] = storage.load_items()

    for path in storage.pop_recovery_notices():
        st.session_state["top_msgs"].append(
            ("warning", f"저장 파일이 손상되어 빈 리스트로 시작했어요. 기존 파일은 `{path.name}`로 보관했어요.")
        )

    # 다른 곳에서 삭제된 아이템을 편집 중이었다면 편집 해제
    editing = st.session_state["editing_id"]
    if editing and not find_item(editing):
        cancel_edit()


def items() -> list[dict]:
    return st.session_state["items"]


def find_item(item_id: str, source: list[dict] | None = None) -> dict | None:
    return next((i for i in (source if source is not None else items()) if i["id"] == item_id), None)


def escape_md(text: str) -> str:
    """사용자 입력이 마크다운 서식으로 해석되지 않도록 특수문자 이스케이프 (알림·안내 문구용)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$:])", r"\\\1", text)


def norm_name(name: str) -> str:
    """중복 판정용: 대소문자·연속 공백 무시."""
    return " ".join(name.split()).casefold()


def mutate(fn) -> bool:
    """최신 파일을 읽어 fn(items)로 변경 후 저장. 성공하면 True."""
    try:
        with storage.transaction() as latest:
            fn(latest)
    except Abort:
        return False
    except StorageError as e:
        st.session_state["top_msgs"].append(("error", f"저장에 실패했어요. 잠시 후 다시 시도해 주세요. ({e})"))
        return False
    return True


def valid_qty(value) -> int | None:
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return None
    return qty if QTY_MIN <= qty <= QTY_MAX else None


# ---------------------------------------------------------------------------
# 콜백 (상태 변경은 모두 여기서 처리 → rerun 순서 문제 방지)
# ---------------------------------------------------------------------------
def add_item() -> None:
    name = " ".join(st.session_state["new_name"].split())
    memo = st.session_state["new_memo"].strip()
    qty = valid_qty(st.session_state["new_qty"])
    now = time.monotonic()

    if name:
        # 저장보다 먼저 기록: 연타 시 다음 실행이 이 콜백을 중간에 끊어도 시각은 남는다
        st.session_state["last_add_ts"] = now
    else:
        # 추가 버튼 연타로 비어 있는 폼이 다시 제출된 경우는 조용히 무시
        if now - st.session_state["last_add_ts"] > DOUBLE_SUBMIT_SEC:
            st.session_state["add_msg"] = ("warning", "아이템 이름을 입력해 주세요.")
        return
    if qty is None:
        st.session_state["add_msg"] = ("warning", QTY_WARNING)
        return

    result = {}

    def apply(latest: list[dict]) -> None:
        result["duplicate"] = any(norm_name(i["name"]) == norm_name(name) for i in latest)
        latest.append(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "quantity": qty,
                "memo": memo,
                "checked": False,
                "created_at": storage.now_iso(),
            }
        )

    if not mutate(apply):
        return
    st.toast(f"{escape_md(name)} 추가됨", icon="🛒")
    st.session_state["add_msg"] = (
        ("info", f"'{escape_md(name)}'은(는) 이미 리스트에 있는 아이템입니다. 그래도 추가했어요.")
        if result.get("duplicate")
        else None
    )


def toggle_item(item_id: str) -> None:
    checked = bool(st.session_state[f"chk_{item_id}"])

    def apply(latest: list[dict]) -> None:
        item = find_item(item_id, latest)
        if not item:
            raise Abort
        item["checked"] = checked

    mutate(apply)


def edit_keys(item_id: str) -> tuple[str, str, str]:
    return f"edit_name_{item_id}", f"edit_qty_{item_id}", f"edit_memo_{item_id}"


def fill_edit_widgets(item: dict, force: bool = False) -> None:
    """편집 입력칸을 아이템 값으로 채운다. force=False면 비어 있는 키만 채움."""
    for key, value in zip(edit_keys(item["id"]), (item["name"], item["quantity"], item["memo"])):
        if force or key not in st.session_state:
            st.session_state[key] = value


def start_edit(item_id: str) -> None:
    item = find_item(item_id)
    if not item:
        return
    st.session_state["editing_id"] = item_id  # 한 번에 한 행만 편집
    st.session_state["edit_msg"] = None
    fill_edit_widgets(item, force=True)


def update_item(item_id: str) -> None:
    k_name, k_qty, k_memo = edit_keys(item_id)
    name = " ".join(st.session_state[k_name].split())
    qty = valid_qty(st.session_state[k_qty])
    memo = st.session_state[k_memo].strip()
    if not name:
        st.session_state["edit_msg"] = ("warning", "이름은 비워 둘 수 없어요.")
        return
    if qty is None:
        st.session_state["edit_msg"] = ("warning", QTY_WARNING)
        return

    found = {}

    def apply(latest: list[dict]) -> None:
        item = find_item(item_id, latest)
        if not item:
            raise Abort
        found["ok"] = True
        item.update(name=name, quantity=qty, memo=memo)

    if mutate(apply):
        cancel_edit()
        st.toast(f"{escape_md(name)} 수정됨", icon="✏️")
    elif not found:
        cancel_edit()
        st.session_state["top_msgs"].append(("warning", "다른 곳에서 이미 삭제된 아이템이에요."))


def cancel_edit() -> None:
    st.session_state["editing_id"] = None
    st.session_state["edit_msg"] = None


def delete_item(item_id: str) -> None:
    removed = {}

    def apply(latest: list[dict]) -> None:
        item = find_item(item_id, latest)
        if not item:
            raise Abort
        removed["name"] = item["name"]
        latest[:] = [i for i in latest if i["id"] != item_id]

    if mutate(apply):
        st.toast(f"{escape_md(removed['name'])} 삭제됨", icon="🗑️")
    if st.session_state["editing_id"] == item_id:
        cancel_edit()


def clear_checked() -> None:
    removed = {}

    def apply(latest: list[dict]) -> None:
        removed["n"] = sum(1 for i in latest if i["checked"])
        if not removed["n"]:
            raise Abort
        latest[:] = [i for i in latest if not i["checked"]]

    if mutate(apply):
        st.toast(f"완료 항목 {removed['n']}개 삭제됨", icon="🧹")
    # 편집 중이던 아이템이 지워졌으면 다음 실행의 init_state에서 편집 해제됨


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
# 색은 하드코딩하지 않고 opacity / 반투명 색만 사용 → 라이트·다크 테마 모두 대응
CSS = """
<style>
/* 아이템 텍스트 */
.item-name { font-weight: 500; line-height: 1.35; word-break: break-word; }
.item-qty  { opacity: 0.75; font-size: 0.9em; margin-left: 0.3em; white-space: nowrap; }
.item-memo { opacity: 0.75; font-size: 0.85em; margin-top: 0.1em; word-break: break-word; }
.item-done .item-name, .item-done .item-qty { text-decoration: line-through; opacity: 0.72; }
.item-done .item-memo { opacity: 0.72; }

/* 아이템 카드 사이 간격 줄이기 */
[class*="st-key-row_"] { padding: 0.35rem 0.6rem !important; }

/* 아이콘 버튼: 작고 정사각형에 가깝게, 터치 영역 최소 40px */
[class*="st-key-row_"] [data-testid="stButton"] button {
    min-width: 40px; min-height: 40px; padding: 0 0.4rem;
}

/* 빈 리스트 안내 */
.empty-box { text-align: center; padding: 2rem 0.5rem; opacity: 0.8; }
.empty-box .emoji { font-size: 2.6rem; }

/* 진행률 퍼센트 */
.progress-label { text-align: right; font-size: 0.85em; opacity: 0.75; margin-bottom: -0.6rem; }

/* 텍스트 영역이 남는 폭을 차지하고, 좁아지면 줄바꿈 */
[class*="st-key-row_"] > div:has(.item-name) { flex: 1 1 0; min-width: 0; }
/* Streamlit 마크다운 기본 음수 하단 여백 제거 → 텍스트가 카드 세로 가운데 정렬, 메모 잘림 방지 */
[class*="st-key-row_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }

/* 좁은 화면 */
@media (max-width: 480px) {
    [class*="st-key-row_"] { padding: 0.3rem 0.4rem !important; gap: 0.25rem !important; }
    .item-name { font-size: 0.95em; }
    h1 { font-size: 2rem !important; white-space: nowrap; }  /* 제목 한 줄 유지 */
}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def show_messages(key: str) -> None:
    """세션에 쌓인 메시지를 한 번만 표시."""
    msgs = st.session_state[key]
    if not msgs:
        return
    for level, text in msgs if isinstance(msgs, list) else [msgs]:
        getattr(st, level)(text)
    st.session_state[key] = [] if isinstance(msgs, list) else None


def render_header() -> None:
    """제목 + 요약 영역."""
    st.title("🛒 쇼핑 리스트")
    show_messages("top_msgs")
    total = len(items())
    done = sum(1 for i in items() if i["checked"])
    ratio = done / total if total else 0.0
    st.caption(f"전체 **{total}**개 · 완료 **{done}**개 · 남은 **{total - done}**개")
    st.markdown(f'<div class="progress-label">{round(ratio * 100)}%</div>', unsafe_allow_html=True)
    st.progress(ratio)


def render_add_form() -> None:
    """아이템 추가 입력 폼 영역: [이름 | 수량 | 메모 | 추가]."""
    with st.form("add_form", clear_on_submit=True, border=True):
        c1, c2, c3, c4 = st.columns([4, 1.5, 3, 1.5], vertical_alignment="bottom")
        c1.text_input("아이템 이름", key="new_name", max_chars=NAME_MAX, placeholder="예: 우유")
        # 범위 제한은 콜백에서 검사 → 범위 밖 값이 조용히 1로 바뀌지 않고 경고 표시
        c2.number_input("수량", key="new_qty", value=1, step=1, help=f"{QTY_MIN}~{QTY_MAX}")
        c3.text_input("메모", key="new_memo", max_chars=MEMO_MAX, placeholder="선택 (예: 저지방)")
        c4.form_submit_button("추가", on_click=add_item, type="primary", width="stretch", help="리스트에 추가")
    show_messages("add_msg")


def render_filter() -> str:
    """보기 필터 영역 (전체/미완료/완료). 선택값 반환."""
    return st.radio("보기", FILTERS, key="filter", horizontal=True, label_visibility="collapsed")


def item_html(item: dict) -> str:
    """아이템 이름/수량/메모 HTML. 사용자 입력은 반드시 escape (XSS 방지)."""
    cls = "item-done" if item["checked"] else ""
    memo = f'<div class="item-memo">{html.escape(item["memo"])}</div>' if item["memo"] else ""
    return (
        f'<div class="{cls}"><span class="item-name">{html.escape(item["name"])}</span>'
        f'<span class="item-qty">×{item["quantity"]}</span>{memo}</div>'
    )


def render_item_row(item: dict) -> None:
    item_id, name = item["id"], item["name"]
    editing = st.session_state["editing_id"] is not None
    # 파일 기준 값으로 체크박스 상태 동기화 (다른 탭에서 바뀐 경우 반영)
    st.session_state[f"chk_{item_id}"] = item["checked"]
    # 가로 flex 컨테이너, wrap=False: 모바일에서도 [체크 | 텍스트(가변) | ✏️ | 🗑️] 한 줄 유지
    with st.container(
        border=True,
        key=f"row_{item_id}",
        horizontal=True,
        wrap=False,
        vertical_alignment="center",
        gap="small",
    ):
        st.checkbox(
            f"{name} 구매 완료",  # 화면에는 숨기지만 스크린 리더가 행을 구분할 수 있게 이름 포함
            key=f"chk_{item_id}",
            on_change=toggle_item,
            args=(item_id,),
            label_visibility="collapsed",
        )
        st.markdown(item_html(item), unsafe_allow_html=True, width="stretch")
        # 다른 행을 편집 중이면 ✏️ 비활성화 → 작성 중인 내용이 조용히 버려지지 않게
        st.button(
            "✏️", key=f"edit_{item_id}", on_click=start_edit, args=(item_id,), type="tertiary",
            disabled=editing, help="다른 항목 수정을 먼저 끝내 주세요" if editing else f"{name} 수정",
        )
        st.button("🗑️", key=f"del_{item_id}", on_click=delete_item, args=(item_id,), type="tertiary", help=f"{name} 삭제")


def render_edit_row(item: dict) -> None:
    item_id = item["id"]
    # 필터 전환 등으로 입력칸 상태가 사라졌으면 아이템 값으로 다시 채움
    fill_edit_widgets(item)
    k_name, k_qty, k_memo = edit_keys(item_id)
    with st.container(border=True, key=f"editrow_{item_id}"):
        st.caption("✏️ 아이템 수정")
        c1, c2, c3 = st.columns([4, 1.5, 3])
        c1.text_input("이름", key=k_name, max_chars=NAME_MAX)
        c2.number_input("수량", key=k_qty, step=1, help=f"{QTY_MIN}~{QTY_MAX}")
        c3.text_input("메모", key=k_memo, max_chars=MEMO_MAX)
        show_messages("edit_msg")
        with st.container(horizontal=True, gap="small"):
            st.button("저장", key=f"save_{item_id}", type="primary", on_click=update_item, args=(item_id,), help="변경 내용 저장")
            st.button("취소", key=f"cancel_{item_id}", on_click=cancel_edit, help="수정 취소")
            st.space("stretch")  # 삭제 버튼은 오른쪽 끝으로
            st.button("🗑️ 삭제", key=f"del_{item_id}", on_click=delete_item, args=(item_id,), help=f"{item['name']} 삭제")


def render_empty(text: str, emoji: str = "🧺") -> None:
    st.markdown(
        f'<div class="empty-box"><div class="emoji">{emoji}</div><div>{text}</div></div>',
        unsafe_allow_html=True,
    )


def render_list(filter_value: str) -> None:
    """아이템 리스트 영역. 미완료 → 완료 순, 각 그룹은 생성 순.

    편집 중인 아이템은 필터와 상관없이 항상 표시한다.
    """
    if not items():
        render_empty("리스트가 비어 있어요.<br>사야 할 것을 추가해 보세요!")
        return

    editing = st.session_state["editing_id"]
    ordered = sorted(items(), key=lambda i: i["checked"])  # stable sort
    if filter_value == "미완료":
        ordered = [i for i in ordered if not i["checked"] or i["id"] == editing]
    elif filter_value == "완료":
        ordered = [i for i in ordered if i["checked"] or i["id"] == editing]

    if not ordered:
        render_empty(f"'{filter_value}' 항목이 없어요.", "🔍")
        return

    for item in ordered:
        if item["id"] == editing:
            render_edit_row(item)
        else:
            render_item_row(item)


def render_footer() -> None:
    """하단 버튼 영역 (오른쪽 정렬). 일괄 삭제는 확인 단계를 거친다."""
    n_checked = sum(1 for i in items() if i["checked"])
    with st.container(horizontal_alignment="right"):
        with st.popover(
            "🧹 완료 항목 삭제",
            disabled=not n_checked,
            help="체크된 항목을 모두 삭제합니다",
            key="clear_popover",
        ):
            st.write(f"완료 항목 **{n_checked}**개를 삭제할까요?")
            st.button("삭제", key="clear_checked", type="primary", on_click=clear_checked, width="stretch")


def main() -> None:
    init_state()
    inject_css()
    render_header()
    render_add_form()
    st.divider()
    filter_value = render_filter()
    render_list(filter_value)
    st.divider()
    render_footer()


main()
