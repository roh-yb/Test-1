"""쇼핑 리스트 데이터를 JSON 파일로 저장/로드한다.

- 모든 읽기/쓰기는 프로세스 전역 잠금(_LOCK) 안에서 수행한다.
  Streamlit은 세션(탭)마다 같은 프로세스의 다른 스레드에서 실행되므로,
  transaction()으로 "읽기 → 변경 → 저장"을 한 번에 처리하면 탭끼리 덮어쓰지 않는다.
- 저장은 임시 파일에 쓴 뒤 os.replace()로 교체(원자적)해서, 쓰는 도중 중단돼도 파일이 깨지지 않는다.
- 손상된 파일은 지우지 않고 shopping_list.corrupt-<시각>.json으로 옮겨 보관한다.
"""

import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# app.py와 같은 폴더 기준 data/shopping_list.json
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = DATA_DIR / "shopping_list.json"

NAME_MAX = 50
MEMO_MAX = 100
QTY_MIN = 1
QTY_MAX = 999

_LOCK = threading.RLock()
_recovered: list[Path] = []  # 손상 파일을 옮겨 둔 경로 (화면 안내용)


class StorageError(Exception):
    """파일 저장 실패."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_item(raw: object) -> dict | None:
    """항목을 데이터 모델(PRD 5장)에 맞게 정규화. 쓸 수 없는 항목이면 None."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:NAME_MAX]
    if not name:
        return None
    try:
        qty = int(raw.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    memo = raw.get("memo")
    return {
        "id": str(raw.get("id") or uuid.uuid4()),
        "name": name,
        "quantity": min(max(qty, QTY_MIN), QTY_MAX),
        "memo": ("" if memo is None else str(memo)).strip()[:MEMO_MAX],
        "checked": bool(raw.get("checked", False)),
        "created_at": str(raw.get("created_at") or now_iso()),
    }


def _quarantine_corrupt_file() -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = DATA_DIR / f"shopping_list.corrupt-{stamp}.json"
    try:
        os.replace(DATA_FILE, target)
        _recovered.append(target)
    except OSError:
        pass


def _load_unlocked() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _quarantine_corrupt_file()
        return []
    except OSError:
        return []
    if not isinstance(data, list):
        _quarantine_corrupt_file()
        return []

    items, seen = [], set()
    for raw in data:
        item = normalize_item(raw)
        if item is None:
            continue
        if item["id"] in seen:  # id 중복이면 새 id 부여 (위젯 key 충돌 방지)
            item["id"] = str(uuid.uuid4())
        seen.add(item["id"])
        items.append(item)
    return items


def _save_unlocked(items: list[dict]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".shopping_list.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, DATA_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        raise StorageError(str(e)) from e


def load_items() -> list[dict]:
    """저장된 아이템 목록을 읽는다. 파일이 없거나 손상되면 빈 리스트를 반환한다."""
    with _LOCK:
        return _load_unlocked()


def save_items(items: list[dict]) -> None:
    """아이템 목록 전체를 저장한다. 실패하면 StorageError."""
    with _LOCK:
        _save_unlocked(items)


@contextmanager
def transaction() -> Iterator[list[dict]]:
    """최신 파일 내용을 읽어 넘겨주고, 블록이 정상 종료되면 저장한다.

    with transaction() as items:
        items.append(...)
    """
    with _LOCK:
        items = _load_unlocked()
        yield items
        _save_unlocked(items)


def pop_recovery_notices() -> list[Path]:
    """손상 파일을 옮긴 기록을 꺼낸다 (한 번만 안내)."""
    with _LOCK:
        out = list(_recovered)
        _recovered.clear()
        return out
