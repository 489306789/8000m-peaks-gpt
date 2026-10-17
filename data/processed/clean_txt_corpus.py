
from pathlib import Path
import csv
import hashlib
import re
import unicodedata


# ============================================================
# 1. Project configuration
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "txt_cleaned"
REPORT_DIR = PROJECT_ROOT / "data" / "processed" / "reports"
REPORT_PATH = REPORT_DIR / "txt_cleaning_report.csv"


# ============================================================
# 2. Utility functions
# ============================================================

def sha256_bytes(data: bytes) -> str:
    """Calculate the SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def decode_text(raw: bytes) -> tuple[str, str, list[str]]:
    """
    Decode text using a conservative encoding strategy.

    UTF-8 is preferred. Legacy encodings are used only when
    explicitly needed and are flagged for manual review.
    """
    warnings = []

    # UTF-8 with or without BOM
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig/utf-8", warnings
    except UnicodeDecodeError:
        pass

    # Common legacy encodings.
    # Successful decoding does not guarantee correct text.
    for encoding in ("cp1252", "gb18030"):
        try:
            decoded = raw.decode(encoding)
            warnings.append(f"legacy_encoding_review:{encoding}")
            return decoded, encoding, warnings
        except UnicodeDecodeError:
            continue

    # Never silently discard undecodable bytes.
    decoded = raw.decode("utf-8", errors="replace")
    warnings.append("decode_with_replacement")
    return decoded, "utf-8-replace", warnings


# ============================================================
# 3. Text cleaning
# ============================================================

def clean_text(text: str) -> tuple[str, list[str]]:
    """
    Apply conservative text normalization.

    This function does not remove substantive paragraphs,
    references, technical details, or safety warnings.
    """
    warnings = []

    if "\ufffd" in text:
        warnings.append("contains_replacement_character")

    if "\x00" in text:
        warnings.append("contains_null_bytes")

    # Normalize Unicode representation.
    text = unicodedata.normalize("NFC", text)

    # Standardize line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Replace non-breaking spaces.
    text = text.replace("\u00a0", " ")

    # Remove control characters except tabs and newlines.
    cleaned_chars = []
    removed_controls = 0

    for char in text:
        if char in ("\n", "\t"):
            cleaned_chars.append(char)
        elif unicodedata.category(char) == "Cc":
            removed_controls += 1
        else:
            cleaned_chars.append(char)

    text = "".join(cleaned_chars)

    if removed_controls:
        warnings.append(f"removed_control_chars:{removed_controls}")

    # Remove trailing whitespace from each line.
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)

    # Collapse 3 or more consecutive newline characters.
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove whitespace at the beginning and end.
    text = text.strip()

    # Basic quality checks.
    if not text:
        warnings.append("empty_after_cleaning")
    elif len(text) < 200:
        warnings.append("very_short_text")

    if "\ufffd" in text:
        warnings.append("review_possible_encoding_problem")

    return text, warnings


# ============================================================
# 4. File discovery and processing
# ============================================================

def discover_txt_files() -> list[Path]:
    """Find .txt files recursively under the raw directory."""
    return sorted(
        path
        for path in RAW_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() == ".txt"
    )


def process_file(source_path: Path) -> dict:
    """Process one TXT file and return its report record."""
    relative_path = source_path.relative_to(RAW_DIR)
    output_path = OUTPUT_DIR / relative_path

    row = {
        "source_path": str(relative_path),
        "output_path": "",
        "status": "",
        "encoding_used": "",
        "original_bytes": "",
        "original_chars": "",
        "cleaned_chars": "",
        "original_sha256": "",
        "cleaned_sha256": "",
        "warnings": "",
        "error": "",
    }

    try:
        raw = source_path.read_bytes()
        original_hash = sha256_bytes(raw)

        decoded_text, encoding, decode_warnings = decode_text(raw)
        cleaned_text, clean_warnings = clean_text(decoded_text)

        warnings = decode_warnings + clean_warnings

        cleaned_bytes = cleaned_text.encode("utf-8")
        cleaned_hash = sha256_bytes(cleaned_bytes)

        row.update({
            "encoding_used": encoding,
            "original_bytes": len(raw),
            "original_chars": len(decoded_text),
            "cleaned_chars": len(cleaned_text),
            "original_sha256": original_hash,
            "cleaned_sha256": cleaned_hash,
            "warnings": ";".join(warnings),
        })

        if not cleaned_text:
            row["status"] = "review_empty"
            return row

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(cleaned_bytes)

        row["output_path"] = str(output_path.relative_to(PROJECT_ROOT))
        row["status"] = (
            "review"
            if warnings
            else "cleaned"
        )

    except Exception as exc:
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"

    return row


# ============================================================
# 5. Duplicate detection and report generation
# ============================================================

def mark_exact_duplicates(rows: list[dict]) -> None:
    """
    Mark identical cleaned text without deleting any files.
    """
    hash_groups = {}

    for row in rows:
        digest = row["cleaned_sha256"]

        if digest and row["status"] in ("cleaned", "review"):
            hash_groups.setdefault(digest, []).append(row)

    for group in hash_groups.values():
        if len(group) > 1:
            for row in group:
                existing = row["warnings"]
                duplicate_warning = "duplicate_cleaned_content"

                row["warnings"] = (
                    f"{existing};{duplicate_warning}"
                    if existing
                    else duplicate_warning
                )

                # Preserve the fact that manual review is needed.
                if row["status"] == "cleaned":
                    row["status"] = "review"


def write_report(rows: list[dict]) -> None:
    """Write a CSV report using UTF-8 with BOM for Excel compatibility."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "source_path",
        "output_path",
        "status",
        "encoding_used",
        "original_bytes",
        "original_chars",
        "cleaned_chars",
        "original_sha256",
        "cleaned_sha256",
        "warnings",
        "error",
    ]

    with REPORT_PATH.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# 6. Main entry point
# ============================================================

def main() -> None:
    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"Raw directory not found: {RAW_DIR}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source_files = discover_txt_files()
    rows = []

    print(f"TXT files discovered: {len(source_files)}")

    for index, source_path in enumerate(source_files, start=1):
        row = process_file(source_path)
        rows.append(row)

        print(
            f"[{index}/{len(source_files)}] "
            f"{row['status']}: {row['source_path']}"
        )

    mark_exact_duplicates(rows)
    write_report(rows)

    status_counts = {}

    for row in rows:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    print("\nTXT cleaning finished.")
    print(f"Summary: {status_counts}")
    print(f"Cleaned output: {OUTPUT_DIR}")
    print(f"Report: {REPORT_PATH}")
    print("Original files were not intentionally modified.")


if __name__ == "__main__":
    main()
