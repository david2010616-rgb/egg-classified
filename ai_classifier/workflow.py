"""Resumable read -> understand -> propose -> choose -> classify -> confirm workflow."""

from pathlib import Path
import uuid

from . import schemas
from .backend import Cancelled
from .extract import read_file, read_names
from .learning import Learner
from .storage import digest, now


def category_parts(value):
    import re
    import unicodedata

    if isinstance(value, str):
        value = value.split("/")
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise ValueError("Category must contain 1-6 folder levels")
    result = []
    for part in value:
        part = unicodedata.normalize("NFKC", str(part)).strip()
        if (
            not part
            or part in {".", ".."}
            or len(part) > 80
            or re.search(r"[\\/:\x00-\x1f<>|?*]", part)
        ):
            raise ValueError("Category contains unsafe path characters or exceeds 80 characters")
        if part.endswith((".", " ")) or re.fullmatch(
            r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part
        ):
            raise ValueError("Category is not a portable folder name")
        result.append(part)
    return result


class Workflow:
    def __init__(self, store, backend, cancel, progress=lambda message: None):
        self.store, self.backend, self.cancel, self.progress = store, backend, cancel, progress
        self.learner = Learner(store)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("Cancelled; completed items saved; this stage can be resumed")

    def new_batch(self, paths=None, manifest=None):
        # Persist the complete inventory before extraction so cancellation is resumable.
        batch = self.store.put(
            "batch",
            {
                "created_at": now(),
                "stage": "reading",
                "scheme": None,
                "proposals": [],
                "inventory": [str(p) for p in paths or []],
                "manifest": str(manifest) if manifest else None,
                "name_inventory": read_names(Path(manifest)) if manifest else None,
            },
        )
        self.ingest(batch["id"])
        return batch

    def ingest(self, batch_id):
        batch = self.store.get("batch", batch_id)
        existing = {r["inventory_index"] for r in self.store.items(batch_id)}
        names = batch.get("name_inventory")
        inventory = names if names is not None else batch["inventory"]
        for i, entry in enumerate(inventory):
            self.check()
            if i in existing:
                continue
            try:
                item = (
                    dict(entry) if names is not None else read_file(Path(entry), cancel=self.cancel)
                )
            except (OSError, ValueError) as exc:
                item = {
                    "name": Path(entry).name,
                    "source": entry,
                    "text": "",
                    "extension": Path(entry).suffix,
                    "fingerprint": "",
                    "read_status": "error",
                    "read_note": str(exc),
                }
            item.update(
                batch=batch_id,
                inventory_index=i,
                status="read",
                prediction=None,
                confirmation=None,
                recognition=None,
                search_result=None,
                target=None,
            )
            self.store.put("item", item)
            self.progress(f"Reading {i + 1}/{len(inventory)}")
        batch["stage"] = "read"
        self.store.put("batch", batch)
        return batch

    def chunks(self, items):
        n = max(
            1,
            min(
                30,
                getattr(self.backend, "settings", None).chunk_size
                if getattr(self.backend, "settings", None)
                else 20,
            ),
        )
        for offset in range(0, len(items), n):
            self.check()
            yield items[offset : offset + n]

    @staticmethod
    def public(item):
        return {k: item[k] for k in ("id", "name", "extension", "text", "read_status", "read_note")}

    def checked_items(self, response, chunk, schema):
        from jsonschema import validate

        data = response["data"]
        validate(data, schema)
        results = data["items"]
        if len(results) != len(chunk) or {r["id"] for r in results} != {r["id"] for r in chunk}:
            raise ValueError("Model returned missing or duplicate item IDs; chunk was not applied and can be retried")
        return {r["id"]: r for r in results}

    def recognize(self, batch_id):
        self.ingest(batch_id)
        items = self.store.items(batch_id)
        for chunk in self.chunks([r for r in items if not r["recognition"]]):
            response = self.backend.request(
                "recognize",
                {
                    "instruction": "Understand each item without classifying it yet. Decide whether external facts need verification and provide a precise query. Do not infer unread contents.",
                    "items": [self.public(r) for r in chunk],
                },
                schemas.RECOGNIZE,
            )
            results = self.checked_items(response, chunk, schemas.RECOGNIZE)
            for row in chunk:
                row.update(recognition=results[row["id"]], status="recognized")
                self.store.put("item", row)
            self.progress(
                f"Recognized {sum(bool(r['recognition']) for r in self.store.items(batch_id))}/{len(items)}"
            )
        cfg = getattr(self.backend, "settings", None)
        if cfg and cfg.search:
            for item in self.store.items(batch_id):
                self.check()
                rec = item["recognition"]
                if rec["needs_search"] and not item["search_result"]:
                    result = self.backend.request(
                        "verify entities and relationships",
                        {
                            "name": item["name"],
                            "query": rec["query"],
                            "summary": rec["summary"],
                            "instruction": "Verify entities, editions and relationships with distinct roles. List ambiguous names, joint participants and conflicting sources. Cite evidence and state uncertainty.",
                        },
                        None,
                        search=True,
                    )
                    item["search_result"] = result
                    self.store.put("item", item)
                    self.progress("Search evidence saved: " + item["name"])

    def propose(self, batch_id, requirement=""):
        batch = self.store.get("batch", batch_id)
        items = self.store.items(batch_id)
        if not items or any(not r["recognition"] for r in items):
            raise ValueError("Finish recognition first, or create a manual scheme offline")
        self.check()
        result = self.backend.request(
            "propose",
            {
                "user_requirement": requirement,
                "instruction": "Analyze the whole batch, then suggest 2-4 schemes based on its contents. Explain each scheme, expected folders and examples. Do not classify individual items yet.",
                "items": [
                    {"name": r["name"], "summary": r["recognition"]["summary"][:600]} for r in items
                ],
            },
            schemas.PROPOSE,
        )
        from jsonschema import validate

        validate(result["data"], schemas.PROPOSE)
        proposals = result["data"]["schemes"]
        if not 1 <= len(proposals) <= 6:
            raise ValueError("No valid classification schemes returned")
        batch.update(
            proposals=proposals,
            composition=result["data"]["composition"],
            requirement=requirement,
            stage="proposed",
        )
        self.store.put("batch", batch)
        return batch

    def choose(self, batch_id, proposal=None, scheme_id=None):
        batch = self.store.get("batch", batch_id)
        if any(r.get("confirmation") for r in self.store.items(batch_id)):
            raise ValueError("This batch has confirmed labels; create a new batch to change schemes")
        if scheme_id:
            scheme = self.store.get("scheme", scheme_id)
            if not scheme:
                raise ValueError("Scheme does not exist")
        else:
            if not proposal or not proposal.get("instruction", "").strip():
                raise ValueError("Provide instructions for the scheme")
            scheme = self.store.put(
                "scheme", {**proposal, "id": uuid.uuid4().hex, "created_at": now(), "registry": []}
            )
        if batch.get("scheme") != scheme["id"]:
            for item in self.store.items(batch_id):
                item.update(
                    prediction=None,
                    target=None,
                    status="recognized" if item["recognition"] else "read",
                )
                self.store.put("item", item)
        batch.update(scheme=scheme["id"], stage="chosen")
        self.store.put("batch", batch)
        return scheme

    def research_for_scheme(self, chunk, scheme):
        """After selection, verify relationships actually needed by this scheme."""
        cfg = getattr(self.backend, "settings", None)
        if not cfg or not cfg.search:
            return
        pending = [
            r for r in chunk if (r.get("scheme_research") or {}).get("scheme") != scheme["id"]
        ]
        if pending:
            response = self.backend.request(
                "research",
                {
                    "instruction": "Decide whether the selected scheme needs external facts. Distinguish relationship roles. Skip search when existing evidence is sufficient. Generate public entity/relationship queries only; never copy private excerpts into search queries.",
                    "scheme": {k: scheme[k] for k in ("name", "instruction")},
                    "items": [
                        {
                            **self.public(r),
                            "recognition": r["recognition"],
                            "existing_evidence": (r.get("search_result") or {}).get("text", "")[
                                :12000
                            ],
                        }
                        for r in pending
                    ],
                },
                schemas.RESEARCH,
            )
            assessments = self.checked_items(response, pending, schemas.RESEARCH)
            for item in pending:
                item["scheme_research"] = {
                    **assessments[item["id"]],
                    "scheme": scheme["id"],
                    "completed": False,
                }
                self.store.put("item", item)
        for item in chunk:
            self.check()
            assessment = item["scheme_research"]
            if not assessment["needs_search"] or assessment["completed"]:
                continue
            result = self.backend.request(
                "verify selected scheme relationships",
                {
                    "name": item["name"],
                    "query": assessment["query"],
                    "scheme": scheme["instruction"],
                    "instruction": "Verify facts needed by this scheme. List relationship types, ambiguous editions, joint participants and conflicting sources; cite evidence.",
                },
                None,
                search=True,
            )
            old = item.get("search_result")
            if old:
                result["text"] = old["text"] + "\n\nAdditional verification:\n" + result["text"]
                result["sources"] = old.get("sources", []) + result.get("sources", [])
                result["queries"] = old.get("queries", []) + result.get("queries", [])
                result["suggestions"] = old.get("suggestions", []) + result.get("suggestions", [])
                result["previous_queries"] = old.get("previous_queries", []) + [
                    {"at": old["queried_at"], "queries": old.get("queries", [])}
                ]
            item["search_result"] = result
            assessment["completed"] = True
            self.store.put("item", item)
            self.progress("Scheme relationships checked: " + item["name"])

    def classify(self, batch_id, local_only=False):
        batch = self.store.get("batch", batch_id)
        scheme = self.store.get("scheme", batch.get("scheme"))
        if not scheme:
            raise ValueError("Choose a scheme before classifying items")
        pending = [
            r for r in self.store.items(batch_id) if not r["prediction"] and not r["confirmation"]
        ]
        if local_only:
            for item in pending:
                self.check()
                refs = self.learner.references(scheme["id"], item)
                guess = self.learner.local_guess(scheme["id"], item)
                category = (
                    refs[0]["category"]
                    if refs and refs[0]["similarity"] == 1
                    else (guess or {}).get("category")
                )
                if category:
                    item.update(
                        prediction={
                            "category": category,
                            "reason": "Suggestion from local confirmed examples/model; still requires confirmation",
                            "needs_review": True,
                            "source_urls": [],
                            "relations": [],
                            "alternatives": [],
                            "confidence": 0,
                        },
                        status="review",
                    )
                    self.store.put("item", item)
            return
        for chunk in self.chunks(pending):
            self.research_for_scheme(chunk, scheme)
            payload = {
                "scheme": {k: scheme[k] for k in ("name", "instruction", "registry")},
                "instruction": "Follow the user-selected scheme. Reuse equivalent category names in registry; add only missing categories. Return paths as arrays. Set needs_review for unsupported external facts, ambiguity or conflicts. Cite only URLs in each item's search_result.sources.",
                "items": [
                    {
                        **self.public(r),
                        "recognition": r["recognition"],
                        "search_result": r["search_result"],
                        "confirmed_references": self.learner.references(scheme["id"], r),
                        "local_classifier_reference": self.learner.local_guess(scheme["id"], r),
                    }
                    for r in chunk
                ],
            }
            result = self.backend.request("classify", payload, schemas.CLASSIFY)
            predictions = self.checked_items(result, chunk, schemas.CLASSIFY)
            for item in chunk:
                p = predictions[item["id"]]
                p["category"] = category_parts(p["category"])
                allowed = {s["url"] for s in (item["search_result"] or {}).get("sources", [])}
                invalid = any(u not in allowed for u in p["source_urls"]) or any(
                    r["source_url"] not in allowed for r in p["relations"]
                )
                p["source_urls"] = [u for u in p["source_urls"] if u in allowed]
                for relation in p["relations"]:
                    relation["verified_source"] = relation["source_url"] in allowed
                relation_objects = {}
                for relation in p["relations"]:
                    key = (relation["subject"].casefold(), relation["relation"].casefold())
                    relation_objects.setdefault(key, set()).add(relation["object"].casefold())
                multiple_participants = any(
                    len(objects) > 1 for objects in relation_objects.values()
                )
                rec = item["recognition"] or {}
                review = (
                    p["needs_review"]
                    or multiple_participants
                    or invalid
                    or bool(rec.get("ambiguity"))
                    or len(p["alternatives"]) > 0
                    or p["confidence"] < 0.8
                    or item["read_status"] in ("filename_only", "error")
                )
                if (
                    rec.get("needs_search")
                    or (item.get("scheme_research") or {}).get("needs_search")
                ) and not allowed:
                    review = True
                p["needs_review"] = review
                # Reuse spelling/case of established names; semantics still require model + human.
                canonical = {"/".join(c).casefold(): c for c in scheme["registry"]}
                p["category"] = canonical.get("/".join(p["category"]).casefold(), p["category"])
                if p["category"] not in scheme["registry"]:
                    scheme["registry"].append(p["category"])
                self.store.put("scheme", scheme)
                item.update(prediction=p, status="review" if review else "predicted")
                self.store.put("item", item)
            self.store.put("scheme", scheme)
            self.progress(
                f"Classified {sum(bool(r['prediction']) for r in self.store.items(batch_id))}/{len(self.store.items(batch_id))}"
            )
        batch["stage"] = "classified"
        self.store.put("batch", batch)

    def confirm(self, item_id, category):
        item = self.store.get("item", item_id)
        batch = self.store.get("batch", item["batch"])
        scheme = self.store.get("scheme", batch.get("scheme"))
        if not scheme:
            raise ValueError("Choose a scheme first")
        if item.get("operation"):
            raise ValueError("An operation is recorded; resume and undo it before changing the category")
        if item["status"] == "done":
            raise ValueError("File already organized; undo before changing it")
        if item["read_status"] == "error":
            raise ValueError("Cannot confirm an unreadable or unidentified file")
        category = category_parts(category)
        record = self.store.put(
            "confirmation",
            {
                "item": item_id,
                "scheme": scheme["id"],
                "name": item["name"],
                "text": item["text"],
                "fingerprint": item["fingerprint"],
                "category": category,
                "origin": "user_confirmed",
                "at": now(),
                "prediction_digest": digest(item["prediction"]),
            },
        )
        item.update(confirmation=record["id"], category=category, status="confirmed", target=None)
        self.store.put("item", item)
        if category not in scheme["registry"]:
            scheme["registry"].append(category)
            self.store.put("scheme", scheme)
        return record
