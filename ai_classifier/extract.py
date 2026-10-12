"""Bounded local extraction. OCR is an injectable local adapter, never silently assumed."""

import csv
import hashlib
import io
import os
import stat
import zipfile
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

MAX_BYTES = 32 * 1024 * 1024
MAX_TEXT = 6000


class OCRAdapter(Protocol):
    def extract(self, path: Path) -> str: ...


def no_links(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symbolic-link paths are not allowed")
    return path


def directory_fd(path, create=False):
    path = Path(os.path.abspath(Path(path).expanduser()))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_regular(path):
    path = no_links(path)
    parent = directory_fd(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("Not a regular file")
    return os.fdopen(fd, "rb")


def file_hash(path, cancel=None):
    path = no_links(path)
    with open_regular(path) as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("Not a regular file")
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b""):
            if cancel and cancel.is_set():
                from .backend import Cancelled

                raise Cancelled("Reading cancelled")
            h.update(block)
        return h.hexdigest()


def read_file(path, ocr: OCRAdapter | None = None, cancel=None):
    path = no_links(path)
    result = {
        "name": path.name,
        "source": str(path),
        "extension": path.suffix.lower(),
        "fingerprint": file_hash(path, cancel),
        "text": "",
        "read_status": "filename_only",
        "read_note": "Filename only: content extraction is not supported for this format",
    }
    try:
        if path.stat().st_size > MAX_BYTES:
            result["read_note"] = "Filename only: file exceeds the 32 MiB extraction limit"
            return result
        with open_regular(path) as stream:
            snapshot = stream.read(MAX_BYTES + 1)
        if (
            len(snapshot) > MAX_BYTES
            or hashlib.sha256(snapshot).hexdigest() != result["fingerprint"]
        ):
            raise ValueError("File changed while being read")
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".markdown", ".csv", ".tsv"}:
            raw = snapshot
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("gb18030")
            result["text"] = text[:MAX_TEXT]
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(snapshot)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > MAX_BYTES:
                    raise ValueError("DOCX text exceeds the extraction limit")
                xml = z.read(info)
                if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                    raise ValueError("XML entities are not allowed")
                root = ElementTree.fromstring(xml)
                result["text"] = "\n".join(
                    e.text or "" for e in root.iter() if e.tag.endswith("}t")
                )[:MAX_TEXT]
        elif suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(snapshot))
            pieces = []
            for page in reader.pages[:20]:
                pieces.append(page.extract_text() or "")
                if sum(map(len, pieces)) >= MAX_TEXT:
                    break
            result["text"] = "\n".join(pieces)[:MAX_TEXT].strip()
        if result["text"].strip():
            result.update(
                read_status="text_excerpt",
                read_note="Text excerpt (up to 6000 characters; first 20 PDF pages)",
            )
        elif suffix in {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".webp", ".bmp"}:
            if ocr:
                result["text"] = ocr.extract(path)[:MAX_TEXT]
                if result["text"].strip():
                    result.update(
                        read_status="ocr_excerpt", read_note="Excerpt from the configured OCR adapter"
                    )
                else:
                    result["read_note"] = "Filename only: OCR returned no text"
            else:
                result["read_note"] = "Filename only: no OCR/vision adapter configured"
        elif suffix in {".txt", ".md", ".csv", ".docx"}:
            result["read_note"] = "Filename only: document text is empty"
    except Exception as exc:
        result.update(
            text="",
            read_status="filename_only",
            read_note=f"Filename only: extraction failed ({type(exc).__name__})",
        )
    return result


def read_names(path):
    path = no_links(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Name list exceeds 32 MiB")
    with open_regular(path) as stream:
        text = stream.read(MAX_BYTES + 1).decode("utf-8-sig")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Name list exceeds the reading limit")
    if path.suffix.lower() == ".csv":
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return []
        header = [v.strip().lower() for v in rows[0]]
        known = next((v for v in ("name", "filename", "名称", "文件名") if v in header), None)
        col = header.index(known) if known else 0
        names = [row[col] for row in (rows[1:] if known else rows) if len(row) > col]
    else:
        names = text.splitlines()
    return [
        dict(
            name=n.strip()[:500],
            source=None,
            extension="",
            text="",
            fingerprint=hashlib.sha256(n.strip().encode()).hexdigest(),
            read_status="name_list",
            read_note="Name-list entry only; not a local file",
        )
        for n in names
        if n.strip()
    ]


def scan_paths(folder, excludes=()):
    root = no_links(folder)
    blocked = [Path(p).absolute() for p in excludes]
    if any(root == p or p in root.parents for p in blocked):
        return
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if not (Path(current) / d).is_symlink()
            and not any(
                (Path(current) / d) == p or p in (Path(current) / d).parents for p in blocked
            )
            and not d.startswith(".")
        )
        for name in sorted(names):
            p = Path(current) / name
            if not p.is_symlink() and p.is_file() and not name.startswith("."):
                yield p
