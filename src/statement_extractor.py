from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from system_logger import get_logger


ACTIVITY_HEADING = "Activity - Current period"
FUTURE_SETTLEMENT_HEADING = "Transactions for Future Settlement"
GLOSSARY_HEADING = "Information about Statement Codes"
OUTPUT_COLUMNS = [
    "date",
    "transaction",
    "ticker_id",
    "quantity",
    "execDate",
    "fx_rate",
    "debit",
    "credit",
    "balance",
    "statement_code",
    "description",
]
DATE_CODE_PATTERN = re.compile(r"^\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<code>[A-Z0-9]+)\b")
MONEY_AT_END_PATTERN = re.compile(
    r"^(?P<prefix>.*?)\s+"
    r"(?P<debit>\$[\d,]+\.\d{2})\s+"
    r"(?P<credit>\$[\d,]+\.\d{2})\s+"
    r"(?P<balance>\$[\d,]+\.\d{2})\s*$"
)
GLOSSARY_ENTRY_PATTERN = re.compile(r"^(?P<code>[A-Z0-9]+)\s+-\s+(?P<description>.+)$")
DEFAULT_DATA_FOLDER = Path(__file__).resolve().parents[1] / "Data"

logger = get_logger(__name__)


def _merge_text(values: Iterable[object]) -> str:
    text = " ".join(str(value).strip() for value in values if pd.notna(value) and str(value).strip())
    return re.sub(r"\s+", " ", text).strip()


def _row_text(row: pd.Series) -> str:
    return _merge_text(row.tolist())


def _split_money_from_text(text: str) -> tuple[str, dict[str, str | None]]:
    money_match = MONEY_AT_END_PATTERN.match(text)
    if not money_match:
        return text, {"debit": None, "credit": None, "balance": None}

    return (
        money_match.group("prefix").strip(),
        {
            "debit": money_match.group("debit"),
            "credit": money_match.group("credit"),
            "balance": money_match.group("balance"),
        },
    )


def _compose_activity_text(parts: list[str], money_values: dict[str, str | None]) -> str:
    text = _merge_text(parts)
    money = [money_values.get("debit"), money_values.get("credit"), money_values.get("balance")]
    if all(money):
        text = _merge_text([text, *money])
    return text


def trim_activity_table(df: pd.DataFrame) -> pd.DataFrame:
    """Return only current-period activity rows from a raw extracted table."""
    if df.empty:
        return df.copy()

    rows = df.reset_index(drop=True)
    start_index: int | None = None
    stop_index = len(rows)

    for index, row in rows.iterrows():
        text = _row_text(row)
        if start_index is None and DATE_CODE_PATTERN.match(text):
            start_index = index
        if start_index is not None and FUTURE_SETTLEMENT_HEADING in text:
            stop_index = index
            break

    if start_index is None:
        return rows.iloc[0:0].copy()

    return rows.iloc[start_index:stop_index].reset_index(drop=True)


