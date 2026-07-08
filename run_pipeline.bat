@echo off
setlocal enabledelayedexpansion
REM Windows 작업 스케줄러(BigFinance_TradeScrape)가 호출하는 배치 진입점.
REM 1) EPIC Finance 스크래핑 -> 2) 연계 데이터 발행(scripts\export_interlink.py)
REM    -> 3) data/interlink만 git commit/push (push 실패는 배치 실패로 취급하지 않음)
cd /d "%~dp0"

echo [%date% %time%] 파이프라인 시작

python scrape_bigfinance.py
if errorlevel 1 (
    echo [%date% %time%] scrape_bigfinance.py 실패 - 파이프라인 중단
    exit /b 1
)

python scripts\export_interlink.py
if errorlevel 1 (
    echo [%date% %time%] export_interlink.py 실패 - 파이프라인 중단
    exit /b 1
)

if not exist "logs" mkdir "logs"

set DATA_THROUGH=
for /f "usebackq delims=" %%J in (`python -c "import json;print(json.load(open('data/interlink/meta.json',encoding='utf-8-sig'))['data_through'])" 2^>nul`) do set DATA_THROUGH=%%J

git add data/interlink
git commit -m "interlink: data through %DATA_THROUGH%"
if errorlevel 1 (
    echo [%date% %time%] git commit 변경사항 없음 또는 실패 - push 단계로 계속 진행
)

git push
if errorlevel 1 (
    echo [%date% %time%] git push 실패 - logs\interlink.log에 기록하고 배치는 정상 종료 처리 >> logs\interlink.log
    echo [%date% %time%] git push FAILED (data_through=%DATA_THROUGH%) >> logs\interlink.log
) else (
    echo [%date% %time%] git push 성공 (data_through=%DATA_THROUGH%)
)

echo [%date% %time%] 파이프라인 종료
exit /b 0
