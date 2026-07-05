"""
data_ingest: EPIC Finance(bigfinance.co.kr) 스냅샷 데이터를 수집하는 교체 가능한 계층.

현재 사용 가능:
  - ExcelDropFetcher: incoming/ 폴더에 수동으로 넣은 엑셀 파일을 파싱 (현재 사용 중)

준비 중 (API/FTP 접근 확정 후 구현):
  - ApiFetcher
  - FtpFetcher

모든 구현체는 BaseFetcher(fetch_latest() -> list[dict])를 따른다.
"""

from dotenv import load_dotenv

# ApiFetcher/FtpFetcher가 os.environ에서 자격증명을 읽을 수 있도록 .env를 로드해둔다.
# .env 파일이 없어도(ExcelDropFetcher만 쓰는 지금 단계) 조용히 넘어간다.
load_dotenv()

from .base import BaseFetcher
from .excel_fetcher import ExcelDropFetcher
from .api_fetcher import ApiFetcher
from .ftp_fetcher import FtpFetcher

__all__ = ["BaseFetcher", "ExcelDropFetcher", "ApiFetcher", "FtpFetcher"]
