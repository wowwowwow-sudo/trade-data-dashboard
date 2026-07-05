"""
FtpFetcher: EPIC Finance(bigfinance.co.kr) FTP 연동용 스텁.

API와 마찬가지로 FTP 연동 활성화 여부를 확인 중이라 fetch_latest()는 아직
구현하지 않았다. 접속 정보를 받으면 이 파일만 채우면 된다.

TODO (FTP 접속 정보 확보 후):
  - 호스트/포트, 인증 방식(일반 FTP vs SFTP/FTPS), 원격 디렉터리 경로
  - 파일명 규칙 (예: 날짜별 파일) 및 "새 파일"을 판별하는 기준
  - 다운로드한 파일의 파싱 방식 (ExcelDropFetcher의 파싱 로직 재사용 가능)
  - 처리 완료 후 원격/로컬 파일 정리 방식 (on_success 활용)

자격증명은 .env(.env.example 참고)에서 환경변수로만 읽는다. 코드에 하드코딩하지 않는다.
"""

from __future__ import annotations

import os

from .base import BaseFetcher


class FtpFetcher(BaseFetcher):
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        remote_dir: str | None = None,
    ):
        # TODO: 실제 접속 정보/인증 방식이 정해지면 생성자 시그니처를 맞춰서 수정
        self.host = host or os.environ.get("EPIC_FTP_HOST")
        self.port = port or int(os.environ.get("EPIC_FTP_PORT", "21"))
        self.user = user or os.environ.get("EPIC_FTP_USER")
        self.password = password or os.environ.get("EPIC_FTP_PASSWORD")
        self.remote_dir = remote_dir or os.environ.get("EPIC_FTP_REMOTE_DIR")

    def fetch_latest(self) -> list[dict]:
        # TODO: FTP 접속 정보 확보 후 구현
        #   1) ftplib(또는 SFTP면 paramiko)로 self.host:self.port 접속
        #      (self.user, self.password 사용 - 절대 하드코딩하지 말 것)
        #   2) self.remote_dir에서 아직 처리하지 않은 새 파일 목록 조회
        #   3) 파일 다운로드 후 파싱 (엑셀이면 ExcelDropFetcher의 파싱 로직 재사용 가능)
        #   4) 표준 레코드 포맷으로 변환해 list[dict] 반환 (새 데이터 없으면 빈 리스트)
        raise NotImplementedError(
            "FtpFetcher는 아직 구현되지 않았습니다. "
            "EPIC Finance FTP 연동(접속 정보) 확정 후 구현 예정입니다."
        )
