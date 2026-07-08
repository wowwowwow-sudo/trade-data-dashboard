"""
ExcelDropFetcher: EPIC Finance(bigfinance.co.kr)에서 수동으로 다운로드한 엑셀 파일을
incoming/ 폴더에서 찾아 표준 레코드 포맷으로 파싱하는 BaseFetcher 구현체.

현재 EPIC Finance 계정에 API/FTP 연동이 활성화되어 있는지 확인 중이라, 그 전까지는
이 방식(수동 다운로드 + 자동 파싱)이 기본 데이터 수집 경로다.

흐름:
  1. EPIC Finance 웹 화면에서 엑셀을 다운로드해 incoming/ 폴더에 넣는다.
  2. run_ingest.py 실행 -> fetch_latest()가 incoming/의 새 엑셀을 모두 열어
     column_mapping.json 설정대로 컬럼명을 표준 이름으로 맞추고 레코드 리스트로 반환.
  3. append_snapshot()이 성공하면 run_ingest.py가 on_success()를 호출 ->
     이번에 읽은 파일들을 processed/ 폴더로 이동 (다음 실행에서 중복 처리 방지).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from .base import BaseFetcher

REQUIRED_STANDARD_COLUMNS = ["품목명", "기준일", "수출금액", "단가"]
OPTIONAL_STANDARD_COLUMNS = ["대분류"]

DEFAULT_COLUMN_MAPPING_PATH = Path(__file__).parent / "column_mapping.json"
EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm")


class ExcelDropFetcher(BaseFetcher):
    def __init__(
        self,
        incoming_dir: str | Path = "incoming",
        processed_dir: str | Path = "processed",
        column_mapping_path: str | Path = DEFAULT_COLUMN_MAPPING_PATH,
        sheet_name: str | int = 0,
    ):
        self.incoming_dir = Path(incoming_dir)
        self.processed_dir = Path(processed_dir)
        self.column_mapping_path = Path(column_mapping_path)
        self.sheet_name = sheet_name

        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        self._column_mapping = self._load_column_mapping()
        self._pending_files: list[Path] = []  # 이번 fetch_latest() 호출에서 읽은 파일들

    # ---------- 컬럼 매핑 ----------
    def _load_column_mapping(self) -> dict[str, list[str]]:
        if not self.column_mapping_path.exists():
            raise FileNotFoundError(
                f"컬럼 매핑 설정 파일을 찾을 수 없습니다: {self.column_mapping_path}"
            )
        with open(self.column_mapping_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # "_comment" 등 밑줄로 시작하는 키는 설정 파일 안의 주석용이므로 제외
        return {k: v for k, v in raw.items() if not k.startswith("_")}

    def _rename_to_standard(self, df: pd.DataFrame, source_file: Path) -> pd.DataFrame:
        df = df.rename(columns=lambda c: str(c).strip())
        rename_map = {}
        for standard_name, aliases in self._column_mapping.items():
            match = next(
                (c for c in df.columns if c == standard_name or c in aliases), None
            )
            if match:
                rename_map[match] = standard_name
        df = df.rename(columns=rename_map)

        missing = [c for c in REQUIRED_STANDARD_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"{source_file.name}: 필수 컬럼을 찾지 못했습니다 {missing}. "
                f"data_ingest/column_mapping.json에 실제 엑셀 컬럼명을 추가해주세요. "
                f"(엑셀 원본 컬럼: {list(df.columns)})"
            )
        keep_cols = [
            c for c in REQUIRED_STANDARD_COLUMNS + OPTIONAL_STANDARD_COLUMNS if c in df.columns
        ]
        return df[keep_cols]

    # ---------- 값 정규화 ----------
    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["기준일"] = pd.to_datetime(df["기준일"]).dt.strftime("%Y-%m-%d")
        for col in ("수출금액", "단가"):
            if df[col].dtype == object:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(",", "", regex=False)
                    .str.replace(" ", "", regex=False)
                )
            df[col] = pd.to_numeric(df[col], errors="coerce")
        before = len(df)
        df = df.dropna(subset=["품목명", "기준일", "수출금액", "단가"])
        dropped = before - len(df)
        if dropped:
            print(f"경고: 값이 비어있거나 숫자로 변환할 수 없는 {dropped}개 행을 건너뜁니다.")
        return df

    # ---------- 파일 탐색 ----------
    def _list_new_excel_files(self) -> list[Path]:
        files = [
            p
            for p in self.incoming_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in EXCEL_EXTENSIONS
            and not p.name.startswith("~$")  # 엑셀이 열려있을 때 생기는 임시/잠금 파일 제외
        ]
        return sorted(files, key=lambda p: p.name)

    # ---------- BaseFetcher 구현 ----------
    def fetch_latest(self) -> list[dict]:
        self._pending_files = []
        records: list[dict] = []

        for path in self._list_new_excel_files():
            try:
                raw = pd.read_excel(path, sheet_name=self.sheet_name)
            except Exception as exc:
                raise RuntimeError(f"{path.name} 파일을 여는 중 오류가 발생했습니다: {exc}") from exc

            df = self._rename_to_standard(raw, path)
            df = self._normalize(df)
            records.extend(df.to_dict(orient="records"))
            self._pending_files.append(path)
            print(f"{path.name}: {len(df)}개 행 파싱 완료")

        return records

    def on_success(self) -> None:
        """append_snapshot() 반영이 끝난 뒤, 이번에 읽은 파일들을 processed/로 이동."""
        for path in self._pending_files:
            if not path.exists():
                continue
            dest = self.processed_dir / path.name
            if dest.exists():
                stamp = pd.Timestamp.now().strftime("%Y%m%d%H%M%S")
                dest = self.processed_dir / f"{path.stem}_{stamp}{path.suffix}"
            shutil.move(str(path), str(dest))
            print(f"이동 완료: {path.name} -> {dest.relative_to(self.processed_dir.parent)}")
        self._pending_files = []
