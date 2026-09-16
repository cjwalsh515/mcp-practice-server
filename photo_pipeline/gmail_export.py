"""Downloads pre-2015 photo attachments from Gmail into a folder pass1.py
can process alongside the iCloud/Takeout year-folders.

Why this needs its own Google Cloud OAuth setup rather than a lightweight
connector: searching/listing messages works through a read-only Gmail
connection, but pulling the actual attachment bytes requires calling
``users.messages.attachments.get`` directly, which needs an OAuth client
of your own. See docs/SETUP.md for how to create one.

    python -m photo_pipeline.gmail_export \\
        --credentials client_secret.json \\
        --output ~/photos/pre-2015

The default query is the one already validated against the real inbox
(see the project brief) — override individual pieces with --before-date
or --senders rather than hand-editing --query unless you need to.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from . import heic_support  # noqa: F401
from .exif_utils import is_plausible_timestamp

logger = logging.getLogger("photo_pipeline.gmail_export")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

DEFAULT_SENDERS = [
    "cjwalsh515@gmail.com",
    "kemplaurajean@gmail.com",
    "ridgewalshes@comcast.net",
    "Maryokemp@gmail.com",
    "BCoughlan@stamfordct.gov",
]
DEFAULT_BEFORE_DATE = "2015/01/01"
DEFAULT_EXTENSIONS = ["jpg", "jpeg", "png", "heic", "gif"]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]+")

IMAGE_MIME_PREFIXES = ("image/",)


def build_query(
    senders: list[str] = DEFAULT_SENDERS,
    before_date: str = DEFAULT_BEFORE_DATE,
    extensions: list[str] = DEFAULT_EXTENSIONS,
) -> str:
    from_clause = " OR ".join(f"from:{s}" for s in senders)
    filename_clause = " OR ".join(f"filename:{ext}" for ext in extensions)
    return f"({from_clause}) has:attachment before:{before_date} ({filename_clause})"


def slugify_sender(email_address: str, sender_names: Optional[dict[str, str]] = None) -> str:
    email_address = email_address.strip().lower()
    if sender_names:
        for known_email, name in sender_names.items():
            if known_email.strip().lower() == email_address:
                return _SANITIZE_RE.sub("-", name.strip().lower()).strip("-")
    local_part = email_address.split("@")[0]
    return _SANITIZE_RE.sub("-", local_part).strip("-") or "unknown"


def extract_email_address(from_header: str) -> str:
    match = _EMAIL_RE.search(from_header or "")
    return match.group(0) if match else "unknown@unknown"


def _load_credentials(credentials_path: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds


def _get_header(payload: dict, name: str) -> Optional[str]:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def _walk_parts(payload: dict):
    """Yields every MIME part in a message payload, recursing into multiparts."""
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        stack.extend(part.get("parts", []))


def _read_exif_datetime_from_bytes(data: bytes) -> Optional[datetime]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            if not exif:
                return None
            from PIL import ExifTags

            tag_values = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            try:
                sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            except Exception:  # noqa: BLE001
                sub_ifd = None
            if sub_ifd:
                tag_values.update({ExifTags.TAGS.get(k, k): v for k, v in sub_ifd.items()})

            for tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                raw = tag_values.get(tag)
                if raw:
                    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            dt = datetime.strptime(str(raw).strip(), fmt)
                            if is_plausible_timestamp(dt, earliest_year=1990):
                                return dt
                        except ValueError:
                            continue
    except Exception:  # noqa: BLE001 - corrupt/partial image bytes are common in old email attachments
        return None
    return None


@dataclass
class DownloadLog:
    path: Path
    downloaded_ids: set = field(default_factory=set)
    skipped: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "DownloadLog":
        log = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text())
            log.downloaded_ids = set(data.get("downloaded_ids", []))
            log.skipped = data.get("skipped", [])
        return log

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"downloaded_ids": sorted(self.downloaded_ids), "skipped": self.skipped},
                indent=2,
            )
        )

    def mark_downloaded(self, attachment_key: str) -> None:
        self.downloaded_ids.add(attachment_key)

    def mark_skipped(self, message_id: str, filename: str, reason: str) -> None:
        self.skipped.append({"message_id": message_id, "filename": filename, "reason": reason})


def run_export(
    *,
    credentials_path: Path,
    output_dir: Path,
    query: str,
    token_path: Optional[Path] = None,
    max_messages: Optional[int] = None,
    sender_names: Optional[dict[str, str]] = None,
    dry_run: bool = False,
) -> DownloadLog:
    output_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_path or (output_dir / "gmail_token.json")
    log = DownloadLog.load(output_dir / "gmail_export_log.json")

    from googleapiclient.discovery import build

    creds = _load_credentials(credentials_path, token_path)
    service = build("gmail", "v1", credentials=creds)

    message_ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token, maxResults=100)
            .execute()
        )
        message_ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or (max_messages and len(message_ids) >= max_messages):
            break

    if max_messages:
        message_ids = message_ids[:max_messages]

    logger.info("Query matched %d candidate messages", len(message_ids))

    for message_id in message_ids:
        message = (
            service.users().messages().get(userId="me", id=message_id, format="full").execute()
        )
        payload = message.get("payload", {})
        from_header = _get_header(payload, "From") or ""
        date_header = _get_header(payload, "Date")
        sender_email = extract_email_address(from_header)
        sender_slug = slugify_sender(sender_email, sender_names)

        try:
            email_dt = parsedate_to_datetime(date_header) if date_header else None
            if email_dt and email_dt.tzinfo:
                email_dt = email_dt.replace(tzinfo=None)
        except (TypeError, ValueError):
            email_dt = None
        if email_dt is None:
            email_dt = datetime.fromtimestamp(int(message["internalDate"]) / 1000)

        for part in _walk_parts(payload):
            filename = part.get("filename") or ""
            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            if not filename or not attachment_id:
                continue
            if not mime_type.startswith(IMAGE_MIME_PREFIXES):
                log.mark_skipped(message_id, filename, f"non-image mimeType: {mime_type}")
                continue

            attachment_key = f"{message_id}:{attachment_id}"
            if attachment_key in log.downloaded_ids:
                continue

            if dry_run:
                logger.info("[dry-run] would download %s from %s (%s)", filename, sender_email, email_dt.date())
                continue

            attachment = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            raw = attachment.get("data", "")
            try:
                data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            except (ValueError, TypeError) as exc:
                log.mark_skipped(message_id, filename, f"base64 decode failed: {exc}")
                continue

            if not data:
                log.mark_skipped(message_id, filename, "zero-byte attachment")
                continue

            exif_dt = _read_exif_datetime_from_bytes(data)
            best_dt = exif_dt if exif_dt is not None else email_dt
            date_str = best_dt.strftime("%Y-%m-%d")

            safe_original = _SANITIZE_RE.sub("_", Path(filename).name)
            out_name = f"{date_str}_from-{sender_slug}_{safe_original}"
            out_path = output_dir / out_name
            n = 1
            while out_path.exists():
                out_path = output_dir / f"{Path(out_name).stem}__{n}{Path(out_name).suffix}"
                n += 1

            out_path.write_bytes(data)
            log.mark_downloaded(attachment_key)
            logger.info("Saved %s", out_path.name)

        log.save()

    log.save()
    logger.info(
        "Done. %d attachments downloaded so far (cumulative), %d skipped this run's log.",
        len(log.downloaded_ids),
        len(log.skipped),
    )
    return log


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--credentials", required=True, type=Path, help="OAuth client secret JSON from Google Cloud Console")
    parser.add_argument("--token", type=Path, default=None, help="Where to cache the OAuth token (default: <output>/gmail_token.json)")
    parser.add_argument("--output", required=True, type=Path, help="Folder to write downloaded photos into (e.g. pre-2015/)")
    parser.add_argument("--query", default=None, help="Full Gmail search query override (default: built from --senders/--before-date/--extensions)")
    parser.add_argument("--senders", default=",".join(DEFAULT_SENDERS), help="Comma-separated sender addresses")
    parser.add_argument("--before-date", default=DEFAULT_BEFORE_DATE, help="Gmail-format date (YYYY/MM/DD), exclusive upper bound")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTENSIONS), help="Comma-separated file extensions to match")
    parser.add_argument("--sender-names", type=Path, default=None, help="Optional JSON file mapping email -> friendly name, e.g. {\"kemplaurajean@gmail.com\": \"mom\"}")
    parser.add_argument("--max-messages", type=int, default=None, help="Cap on number of matching messages to process (for testing the query)")
    parser.add_argument("--dry-run", action="store_true", help="List what would be downloaded without saving files")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    query = args.query or build_query(
        senders=[s.strip() for s in args.senders.split(",") if s.strip()],
        before_date=args.before_date,
        extensions=[e.strip() for e in args.extensions.split(",") if e.strip()],
    )
    logger.info("Using query: %s", query)

    sender_names = json.loads(args.sender_names.read_text()) if args.sender_names else None

    run_export(
        credentials_path=args.credentials,
        output_dir=args.output,
        query=query,
        token_path=args.token,
        max_messages=args.max_messages,
        sender_names=sender_names,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