def _normalize_raw_activity_table(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    data.columns = range(len(data.columns))
    return trim_activity_table(data)


def merge_wrapped_activity_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Merge continuation rows into the preceding dated activity row."""
    data = _normalize_raw_activity_table(df)
    if data.empty:
        return data

    groups: list[str] = []
    current: list[str] = []
    current_money = {"debit": None, "credit": None, "balance": None}

    for _, row in data.iterrows():
        text = _row_text(row)
        if not text:
            continue
        if DATE_CODE_PATTERN.match(text):
            if current:
                groups.append(_compose_activity_text(current, current_money))
            prefix, current_money = _split_money_from_text(text)
            current = [prefix]
            continue
        if current:
            current.append(text)

    if current:
        groups.append(_compose_activity_text(current, current_money))

    return pd.DataFrame({"raw_text": groups})


def parse_activity_rows(activity_rows: pd.DataFrame) -> pd.DataFrame:
    """Parse merged activity text while retaining unsupported statement codes."""
    if activity_rows.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    records: list[dict[str, object]] = []

    for raw_text in activity_rows["raw_text"].astype(str):
        text_without_money, money_values = _split_money_from_text(raw_text)
        date_code = DATE_CODE_PATTERN.match(text_without_money)
        details = text_without_money[date_code.end() :].strip() if date_code else text_without_money.strip()

        ticker_id = None
        description = details
        ticker_match = re.match(r"(?P<ticker>[A-Z0-9.]+)\s+-\s+(?P<description>.*)$", details)
        if ticker_match:
            ticker_id = ticker_match.group("ticker")
            description = ticker_match.group("description")

        quantity = None
        quantity_patterns = [
            r"(?:Bought|Sold)\s+(?P<quantity>\d+(?:\.\d+)?)\s+shares",
            r"(?P<quantity>\d+(?:\.\d+)?)\s+Shares?\s+on\s+loan",
            r"Loan\s+of\s+(?P<quantity>\d+(?:\.\d+)?)\s+shares\s+terminated",
        ]
        for quantity_pattern in quantity_patterns:
            quantity_match = re.search(quantity_pattern, details, flags=re.IGNORECASE)
            if quantity_match:
                quantity = quantity_match.group("quantity")
                break

        exec_match = re.search(r"\(executed\s+at\s+(?P<execDate>\d{4}-\d{2}-\d{2})\)", details, flags=re.IGNORECASE)
        record_date_match = re.search(r"record\s+date\s+of\s+(?P<record_date>\d{4}-\d{2}-\d{2})", details, flags=re.IGNORECASE)
        fx_match = re.search(r"FX\s+Rate:\s+(?P<fx_rate>\d+(?:\.\d+)?)", details, flags=re.IGNORECASE)
        statement_code = date_code.group("code").upper() if date_code else None

        record = {
            "date": date_code.group("date") if date_code else None,
            "transaction": statement_code,
            "ticker_id": ticker_id,
            "quantity": quantity,
            "execDate": (exec_match.group("execDate") if exec_match else None)
            or (record_date_match.group("record_date") if record_date_match else None),
            "fx_rate": fx_match.group("fx_rate") if fx_match else None,
            "statement_code": statement_code,
            "description": re.sub(r"\s+", " ", description).strip(),
        }

        record.update(money_values)
        records.append(record)

    parsed_df = pd.DataFrame(records)
    for column in OUTPUT_COLUMNS:
        if column not in parsed_df.columns:
            parsed_df[column] = None

    return parsed_df[OUTPUT_COLUMNS]


def transformations(df: pd.DataFrame) -> pd.DataFrame:
    return parse_activity_rows(merge_wrapped_activity_rows(df))


def clean_transactions(data: pd.DataFrame) -> pd.DataFrame:
    """Keep extracted current-period rows and preserve upload-facing columns."""
    if data.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    cleaned = data.copy()
    for column in OUTPUT_COLUMNS:
        if column not in cleaned.columns:
            cleaned[column] = None

    missing_exec_date = cleaned["execDate"].isna() | cleaned["execDate"].astype("string").str.strip().eq("")
    dividend_rows = cleaned["transaction"].astype("string").str.upper().eq("DIV")
    has_date = ~(cleaned["date"].isna() | cleaned["date"].astype("string").str.strip().eq(""))
    fill_exec_date = dividend_rows & missing_exec_date & has_date
    if fill_exec_date.any():
        cleaned.loc[fill_exec_date, "execDate"] = cleaned.loc[fill_exec_date, "date"]
        logger.info("Filled missing dividend execDate values from date | rows=%d", int(fill_exec_date.sum()))

    missing_code = cleaned["statement_code"].isna() | cleaned["statement_code"].astype("string").str.strip().eq("")
    if missing_code.any():
        logger.warning("Keeping %d row(s) without parsed statement_code", int(missing_code.sum()))

    return cleaned[OUTPUT_COLUMNS].reset_index(drop=True)


def _activity_lines_from_text(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    activity_lines: list[str] = []
    in_activity = False

    for line in lines:
        if ACTIVITY_HEADING in line:
            in_activity = True
            continue
        if not in_activity:
            continue
        if FUTURE_SETTLEMENT_HEADING in line:
            break
        if line.startswith("Date Transaction Description"):
            continue
        activity_lines.append(line)

    return activity_lines


def activity_lines_to_rows(lines: list[str]) -> pd.DataFrame:
    if not lines:
        return pd.DataFrame(columns=["raw_text"])

    rows: list[str] = []
    current: list[str] = []
    current_money = {"debit": None, "credit": None, "balance": None}
    for line in lines:
        if DATE_CODE_PATTERN.match(line):
            if current:
                rows.append(_compose_activity_text(current, current_money))
            prefix, current_money = _split_money_from_text(line)
            current = [prefix]
            continue
        if current:
            current.append(line)

    if current:
        rows.append(_compose_activity_text(current, current_money))

    return pd.DataFrame({"raw_text": rows})


def find_activity_pages(file: Path, search: str = ACTIVITY_HEADING) -> list[str]:
    import pdfplumber

    matched_pages = []
    with pdfplumber.open(file) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if search in text:
                matched_pages.append(str(page_number))
    return matched_pages


def find_glossary_pages(file: Path, search: str = GLOSSARY_HEADING) -> list[str]:
    import pdfplumber

    matched_pages = []
    with pdfplumber.open(file) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if search in text:
                matched_pages.append(str(page_number))
    return matched_pages


def _extract_activity_with_pdfplumber(file: Path, pages: list[str]) -> pd.DataFrame:
    import pdfplumber

    rows: list[pd.DataFrame] = []
    page_numbers = {int(page) for page in pages}

    with pdfplumber.open(file) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            if page_number not in page_numbers:
                continue
            text = page.extract_text() or ""
            page_rows = activity_lines_to_rows(_activity_lines_from_text(text))
            if not page_rows.empty:
                page_rows["source_page"] = str(page_number)
                rows.append(page_rows)

    if not rows:
        return pd.DataFrame(columns=["raw_text"])

    return pd.concat(rows, ignore_index=True)


def extract_statement_pdf(file: Path) -> pd.DataFrame:
    file = Path(file)
    pages = find_activity_pages(file)
    if not pages:
        logger.info("No activity pages found in %s", file.name)
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    logger.info("Extracting current-period activity from %s pages=%s", file.name, ",".join(pages))
    activity_rows = _extract_activity_with_pdfplumber(file, pages)
    parsed = parse_activity_rows(activity_rows)
    final_df = clean_transactions(parsed)
    logger.info("Current-period extraction complete | file=%s | rows=%d", file.name, len(final_df))
    return final_df


def _iter_glossary_cells(df: pd.DataFrame) -> Iterable[str]:
    for column in df.columns:
        current = ""
        for value in df[column].dropna().astype(str):
            cell = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
            if not cell or cell == GLOSSARY_HEADING:
                continue
            if GLOSSARY_ENTRY_PATTERN.match(cell):
                if current:
                    yield current
                current = cell
                continue
            if current and not cell.startswith("Wealthsimple"):
                current = f"{current} {cell}"
        if current:
            yield current


def extract_glossary_from_tables(tables: Iterable[object]) -> pd.DataFrame:
    records: list[dict[str, str]] = []
    for table in tables:
        df = getattr(table, "df", table)
        for cell in _iter_glossary_cells(df):
            match = GLOSSARY_ENTRY_PATTERN.match(cell)
            if not match:
                continue
            records.append(
                {
                    "code": match.group("code").strip(),
                    "description": re.sub(r"\s+", " ", match.group("description")).strip(),
                }
            )

    glossary = pd.DataFrame(records, columns=["code", "description"])
    if glossary.empty:
        return glossary
    return glossary.drop_duplicates(subset=["code"], keep="first").sort_values("code").reset_index(drop=True)


def extract_statement_glossary_pdf(file: Path) -> pd.DataFrame:
    import camelot

    file = Path(file)
    pages = find_glossary_pages(file)
    if not pages:
        logger.info("No statement-code glossary pages found in %s", file.name)
        return pd.DataFrame(columns=["code", "description"])

    tables = []
    for page in pages:
        logger.info("Reading glossary page %s from %s", page, file.name)
        tables.extend(camelot.read_pdf(str(file), pages=page, flavor="stream"))

    glossary = extract_glossary_from_tables(tables)
    logger.info("Glossary extraction complete | file=%s | rows=%d", file.name, len(glossary))
    return glossary


def extract_folder(folder: Path | str, include_glossary: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    folder = Path(folder)
    files = sorted(folder.glob("*.pdf"))

    if not files:
        logger.info("No PDF files found in %s", folder)
        return pd.DataFrame(columns=OUTPUT_COLUMNS), pd.DataFrame(columns=["code", "description"])

    transaction_frames = [extract_statement_pdf(file) for file in files]
    valid_transactions = [frame for frame in transaction_frames if not frame.empty]
    transactions = pd.concat(valid_transactions, ignore_index=True) if valid_transactions else pd.DataFrame(columns=OUTPUT_COLUMNS)

    if not include_glossary:
        return transactions.reset_index(drop=True), pd.DataFrame(columns=["code", "description"])

    glossary_frames = [extract_statement_glossary_pdf(file) for file in files]
    valid_glossaries = [frame for frame in glossary_frames if not frame.empty]
    glossary = pd.concat(valid_glossaries, ignore_index=True) if valid_glossaries else pd.DataFrame(columns=["code", "description"])
    if not glossary.empty:
        glossary = glossary.drop_duplicates(subset=["code"], keep="first").sort_values("code")

    return transactions.reset_index(drop=True), glossary.reset_index(drop=True)


def camelot_extraction_pipeline(file: Path) -> pd.DataFrame:
    return extract_statement_pdf(file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Wealthsimple PDF statement activity.")
    parser.add_argument("--folder", type=Path, default=DEFAULT_DATA_FOLDER)
    parser.add_argument("--include-glossary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    transactions, glossary = extract_folder(args.folder, include_glossary=args.include_glossary)

    print("Transactions:")
    print(transactions.to_string(index=False))
    if args.include_glossary:
        print("\nStatement code glossary:")
        print(glossary.to_string(index=False))


if __name__ == "__main__":
    main()
