from __future__ import annotations

import argparse
import os
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from imap_tools import AND, OR, MailBox

from config import (
    EMAIL_OUTPUT_COLUMNS,
    EXPORT_FOLDER,
    INTERAC_SENDER,
    INTERAC_SUBJECT_PATTERN,
    WEALTHSIMPLE_DATE_PATTERNS,
    WEALTHSIMPLE_SENDERS,
)
from system_logger import get_logger


"""
Load environment settings once so the runtime can read credentials
and the temporary email start date consistently.
"""
load_dotenv()

GMAIL = os.getenv("GMAIL_USER")
PASS = os.getenv("GMAIL_PASS")
DEFAULT_START_DATE = os.getenv("START_DATE")
DEFAULT_EXPORT_FOLDER = EXPORT_FOLDER

"""
Define the combined output contract that both email sources normalize into.
"""
OUTPUT_COLUMNS = EMAIL_OUTPUT_COLUMNS

logger = get_logger(__name__)


"""
Normalize the temporary configured start date until the future database
checkpoint flow is implemented.
"""
def normalize_start_date(value: str | date | datetime | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


"""
Collapse email text into a predictable line structure before regex parsing.
"""
def normalize_email_text(email: str) -> str:
    return "\n".join(line.strip() for line in email.splitlines() if line.strip())


"""
Apply a first-match regex helper so the source-specific parsers stay compact.
"""
def extract_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


"""
Read Wealthsimple order dates from the different labels used across order
email variants such as market buys, limit buys, and filled orders.
"""
def extract_wealthsimple_date(text: str) -> date | None:
    for pattern in WEALTHSIMPLE_DATE_PATTERNS:
        value = extract_first(pattern, text)
        if not value:
            continue
        parsed = pd.to_datetime(value, errors="coerce")
        if not pd.isna(parsed):
            return parsed.date()
    return None


"""
Resolve the final output date using the agreed precedence: first the date
found inside the email content, then the IMAP received date, and only then
fall back to a blank value with a warning.
"""
def resolve_email_date(parsed_date: date | None, received_date: date | None, source_name: str) -> date | str:
    if parsed_date is not None:
        return parsed_date
    if received_date is not None:
        logger.debug("Using received-date fallback for %s email", source_name)
        return received_date
    logger.warning("No date could be resolved for %s email", source_name)
    return ""


"""
Classify Wealthsimple subjects into the supported transaction groups.
"""
def wealthsimple_subject_type(subject: str) -> str | None:
    lowered = (subject or "").lower()
    if "filled" in lowered:
        return "filled"
    if "dividend" in lowered:
        return "dividend"
    return None


"""
Keep sender checks explicit so mailbox fetches are validated before parsing.
"""
def message_from_wealthsimple(msg: object) -> bool:
    sender = str(getattr(msg, "from_", "") or "").lower()
    return any(expected in sender for expected in WEALTHSIMPLE_SENDERS)


def message_from_interac(msg: object) -> bool:
    sender = str(getattr(msg, "from_", "") or "").lower()
    return INTERAC_SENDER.lower() in sender


def message_subject_matches_interac(msg: object) -> bool:
    subject = str(getattr(msg, "subject", "") or "").strip()
    return bool(INTERAC_SUBJECT_PATTERN.search(subject))


"""
Parse Wealthsimple order and dividend emails without changing the fields
already extracted by the reference implementation.
"""
def parse_wealthsimple_email(email: str, subject: str, received_date: date | None = None) -> pd.DataFrame | None:
    text = normalize_email_text(email).replace("*", "")

    try:
        parsed_date = extract_wealthsimple_date(text)
        transaction = "Dividend" if wealthsimple_subject_type(subject) == "dividend" else extract_first(r"Type:\s*(.+)", text)
        if not transaction:
            logger.warning("Parsed Wealthsimple email without transaction type | subject=%s", subject)
        currency_match = re.search(r"(?:Average price|Total cost|Amount):\s*(US\$|CA\$)", text, flags=re.IGNORECASE)
        price_currency = (
            "USD" if currency_match and currency_match.group(1).upper() == "US$"
            else "CAD" if currency_match else ""
        )
        row = {
            "account": extract_first(r"Account:\s*(.+)", text) or "",
            "transaction": transaction or "",
            "ticker_id": "",
            "ticker": extract_first(r"Symbol:\s*(.+)", text) or "",
            "quantity": extract_first(r"Shares:\s*(.+)", text) or "",
            "avg_price": extract_first(r"Average price:\s*(?:US\$|\$)?(.+)", text) or "",
            "total_cost": extract_first(r"Total cost:\s*(?:US\$|\$)?(.+)", text) or "",
            "debit": extract_first(r"Amount:\s*(?:CA\$|US\$|\$)?(\d+(?:\.\d{1,2})?)", text) or "",
            "date": resolve_email_date(parsed_date, received_date, "Wealthsimple"),
            "price_currency": price_currency,
        }
        logger.debug(
            "Parsed Wealthsimple email | transaction=%s | ticker=%s | quantity=%s | date=%s",
            row["transaction"],
            row["ticker"],
            row["quantity"],
            row["date"],
        )
        return pd.DataFrame([row], columns=OUTPUT_COLUMNS)
    except Exception:
        logger.exception("Failed to parse Wealthsimple email")
        return None


"""
Extract Interac deposit amounts carefully so date text does not get mistaken
for money values.
"""
def extract_interac_money(text: str) -> str:
    patterns = [
        r"(?im)^(?:Amount|Deposit amount|Deposited amount|Transfer amount)\s*[:\-]?\s*(?:CA\$|CAD|\$)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b",
        r"(?im)^(?:Amount|Deposit|Deposited|Sent|Received|Transfer)[^\n$]*?(?:CA\$|CAD|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\b",
        r"(?:CA\$|CAD|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).replace(",", "")
    return ""


"""
Extract Interac dates from several known formats and fall back to the email
received date when the body does not contain one.
"""
def extract_interac_date(text: str, fallback_date: date | None = None) -> date | None:
    patterns = [
        r"(?:Date|Deposited on|Received on|Sent on|Transfer date)\D{0,40}([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"(?:Date|Deposited on|Received on|Sent on|Transfer date)\D{0,40}(\d{4}-\d{1,2}-\d{1,2})",
        r"(?:Date|Deposited on|Received on|Sent on|Transfer date)\D{0,40}(\d{1,2}/\d{1,2}/\d{4})",
        r"([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"(\d{4}-\d{1,2}-\d{1,2})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = pd.to_datetime(match.group(1), errors="coerce")
        if not pd.isna(parsed):
            return parsed.date()
    return fallback_date


"""
Normalize Interac deposit emails into the same output contract as the
Wealthsimple email rows.
"""
def parse_interac_email(email: str, subject: str = "", received_date: date | None = None) -> pd.DataFrame | None:
    text = normalize_email_text("\n".join(part for part in (subject, email) if part))

    try:
        row = {
            "account": "TFSA",
            "transaction": "Deposit",
            "ticker_id": 0,
            "ticker": "EMAIL",
            "quantity": "",
            "avg_price": "",
            "total_cost": "",
            "debit": extract_interac_money(text),
            "date": resolve_email_date(extract_interac_date(text), received_date, "Interac"),
            "price_currency": "",
        }
        logger.debug("Parsed Interac email | debit=%s | date=%s", row["debit"], row["date"])
        return pd.DataFrame([row], columns=OUTPUT_COLUMNS)
    except Exception:
        logger.exception("Failed to parse Interac email")
        return None


"""
Collect matching Wealthsimple emails, collect matching Interac emails, and
return one normalized dataframe for the whole run.
"""
def fetch_email_transactions(
    mailbox: MailBox | None = None,
    start_date: date | None = None,
) -> pd.DataFrame:
    normalized_start = start_date if start_date is not None else normalize_start_date(DEFAULT_START_DATE)
    if normalized_start is None:
        logger.info("No email start date configured yet; reading all matching emails")
    else:
        logger.info("Resolved email start date to %s", normalized_start)

    own_mailbox = mailbox is None
    if own_mailbox:
        if not GMAIL or not PASS:
            raise RuntimeError("Missing GMAIL_USER or GMAIL_PASS environment variables.")
        mailbox = MailBox("imap.gmail.com")
        mailbox.login(GMAIL, PASS, "Inbox")

    rows: list[pd.DataFrame] = []
    wealthsimple_messages_seen = 0
    wealthsimple_rows_added = 0
    interac_messages_seen = 0
    interac_rows_added = 0

    try:
        wealthsimple_query_parts: list[object] = [OR(*(AND(from_=sender) for sender in WEALTHSIMPLE_SENDERS))]
        interac_query_parts: list[object] = [AND(from_=INTERAC_SENDER)]
        if normalized_start is not None:
            wealthsimple_query_parts.append(AND(date_gte=normalized_start))
            interac_query_parts.append(AND(date_gte=normalized_start))

        for msg in mailbox.fetch(AND(*wealthsimple_query_parts)):
            wealthsimple_messages_seen += 1
            if not message_from_wealthsimple(msg):
                continue
            subject = str(getattr(msg, "subject", "") or "")
            if wealthsimple_subject_type(subject) is None:
                continue
            received_date = msg.date.date() if getattr(msg, "date", None) else None
            parsed = parse_wealthsimple_email(getattr(msg, "text", "") or "", subject, received_date)
            if parsed is not None:
                rows.append(parsed)
                wealthsimple_rows_added += len(parsed)

        for msg in mailbox.fetch(AND(*interac_query_parts)):
            interac_messages_seen += 1
            if not message_from_interac(msg):
                continue
            if not message_subject_matches_interac(msg):
                continue
            received_date = msg.date.date() if getattr(msg, "date", None) else None
            parsed = parse_interac_email(getattr(msg, "text", "") or "", getattr(msg, "subject", "") or "", received_date)
            if parsed is not None:
                rows.append(parsed)
                interac_rows_added += len(parsed)
    finally:
        if own_mailbox:
            mailbox.logout()

    logger.info(
        "Email fetch summary | wealthsimple_messages=%d | wealthsimple_rows=%d | interac_messages=%d | interac_rows=%d",
        wealthsimple_messages_seen,
        wealthsimple_rows_added,
        interac_messages_seen,
        interac_rows_added,
    )

    if not rows:
        logger.info("Combined email extraction complete | rows=0")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    """
    Normalize the final dataframe once so print output and tests both use the
    same ordering and blank-value rules.
    """
    data = pd.concat(rows, ignore_index=True)
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.sort_values(["date"], kind="stable", na_position="last").reset_index(drop=True)
    data["date"] = data["date"].dt.date
    for column in OUTPUT_COLUMNS:
        if column not in data.columns:
            data[column] = ""
    data = data.fillna("")
    logger.info("Combined email extraction complete | rows=%d", len(data))
    return data[OUTPUT_COLUMNS]


"""
Write the current run's combined email data to a timestamped CSV when export
is explicitly requested.
"""
def export_run_csv(data: pd.DataFrame, export_folder: Path | str = DEFAULT_EXPORT_FOLDER) -> Path:
    export_folder = Path(export_folder)
    export_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = export_folder / f"email_transactions_{timestamp}.csv"
    data.to_csv(export_path, index=False)
    logger.info("Exported email transactions | file=%s | rows=%d", export_path, len(data))
    return export_path


"""
Accept an optional explicit start date while the permanent database-backed
checkpoint remains future work.
"""
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse email CLI arguments from a caller or the process command line."""
    parser = argparse.ArgumentParser(description="Extract Wealthsimple and Interac email transactions.")
    parser.add_argument("--date-from", type=str, default=DEFAULT_START_DATE)
    parser.add_argument("--export", action="store_true", help="Write this run's email data to exports/*.csv.")
    parser.add_argument("--export-folder", type=Path, default=DEFAULT_EXPORT_FOLDER)
    return parser.parse_args(argv)


"""
Print the full combined dataset at the end of each run for visibility.
"""
def main(argv: list[str] | None = None) -> int:
    """Run email extraction as a standalone or delegated CLI command."""
    try:
        args = parse_args(argv)
        logger.info(
            "Running email extractor CLI | date_from=%s | export=%s",
            args.date_from,
            args.export,
        )
        start_date = normalize_start_date(args.date_from)
        data = fetch_email_transactions(start_date=start_date)
        logger.info("Email extractor produced %d row(s) before print/export", len(data))

        if data.empty:
            print("No matching emails found.")
            return 0

        print("Email transactions:")
        print(data.to_string(index=False))
        if args.export:
            export_path = export_run_csv(data, args.export_folder)
            print(f"\nExported email transactions to {export_path}")
        return 0
    except Exception:
        logger.exception("Email extractor failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
