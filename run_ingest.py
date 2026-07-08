"""
수동 실행 스크립트: EPIC Finance(bigfinance.co.kr)에서 다운로드한 엑셀을
trade_history_long.csv에 반영한다.

사용법:
  1. bigfinance.co.kr에서 "잠정 수출 품목 리스트" 엑셀을 다운로드해 incoming/ 폴더에 넣는다.
  2. python run_ingest.py 실행
  3. 새 데이터가 append_snapshot()으로 trade_history_long.csv에 반영되고,
     이번에 처리한 엑셀 파일은 processed/ 폴더로 자동 이동한다 (중복 처리 방지).

지금은 ExcelDropFetcher(수동 다운로드 + 자동 파싱)만 연결되어 있다.
추후 EPIC Finance API/FTP 연동이 확정되면 이 스크립트에서 fetcher만
ApiFetcher()/FtpFetcher()로 교체하면 되고, 나머지 흐름은 그대로 재사용된다.

Windows 작업 스케줄러로 이 스크립트를 매일 자동 실행하는 방법은 README.md 참고.
"""

from __future__ import annotations

import sys

from append_snapshot import append_snapshot
from data_ingest.excel_fetcher import ExcelDropFetcher


def main() -> int:
    fetcher = ExcelDropFetcher()

    try:
        records = fetcher.fetch_latest()
    except Exception as exc:
        print(f"데이터 수집 중 오류가 발생했습니다: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("incoming/ 폴더에 새로 처리할 엑셀 파일이 없습니다.")
        return 0

    print(f"{len(records)}개 레코드를 찾았습니다. trade_history_long.csv에 반영합니다...")
    try:
        append_snapshot(records)
    except Exception as exc:
        print(f"trade_history_long.csv 반영 중 오류가 발생했습니다: {exc}", file=sys.stderr)
        print("처리한 엑셀 파일은 incoming/에 그대로 남겨두고 종료합니다 (재실행 가능).", file=sys.stderr)
        return 1

    fetcher.on_success()
    print("완료: 처리된 엑셀 파일을 processed/ 폴더로 이동했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
