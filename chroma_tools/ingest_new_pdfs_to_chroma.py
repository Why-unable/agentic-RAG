"""Incrementally ingest new PDFs from data/pdffiles into a running Chroma server.

Usage example:
    .venv/bin/python chroma_tools/ingest_new_pdfs_to_chroma.py

This script connects to a local Chroma server (client-server mode), scans
`data/pdffiles`, and only ingests PDFs that are not already present in the
target collection based on the `source_file` metadata field.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Iterable, List

import chromadb


DEFAULT_COLLECTION_NAME = "pdf_documents"
DEFAULT_PDF_DIR = Path("data/pdffiles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest only new PDF files from data/pdffiles into Chroma."
    )
    parser.add_argument("--host", default="localhost",
                        help="Chroma server host")
    parser.add_argument("--port", type=int, default=8000,
                        help="Chroma server port")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Target Chroma collection name",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=DEFAULT_PDF_DIR,
        help="Directory that contains PDFs",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=900,
        help="Character length for each text chunk",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=120,
        help="Character overlap between adjacent chunks",
    )
    parser.add_argument(
        "--min-chunk-chars",
        type=int,
        default=80,
        help="Minimum character length required for a chunk to be indexed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print which files would be ingested, do not write to Chroma",
    )
    parser.add_argument(
        "--enable-ocr-fallback",
        action="store_true",
        help="Use OCR when direct PDF text extraction is empty",
    )
    parser.add_argument(
        "--ocr-lang",
        default="chi_sim+eng",
        help="Tesseract OCR language(s), e.g. chi_sim+eng",
    )
    parser.add_argument(
        "--delete-missing",
        action="store_true",
        help="Delete indexed PDF chunks whose source files no longer exist on disk",
    )
    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help=(
            "Delete and recreate the target collection before ingesting, "
            "then re-ingest all PDFs"
        ),
    )
    parser.add_argument(
        "--upsert-retries",
        type=int,
        default=3,
        help="Retry times for Chroma upsert when transient internal errors occur",
    )
    return parser.parse_args()


def iter_chunks(text: str, chunk_size: int, chunk_overlap: int) -> Iterable[str]:
    text = text.strip()
    if not text:
        return
    step = max(1, chunk_size - max(0, chunk_overlap))
    for start in range(0, len(text), step):
        chunk = text[start: start + chunk_size].strip()
        if chunk:
            yield chunk


def normalize_extracted_text(text: str) -> str:
    """Normalize extracted PDF text before chunking.

    Handles common OCR/PDF artifacts, especially Chinese text where every
    character is split by a newline.
    """
    if not text:
        return ""

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\u00a0", " ").replace("\u200b", "")

    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    if not lines:
        return ""

    # If most lines are single-char, join directly to remove vertical text noise.
    short_line_count = sum(1 for line in lines if len(line) <= 1)
    short_ratio = short_line_count / len(lines)
    if len(lines) >= 20 and short_ratio >= 0.6:
        return "".join(lines)

    # Default: collapse all whitespace/newlines into a single space.
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_source_path(path: Path) -> str:
    # Keep path style stable across runs for reliable dedup checks.
    return path.as_posix()


def is_file_already_ingested(collection, source_file: str) -> bool:
    existing = collection.get(
        where={"source_file": source_file},
        include=[],
        limit=1,
    )
    return len(existing.get("ids", [])) > 0


def build_file_key(source_file: str) -> str:
    return hashlib.sha1(source_file.encode("utf-8")).hexdigest()[:16]


def list_indexed_source_files(collection, batch_size: int = 1000) -> set[str]:
    source_files: set[str] = set()
    offset = 0

    while True:
        batch = collection.get(
            include=["metadatas"],
            limit=batch_size,
            offset=offset,
        )
        metadatas = batch.get("metadatas", [])
        if not metadatas:
            break

        for metadata in metadatas:
            if isinstance(metadata, dict):
                source_file = metadata.get("source_file")
                if isinstance(source_file, str) and source_file:
                    source_files.add(source_file)

        if len(metadatas) < batch_size:
            break
        offset += batch_size

    return source_files


def read_pdf_pages_with_pypdf(pdf_path: Path) -> List[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required. Install it with: .venv/bin/pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages: List[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        pages.append(normalize_extracted_text(text))
    return pages


def read_pdf_pages_with_ocr(pdf_path: Path, ocr_lang: str) -> List[str]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required for OCR fallback. Install it with: uv add pymupdf"
        ) from exc

    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "pytesseract and pillow are required for OCR fallback. Install with: uv add pytesseract"
        ) from exc

    doc = fitz.open(str(pdf_path))
    pages: List[str] = []

    for page in doc:
        # 2x zoom improves OCR quality for scanned pages.
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(image, lang=ocr_lang).strip()
        pages.append(normalize_extracted_text(text))

    doc.close()
    return pages


def read_pdf_pages(pdf_path: Path, enable_ocr_fallback: bool, ocr_lang: str) -> List[str]:
    pages = read_pdf_pages_with_pypdf(pdf_path)
    has_meaningful_text = any(len(text.strip()) >= 20 for text in pages)
    if has_meaningful_text or not enable_ocr_fallback:
        return pages

    print(f"No meaningful text via pypdf, switching to OCR: {pdf_path.name}")
    return read_pdf_pages_with_ocr(pdf_path, ocr_lang)


def main() -> None:
    args = parse_args()

    if not args.pdf_dir.exists():
        raise SystemExit(f"PDF directory does not exist: {args.pdf_dir}")

    pdf_files = sorted(args.pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in: {args.pdf_dir}")
        return

    client = chromadb.HttpClient(host=args.host, port=args.port)
    collection = client.get_or_create_collection(name=args.collection)

    if args.reset_collection:
        if args.dry_run:
            print(
                "Dry run enabled. Collection would be reset before ingest: "
                f"{args.collection}"
            )
        else:
            client.delete_collection(name=args.collection)
            collection = client.get_or_create_collection(name=args.collection)
            print(f"Collection reset: {args.collection}")

    print(f"Collection: {args.collection}")
    print(f"PDF directory: {args.pdf_dir}")
    print(f"Total PDFs found: {len(pdf_files)}")
    print(f"Min chunk chars: {args.min_chunk_chars}")
    print(f"Upsert retries: {args.upsert_retries}")

    disk_source_files = {normalize_source_path(path) for path in pdf_files}

    removed_source_files: List[str] = []
    if args.delete_missing:
        indexed_source_files = list_indexed_source_files(collection)
        removed_source_files = sorted(indexed_source_files - disk_source_files)
        print(
            f"Indexed source files found in collection: {len(indexed_source_files)}")
        print(f"Missing on disk (to delete): {len(removed_source_files)}")

        if removed_source_files and args.dry_run:
            print("Dry run enabled. The following source files would be deleted:")
            for source_file in removed_source_files:
                print(f"- {source_file}")
        elif removed_source_files:
            deleted_sources = 0
            for source_file in removed_source_files:
                collection.delete(where={"source_file": source_file})
                deleted_sources += 1
                print(
                    f"Deleted indexed chunks for missing file: {source_file}")
            print(f"Deleted missing source files: {deleted_sources}")
        else:
            print("No missing indexed files to delete.")

    new_files: List[Path] = []
    skipped_files: List[Path] = []
    for pdf_path in pdf_files:
        source_file = normalize_source_path(pdf_path)
        if args.reset_collection:
            new_files.append(pdf_path)
            continue

        if is_file_already_ingested(collection, source_file):
            skipped_files.append(pdf_path)
        else:
            new_files.append(pdf_path)

    print(f"Already ingested: {len(skipped_files)}")
    print(f"New files to ingest: {len(new_files)}")

    if not new_files:
        print("Nothing new to ingest.")
        return

    if args.dry_run:
        print("Dry run enabled. The following files would be ingested:")
        for pdf_path in new_files:
            print(f"- {pdf_path}")
        return

    total_chunks = 0
    ingested_files = 0

    for pdf_path in new_files:
        source_file = normalize_source_path(pdf_path)
        file_key = build_file_key(source_file)

        pages = read_pdf_pages(
            pdf_path,
            enable_ocr_fallback=args.enable_ocr_fallback,
            ocr_lang=args.ocr_lang,
        )

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[dict] = []

        for page_index, page_text in enumerate(pages, start=1):
            if not page_text:
                continue

            page_text = normalize_extracted_text(page_text)
            if not page_text:
                continue

            for chunk_index, chunk in enumerate(
                iter_chunks(page_text, args.chunk_size, args.chunk_overlap), start=1
            ):
                if len(chunk) < args.min_chunk_chars:
                    continue

                chunk_id = f"pdf::{file_key}::p{page_index}::c{chunk_index}"
                ids.append(chunk_id)
                documents.append(chunk)
                metadatas.append(
                    {
                        "source_file": source_file,
                        "file_name": pdf_path.name,
                        "page": page_index,
                        "chunk": chunk_index,
                        "doc_type": "pdf",
                    }
                )

        if not documents:
            print(f"Skipping (no extractable text): {pdf_path}")
            continue

        upsert_ok = False
        last_error: Exception | None = None
        for attempt in range(1, max(1, args.upsert_retries) + 1):
            try:
                collection.upsert(
                    ids=ids, documents=documents, metadatas=metadatas)
                upsert_ok = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                is_compaction_error = "Error in compaction" in str(exc)
                if is_compaction_error and attempt < max(1, args.upsert_retries):
                    wait_seconds = min(5, attempt * 1.5)
                    print(
                        f"Upsert failed for {pdf_path.name} (attempt {attempt}), "
                        f"retrying in {wait_seconds}s: {exc}"
                    )
                    time.sleep(wait_seconds)
                    continue
                break

        if not upsert_ok:
            # Avoid partial-ingest leftovers causing false "already ingested" next run.
            try:
                collection.delete(where={"source_file": source_file})
                print(
                    f"Rolled back partial chunks for failed file: {pdf_path.name}"
                )
            except Exception as rollback_exc:  # noqa: BLE001
                print(
                    "Rollback failed after upsert error. "
                    f"source_file={source_file}, error={rollback_exc}"
                )

            raise RuntimeError(
                f"Failed to upsert {pdf_path.name} after {max(1, args.upsert_retries)} attempts"
            ) from last_error

        ingested_files += 1
        total_chunks += len(documents)
        print(f"Ingested {pdf_path.name}: {len(documents)} chunks")

    print("\nDone.")
    print(f"Files ingested: {ingested_files}")
    print(f"Chunks upserted: {total_chunks}")


if __name__ == "__main__":
    main()
