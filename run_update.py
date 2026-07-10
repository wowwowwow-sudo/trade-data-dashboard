"""
다중 실행자(여러 PC/여러 번 실행) 대비 안전 실행 래퍼.

기존 run_pipeline.bat와의 차이:
  - 시작 전 git pull --ff-only로 원격 변경사항을 먼저 받는다 (실패 시 중단)
  - trade_history_long.csv / company_trade_history_long.csv 원본 데이터도
    data/interlink와 함께 commit/push한다 (run_pipeline.bat는 interlink만 push해서
    원본 CSV 갱신이 로컬에만 쌓이고 원격에 반영되지 않는 문제가 있었음)
  - push가 경합(원격이 앞서 있음)으로 거절되면 pull --rebase 후 1회만 재시도한다

순서: git pull --ff-only -> scrape_bigfinance.py -> scripts/export_interlink.py
     -> 변경 있으면 데이터 파일만 git add -> commit -> push (실패 시 rebase 후 재시도 1회)

실행: python run_update.py
스케줄러 태스크(BigFinance_TradeScrape)가 run_pipeline.bat 대신 이 스크립트를
호출하도록 바꾸려면 "Task To Run"을
  python.exe C:\\...\\수출입_대시보드\\run_update.py
로 변경하면 된다 (가상환경을 쓰는 경우 해당 python.exe 경로 사용).
"""

from __future__ import annotations

import getpass
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

DATA_PATHS = [
    "trade_history_long.csv",
    "company_trade_history_long.csv",
    "data/interlink",
]


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=capture,
    )


def git_pull_ff_only() -> bool:
    result = run(["git", "pull", "--ff-only"])
    if result.returncode != 0:
        print(
            "[중단] git pull --ff-only 실패 - 로컬과 원격이 갈라져 있을 수 있습니다. "
            "수동으로 확인 후 다시 실행해주세요.",
            file=sys.stderr,
        )
        return False
    return True


def run_script(script: str) -> bool:
    result = run([sys.executable, script])
    if result.returncode != 0:
        print(f"[중단] {script} 실행 실패", file=sys.stderr)
        return False
    return True


def has_data_changes() -> bool:
    result = run(["git", "status", "--porcelain", *DATA_PATHS], capture=True)
    return bool(result.stdout.strip())


def commit_and_push() -> bool:
    run(["git", "add", *DATA_PATHS])

    today = datetime.now().strftime("%Y-%m-%d")
    message = f"auto: 데이터 갱신 {today} (실행자: {getpass.getuser()})"
    commit = run(["git", "commit", "-m", message])
    if commit.returncode != 0:
        print("[정보] 커밋할 변경사항이 없습니다.")
        return True

    push = run(["git", "push"])
    if push.returncode == 0:
        print("[완료] push 성공")
        return True

    print("[경고] push 실패 - 원격이 앞서 있을 수 있습니다. pull --rebase 후 1회 재시도합니다.")
    rebase = run(["git", "pull", "--rebase"])
    if rebase.returncode != 0:
        print(
            "[실패] pull --rebase 실패 - 충돌이 발생했을 수 있습니다. 수동 확인이 필요합니다.",
            file=sys.stderr,
        )
        return False

    retry = run(["git", "push"])
    if retry.returncode != 0:
        print("[실패] 재시도 push도 실패했습니다. 수동 확인이 필요합니다.", file=sys.stderr)
        return False

    print("[완료] 재시도 push 성공")
    return True


def main() -> int:
    if not git_pull_ff_only():
        return 1

    if not run_script("scrape_bigfinance.py"):
        return 1

    if not run_script("scripts/export_interlink.py"):
        return 1

    if not has_data_changes():
        print("[정보] 데이터 변경사항이 없습니다. 커밋을 건너뜁니다.")
        return 0

    return 0 if commit_and_push() else 1


if __name__ == "__main__":
    sys.exit(main())
