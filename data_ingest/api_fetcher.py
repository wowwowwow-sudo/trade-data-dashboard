"""
ApiFetcher: EPIC Finance(bigfinance.co.kr) 공식 API 연동용 스텁.

현재 회사 계정에 API 기능이 활성화되어 있는지 담당자에게 확인 중이라 아직 구현하지
않았다. 문서를 받으면 fetch_latest() 내부만 채우면 되도록 골격만 잡아둔 상태.

TODO (API 문서 확보 후):
  - BASE_URL / 엔드포인트 경로
  - 인증 방식 (API Key 헤더? OAuth2? Basic Auth?) 및 토큰 갱신 로직
  - 요청 파라미터 (조회 기간, 품목 필터, 페이지네이션 여부 등)
  - 응답(JSON/XML 등) -> 표준 레코드 포맷(품목명/대분류/기준일/수출금액/단가) 매핑
  - 마지막으로 가져온 시점 이후의 "새 데이터만" 가져오는 기준 (예: 기준일 커서 저장)

자격증명은 .env(.env.example 참고)에서 환경변수로만 읽는다. 코드에 하드코딩하지 않는다.
"""

from __future__ import annotations

import os

from .base import BaseFetcher


class ApiFetcher(BaseFetcher):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        # TODO: 실제 인증/파라미터 방식이 정해지면 생성자 시그니처를 맞춰서 수정
        self.base_url = base_url or os.environ.get("EPIC_API_BASE_URL")
        self.api_key = api_key or os.environ.get("EPIC_API_KEY")

    def fetch_latest(self) -> list[dict]:
        # TODO: API 문서 확보 후 구현
        #   1) requests(또는 httpx)로 최신 스냅샷 엔드포인트 호출
        #      (self.base_url, self.api_key 사용 - 절대 하드코딩하지 말 것)
        #   2) 인증 헤더/쿼리 파라미터 설정
        #   3) 응답을 표준 레코드 포맷으로 변환
        #      [{"품목명": ..., "대분류": ..., "기준일": "YYYY-MM-DD",
        #        "수출금액": ..., "단가": ...}, ...]
        #   4) list[dict] 반환 (새 데이터 없으면 빈 리스트)
        raise NotImplementedError(
            "ApiFetcher는 아직 구현되지 않았습니다. "
            "EPIC Finance API 연동(엔드포인트/인증 방식) 확정 후 구현 예정입니다."
        )
