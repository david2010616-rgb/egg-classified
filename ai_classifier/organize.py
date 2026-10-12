"""Exclusive publish, fd-relative no-symlink traversal and durable per-file journal.
Copies are staged and fsynced before publishing. Moves unlink only verified originals.
"""

import hashlib
import os
import stat
import uuid
from pathlib import Path

from .backend import Cancelled
from .extract import directory_fd, no_links
from .storage import now
from .workflow import category_parts


def hash_fd(fd, cancel=None):
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256()
    while True:
        if cancel and cancel.is_set():
            raise Cancelled("Cancelled; operation journal saved")
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        h.update(block)
    return h.hexdigest()


def identity(st):
    return [st.st_dev, st.st_ino]


class Organizer:
    def __init__(self, store):
        self.store = store

    def preview(self, batch, root):
        if not str(root).strip():
            raise ValueError("Choose an output folder")
        root = no_links(root)
        used = set()
        rows = self.store.items(batch)
        for item in rows:
            if item["status"] == "done" or item.get("operation"):
                continue
            category = (
                item.get("category")
                if item.get("confirmation")
                else (item.get("prediction") or {}).get("category")
            )
            if not category or not item["source"]:
                continue
            source = no_links(item["source"])
            if root == source.parent or root in source.parent.parents:
                raise ValueError("Output cannot be the source directory or its ancestor")
            folder = root.joinpath(*category_parts(category))
            no_links(folder)
            target = folder / source.name
            index = 2
            while os.path.lexists(target) or str(target).casefold() in used:
                target = folder / f"{source.stem} ({index}){source.suffix}"
                index += 1
            used.add(str(target).casefold())
            item.update(target=str(target), destination_root=str(root))
            self.store.put("item", item)
        return self.store.items(batch)

    def transfer(self, op, cancel):
        if cancel.is_set():
            raise Cancelled("Cancelled")
        source, target = Path(op["source"]), Path(op["target"])
        # Target must belong to the pinned root, even if a journal record is malformed.
        root = Path(op["root"])
        if root not in target.parents or ".." in target.parts:
            raise ValueError("Destination is outside the allowed root")
        sdir = directory_fd(source.parent)
        try:
            tdir = directory_fd(target.parent, create=True)
            try:
                self._transfer_fds(op, cancel, sdir, tdir, source.name, target.name)
            finally:
                os.close(tdir)
        finally:
            os.close(sdir)

    def _transfer_fds(self, op, cancel, sdir, tdir, source, target):
        temp = ".classifier-" + op["id"] + ".tmp"

        def stat_at(name, fd):
            try:
                return os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return None

        published = stat_at(target, tdir)
        if published:
            if not stat.S_ISREG(published.st_mode) or identity(published) != op.get(
                "copy_identity"
            ):
                raise ValueError("Destination exists or was replaced; it will not be overwritten")
            out = os.open(target, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=tdir)
            try:
                if hash_fd(out, cancel) != op["fingerprint"]:
                    raise ValueError("Published destination content has changed")
            finally:
                os.close(out)
        else:
            src = os.open(source, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sdir)
            try:
                before = os.fstat(src)
                if not stat.S_ISREG(before.st_mode) or hash_fd(src, cancel) != op["fingerprint"]:
                    raise ValueError("Source content changed; import it again")
                op["source_identity"] = identity(before)
                # Never truncate a prior temporary file: remove only our recorded inode.
                oldtemp = stat_at(temp, tdir)
                if oldtemp:
                    if identity(oldtemp) != op.get("temp_identity") or not stat.S_ISREG(
                        oldtemp.st_mode
                    ):
                        raise ValueError("Temporary file identity is unknown; manual review required")
                    os.unlink(temp, dir_fd=tdir)
                out = os.open(
                    temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=tdir
                )
                try:
                    op["temp_identity"] = identity(os.fstat(out))
                    op["state"] = "copying"
                    self.store.put("operation", op)
                    os.lseek(src, 0, os.SEEK_SET)
                    h = hashlib.sha256()
                    while True:
                        if cancel.is_set():
                            raise Cancelled("Copy cancelled; it can be resumed")
                        block = os.read(src, 1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                        view = memoryview(block)
                        while view:
                            view = view[os.write(out, view) :]
                    if h.hexdigest() != op["fingerprint"]:
                        raise ValueError("Source changed during copying")
                    os.fsync(out)
                    op.update(copy_identity=identity(os.fstat(out)), state="copied")
                    self.store.put("operation", op)
                finally:
                    os.close(out)
                # Atomic no-clobber publish, even when another process occupies the preview path.
                os.link(temp, target, src_dir_fd=tdir, dst_dir_fd=tdir, follow_symlinks=False)
                os.fsync(tdir)
            finally:
                os.close(src)
        op["state"] = "published"
        self.store.put("operation", op)
        if op["mode"] == "move":
            original = stat_at(source, sdir)
            if original:
                if not stat.S_ISREG(original.st_mode) or identity(original) != op.get(
                    "source_identity"
                ):
                    raise ValueError("Source identity changed; it will not be deleted")
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sdir)
                try:
                    if hash_fd(fd, cancel) != op["fingerprint"]:
                        raise ValueError("Source changed; keeping both source and copy")
                finally:
                    os.close(fd)
                if (
                    identity(os.stat(source, dir_fd=sdir, follow_symlinks=False))
                    != op["source_identity"]
                ):
                    raise ValueError("Source was replaced")
                os.unlink(source, dir_fd=sdir)
                os.fsync(sdir)
        if stat_at(temp, tdir):
            if identity(os.stat(temp, dir_fd=tdir, follow_symlinks=False)) == op.get(
                "temp_identity"
            ):
                os.unlink(temp, dir_fd=tdir)
        op.update(state="done", finished_at=now(), error=None)
        self.store.put("operation", op)

    def execute(self, batch, mode, cancel, progress=lambda msg: None):
        if mode not in ("copy", "move"):
            raise ValueError("Select copy or move")
        errors, done = [], 0
        for item in self.store.items(batch):
            if cancel.is_set():
                raise Cancelled("Cancelled; completed files will not be processed again")
            if item["status"] == "done" or not item.get("confirmation") or not item["source"]:
                continue
            if not item.get("target"):
                raise ValueError("Generate a path preview first")
            op = self.store.get("operation", item.get("operation", ""))
            if op and op["mode"] != mode:
                raise ValueError("An operation is pending; resume with its original copy/move mode")
            if not op:
                op = self.store.put(
                    "operation",
                    {
                        "id": uuid.uuid4().hex,
                        "batch": batch,
                        "item": item["id"],
                        "source": item["source"],
                        "target": item["target"],
                        "root": item["destination_root"],
                        "fingerprint": item["fingerprint"],
                        "mode": mode,
                        "state": "prepared",
                        "at": now(),
                    },
                )
                item["operation"] = op["id"]
                self.store.put("item", item)
            try:
                if op["state"] != "done":
                    self.transfer(op, cancel)
                item.update(status="done", target=op["target"])
                self.store.put("item", item)
                done += 1
                progress(f"{'Copy' if mode == 'copy' else 'Move'} complete: {item['name']}")
            except Cancelled:
                raise
            except (OSError, ValueError) as exc:
                op["error"] = str(exc)
                self.store.put("operation", op)
                errors.append(f"{item['name']}: {exc}")
        return {"completed": done, "errors": errors}

    def undo(self, batch, cancel):
        done, errors = 0, []
        for op in reversed(self.store.all("operation")):
            if (
                op.get("batch") != batch
                or op.get("mode") != "move"
                or op.get("undo_of")
                or op.get("undone")
            ):
                continue
            if op["state"] != "done":
                continue
            self_item = self.store.get("item", op["item"])
            reverse_id = "undo-" + op["id"]
            reverse = self.store.get("operation", reverse_id)
            if not reverse:
                reverse = self.store.put(
                    "operation",
                    {
                        "id": reverse_id,
                        "undo_of": op["id"],
                        "batch": batch,
                        "item": op["item"],
                        "source": op["target"],
                        "target": op["source"],
                        "root": str(Path(op["source"]).parent),
                        "fingerprint": op["fingerprint"],
                        "mode": "move",
                        "state": "prepared",
                        "at": now(),
                    },
                )
            try:
                if reverse["state"] != "done":
                    self.transfer(reverse, cancel)
                op["undone"] = True
                self.store.put("operation", op)
                self_item.update(status="confirmed", operation=None, target=None)
                self.store.put("item", self_item)
                done += 1
            except Cancelled:
                raise
            except (OSError, ValueError) as exc:
                errors.append(f"{op['source']}: {exc}")
        return {"restored": done, "errors": errors}
