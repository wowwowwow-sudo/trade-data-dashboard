# 수출입 데이터 대시보드

EPIC Finance(bigfinance.co.kr) 커스텀 워치리스트 기반 수출입 품목 카드 대시보드.

## 실행
```
pip install -r requirements.txt
streamlit run app.py
```

## 데이터 갱신 상태 (현재: 수동 다운로드 + 자동 파싱)

**현재는 수동 다운로드 + 자동 파싱(ExcelDropFetcher) 단계다.** EPIC Finance가 공식
API/FTP 연동을 지원한다고 하지만, 회사 계정에 해당 기능이 활성화되어 있는지는 아직
담당자에게 확인 중이다. 활성화가 확정되면 `data_ingest/api_fetcher.py`(`ApiFetcher`)
또는 `data_ingest/ftp_fetcher.py`(`FtpFetcher`)만 구현해서 교체할 예정이고,
`run_ingest.py`나 `append_snapshot.py` 쪽 로직은 바꿀 필요가 없다.

> 참고: EPIC Finance 로그인 자격증명을 이용한 브라우저 자동 로그인/스크래핑은 계정
> 정책 확인 전이라 만들지 않았다. 지금은 사람이 직접 웹 화면에서 엑셀을 다운로드해야 한다.

### 데이터 수집 계층 구조 (`data_ingest/`)

```
data_ingest/
  base.py            # BaseFetcher 공통 인터페이스 (fetch_latest() -> list[dict])
  excel_fetcher.py    # ExcelDropFetcher - 현재 사용 중
  api_fetcher.py      # ApiFetcher - 스텁 (API 문서 확보 후 구현 예정)
  ftp_fetcher.py      # FtpFetcher - 스텁 (FTP 접속 정보 확보 후 구현 예정)
  column_mapping.json # 엑셀 컬럼명 <-> 표준 컬럼명 매핑 설정
```

모든 구현체는 `fetch_latest() -> list[dict]`로 아래 표준 포맷의 레코드를 반환한다
(이 포맷은 그대로 `append_snapshot()`에 넘길 수 있다):

```python
{"품목명": "반도체_메모리", "대분류": "반도체", "기준일": "2026-07-31",
 "수출금액": 31000000000, "단가": 81000.0}
```

### 지금 하는 방법 (ExcelDropFetcher)

1. bigfinance.co.kr에 로그인 후 "잠정 수출 품목 리스트" 화면에서 엑셀을 다운로드해
   `incoming/` 폴더에 넣는다.
2. 실행:
   ```
   python run_ingest.py
   ```
3. `incoming/`에 있는 새 엑셀 파일을 모두 열어 파싱하고, `append_snapshot()`으로
   `trade_history_long.csv`에 반영한다 (품목명/기준일 조합이 이미 있으면 최신 값으로
   덮어쓰고, 없으면 새 행 추가 - 기존 로직 그대로).
4. 처리에 성공한 파일은 `processed/` 폴더로 자동 이동한다 (다음 실행에서 중복 처리 방지).
5. 앱을 새로고침하면 반영된 데이터가 보인다.

엑셀 헤더가 EPIC Finance 다운로드 포맷과 다르거나 바뀌었다면, 코드를 고치지 않고
`data_ingest/column_mapping.json`에 실제 컬럼명을 추가하면 된다:

```json
{
  "품목명": ["품목명", "품목", "Item Name"],
  "기준일": ["기준일", "기준연월", "Date"],
  ...
}
```

### 나중에 API/FTP로 전환하는 방법

1. `.env.example`을 `.env`로 복사하고 담당자에게 받은 값을 채운다:
   ```
   cp .env.example .env
   ```
   (`.env`는 `.gitignore`에 등록되어 있어 커밋되지 않는다. 자격증명은 절대 코드에
   하드코딩하지 않는다.)
2. `data_ingest/api_fetcher.py` (또는 `ftp_fetcher.py`)의 `fetch_latest()`를
   실제 API/FTP 문서에 맞춰 구현한다. `TODO` 주석에 채워야 할 항목이 정리되어 있다.
3. `run_ingest.py`에서 아래 한 줄만 바꾸면 나머지 흐름(파싱 -> append_snapshot ->
   후처리)은 그대로 재사용된다:
   ```python
   fetcher = ExcelDropFetcher()   # -> ApiFetcher() 또는 FtpFetcher()로 교체
   ```

### Windows 작업 스케줄러로 매일 자동 실행하기 (선택, 로컬에서 직접 설정)

1. `run_ingest.bat` 같은 배치 파일을 만들어 저장소 경로에 둔다 (예시):
   ```bat
   cd /d "C:\path\to\trade-data-dashboard"
   "C:\path\to\python.exe" run_ingest.py >> ingest_log.txt 2>&1
   ```
   (`where python`으로 python.exe 경로 확인 가능)
2. 시작 메뉴에서 "작업 스케줄러(Task Scheduler)"를 연다.
3. 오른쪽 "작업 만들기(Create Task...)" 클릭.
4. **일반** 탭: 이름 입력 (예: `trade-data-ingest`), "가장 높은 권한으로 실행" 체크(선택).
5. **트리거** 탭 -> "새로 만들기" -> 매일, 원하는 시간 지정 (예: 매일 오전 9시).
6. **동작** 탭 -> "새로 만들기" -> 프로그램/스크립트에 위에서 만든
   `run_ingest.bat` 경로 지정.
7. **조건/설정** 탭은 기본값으로 두거나 필요에 맞게 조정 후 확인.
8. 참고: 이 방식은 여전히 `incoming/`에 엑셀 파일이 들어와 있어야 반영된다.
   EPIC Finance API/FTP 연동이 확정되기 전까지는, 엑셀 다운로드 자체는 사람이
   직접 해야 한다.

품목이 쌓일수록 카드의 추이 막대(스파크라인)가 자동으로 길어집니다.
