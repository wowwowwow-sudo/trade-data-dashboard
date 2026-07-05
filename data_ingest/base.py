"""
data_ingest 공통 인터페이스.

새 스냅샷 데이터를 어디서 가져오든(엑셀 수동 다운로드, API, FTP 등) append_snapshot()에
그대로 넘길 수 있는 동일한 표준 포맷으로 변환해서 돌려주는 것이 이 모듈의 역할이다.
데이터 수집 방식이 바뀌어도(엑셀 -> API/FTP) run_ingest.py나 append_snapshot.py는
건드릴 필요가 없도록, fetch_latest() 뒤에서 각 구현체가 알아서 처리한다.

표준 레코드 포맷 (append_snapshot.append_snapshot()이 기대하는 것과 동일):
    {
        "품목명": str,           # 필수
        "대분류": str,           # 선택 - 없으면 append_snapshot이 품목명 앞부분("_" 이전)에서 자동 추출
        "기준일": str,           # 필수, "YYYY-MM-DD" 형식
        "수출금액": float | int,  # 필수
        "단가": float,           # 필수
    }
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseFetcher(ABC):
    """모든 데이터 수집기가 구현해야 하는 공통 인터페이스."""

    @abstractmethod
    def fetch_latest(self) -> list[dict]:
        """
        새로 반영할 스냅샷 레코드를 표준 포맷의 list[dict]로 반환한다.
        새 데이터가 없으면 (예외를 던지지 말고) 빈 리스트를 반환해야 한다.
        """
        raise NotImplementedError

    def on_success(self) -> None:
        """
        fetch_latest()가 반환한 레코드가 append_snapshot()에 성공적으로 반영된 '이후'
        호출된다. 처리 완료 표시(파일 이동, 커서/오프셋 저장 등)가 필요한 구현체만
        override하면 된다. 기본 동작은 아무 것도 하지 않음.
        """
        return None
