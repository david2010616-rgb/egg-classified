"""Explicit live integration probe. Never moves files or creates trusted labels."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from .backend import GeminiBackend, Settings
from .storage import Store, now
from .workflow import Workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", type=Path, help="UTF-8 TXT/CSV test names you permit sending")
    parser.add_argument("--output", type=Path, default=Path("work/live-api-verification.json"))
    parser.add_argument(
        "--search", action="store_true", help="Allow Google Search, maximum 5 requests"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "at": now(),
        "model": os.getenv("GEMINI_MODEL", ""),
        "status": "not_run",
        "note": "Connectivity and workflow probe; not a human-labeled accuracy evaluation",
    }
    if not os.getenv("GEMINI_API_KEY"):
        report.update(status="skipped_no_key", note="No GEMINI_API_KEY; live API verification not performed")
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report["note"])
        return 77
    if not report["model"]:
        parser.error("Set GEMINI_MODEL to an explicitly selected supported model ID")
    if not args.names:
        parser.error("Provide --names to explicitly select test names permitted for API transmission")
    store = Store(args.output.parent / "live-verification.sqlite3")
    cancel = threading.Event()
    cfg = Settings(
        model=report["model"], network=True, search=args.search, max_calls=30, max_search=5
    )
    backend = GeminiBackend(store, "pending", cfg, cancel)
    flow = Workflow(store, backend, cancel)
    batch = flow.new_batch(manifest=args.names)["id"]
    backend.batch = batch
    try:
        flow.recognize(batch)
        proposal = flow.propose(batch, "Suggest suitable schemes for this batch and flag ambiguous names or editions")
        flow.choose(batch, proposal["proposals"][0])
        flow.classify(batch)
        report.update(
            status="completed_unreviewed",
            batch=batch,
            proposals=proposal["proposals"],
            items=store.items(batch),
            usage=store.usage(batch),
        )
    except Exception as exc:
        report.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            batch=batch,
            usage=store.usage(batch),
        )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["status"], args.output)
    return 0 if report["status"] == "completed_unreviewed" else 1


if __name__ == "__main__":
    sys.exit(main())
