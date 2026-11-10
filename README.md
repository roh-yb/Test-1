# 🛒 쇼핑 리스트

아이템 추가 · 수정 · 삭제 · 체크 기능이 있는 간단한 쇼핑 리스트 웹앱입니다.
Python + Streamlit으로 만들었으며, 요구사항은 [PRD.md](PRD.md)를 참고하세요.

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 http://localhost:8501 로 접속합니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `app.py` | Streamlit 메인 앱 (화면 구성) |
| `storage.py` | `data/shopping_list.json` 로드/저장 |
| `data/` | 리스트 데이터 저장 폴더 (앱 실행 시 자동 생성) |

## 배포 시 주의 (Streamlit Community Cloud)

- `data/`는 `.gitignore`에 포함되어 저장소에 올라가지 않습니다. 배포된 앱은 빈 리스트로 시작합니다.
- 로그인 기능이 없어서 **주소를 아는 사람은 모두 같은 리스트를 보고 수정·삭제할 수 있습니다.** 혼자 쓰려면 Streamlit Cloud 앱 설정에서 공개 범위를 본인 또는 초대한 사람으로 제한하세요.
- 클라우드 컨테이너가 재시작되거나 다시 배포되면 저장 파일이 초기화될 수 있습니다. 오래 보관하려면 Google Sheets나 Supabase 같은 외부 저장소를 쓰세요(PRD 7.4).
