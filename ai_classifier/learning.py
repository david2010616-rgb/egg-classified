"""Confirmed-case retrieval and a versioned local character n-gram Naive Bayes model.
This is neither model self-training nor foundation-model fine tuning.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from .storage import digest, now


def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def sample_text(item):
    return (item["name"] + "\n" + item.get("text", ""))[:7000]


def series(text):
    return re.sub(r"[\W\d_]+", "", normalized(text.rsplit(".", 1)[0]))


def features(text):
    value = normalized(text)
    return Counter(value[i : i + 3] for i in range(max(0, len(value) - 2)))


def grouped(rows):
    """Connected components: exact hash, filename series, and >=.85 char similarity.
    Conservative over-grouping is intentional. O(n²), appropriate to hundreds of cases.
    """
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    texts = [normalized(r.get("text", "") or r["name"])[:7000] for r in rows]
    for i, a in enumerate(rows):
        for j in range(i):
            b = rows[j]
            same_series = series(a["name"]) == series(b["name"]) and len(series(a["name"])) >= 3
            exact = a["fingerprint"] == b["fingerprint"]
            similar = False
            if not exact and not same_series and min(len(texts[i]), len(texts[j])) >= 20:
                matcher = SequenceMatcher(None, texts[i], texts[j], autojunk=False)
                similar = matcher.quick_ratio() >= 0.85 and matcher.ratio() >= 0.85
            if exact or same_series or similar:
                parent[root(i)] = root(j)
    components = defaultdict(list)
    for i in range(len(rows)):
        components[root(i)].append(i)
    result = []
    for indices in components.values():
        key = min(rows[i]["fingerprint"] for i in indices)
        result.append((key, indices))
    return result


def fit(rows):
    counts, totals, docs = defaultdict(Counter), Counter(), Counter()
    for row in rows:
        label = "/".join(row["category"])
        counts[label].update(features(sample_text(row)))
        totals[label] += sum(features(sample_text(row)).values())
        docs[label] += 1
    vocab = set().union(*(set(v) for v in counts.values()))
    return {
        "counts": dict(counts),
        "totals": dict(totals),
        "docs": dict(docs),
        "vocabulary": len(vocab),
    }


def predict(model, text):
    scores = {}
    f = features(text)
    for label, count in model["counts"].items():
        score = math.log(model["docs"][label] / sum(model["docs"].values()))
        den = model["totals"][label] + max(1, model["vocabulary"])
        scores[label] = score + sum(n * math.log((count.get(k, 0) + 1) / den) for k, n in f.items())
    return max(scores, key=scores.get)


def macro_f1(actual, predicted):
    values = []
    for label in sorted(set(actual) | set(predicted)):
        tp = sum(a == label and p == label for a, p in zip(actual, predicted))
        fp = sum(a != label and p == label for a, p in zip(actual, predicted))
        fn = sum(a == label and p != label for a, p in zip(actual, predicted))
        values.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return sum(values) / len(values)


class Learner:
    def __init__(self, store):
        self.store = store

    def cases(self, scheme):
        # Corrections replace the active label for an identical item under the same scheme.
        latest = {}
        for row in self.store.all("confirmation"):
            if row["scheme"] == scheme:
                latest[row["item"]] = row
        return list(latest.values())

    def references(self, scheme, item, limit=3):
        scored = []
        f = set(features(sample_text(item)))
        for row in self.cases(scheme):
            g = set(features(sample_text(row)))
            score = len(f & g) / max(1, len(f | g))
            if row["fingerprint"] == item["fingerprint"]:
                score = 1.0
            if score >= 0.2:
                scored.append((score, row))
        return [
            {
                "name": r["name"],
                "category": r["category"],
                "similarity": round(s, 3),
                "origin": "user_confirmed",
            }
            for s, r in sorted(scored, key=lambda x: -x[0])[:limit]
        ]

    def train(self, scheme, cancel):
        rows = self.cases(scheme)
        signature = digest([(r["id"], r["category"]) for r in rows])
        existing = [m for m in self.store.all("model") if m["scheme"] == scheme]
        if any(m["signature"] == signature for m in existing):
            return "Confirmed examples unchanged; no retraining needed"
        if len(rows) < 30:
            return f"Stored {len(rows)} confirmed examples; local training requires at least 30"
        if cancel.is_set():
            return "Training cancelled"
        groups = grouped(rows)
        train, test = [], []
        train_groups, test_groups = [], []
        for key, indices in groups:
            is_test = int(digest(key)[:8], 16) % 5 == 0
            (test if is_test else train).extend(rows[i] for i in indices)
            (test_groups if is_test else train_groups).append(key)
        labels = set("/".join(r["category"]) for r in rows)
        if (
            len(labels) < 2
            or len(test_groups) < 3
            or len(train_groups) < 5
            or any(
                sum("/".join(r["category"]) == label for r in test) < 2
                or sum("/".join(r["category"]) == label for r in train) < 3
                for label in labels
            )
        ):
            return "Examples stored; insufficient independent groups or per-class evaluation examples; no model activated"
        if cancel.is_set():
            return "Training cancelled"
        model = fit(train)
        actual = ["/".join(r["category"]) for r in test]
        score = macro_f1(actual, [predict(model, sample_text(r)) for r in test])
        majority = Counter("/".join(r["category"]) for r in train).most_common(1)[0][0]
        baseline = macro_f1(actual, [majority] * len(test))
        active = self.store.get("active_model", scheme)
        incumbent_score = 0.0
        overlap = False
        if active:
            old = self.store.get("model", active["version"])
            test_ids = {r["item"] for r in test}
            overlap = bool(test_ids & set(old["train_items"]))
            incumbent_score = macro_f1(
                actual, [predict(old["model"], sample_text(r)) for r in test]
            )
        accepted = (
            score >= 0.75 and score >= baseline + 0.05 and score >= incumbent_score and not overlap
        )
        record = self.store.put(
            "model",
            {
                "scheme": scheme,
                "signature": signature,
                "created_at": now(),
                "algorithm": "character-trigram multinomial naive Bayes",
                "model": model,
                "train_items": [r["item"] for r in train],
                "test_items": [r["item"] for r in test],
                "train_groups": train_groups,
                "test_groups": test_groups,
                "confirmation_ids": [r["id"] for r in rows],
                "accepted": accepted,
                "metrics": {
                    "macro_f1": score,
                    "majority_f1": baseline,
                    "incumbent_f1": incumbent_score,
                    "group_merge_overlap": overlap,
                    "train_count": len(train),
                    "test_count": len(test),
                },
            },
        )
        if accepted:
            self.store.put(
                "active_model",
                {
                    "id": scheme,
                    "version": record["id"],
                    "previous": active["version"] if active else None,
                },
            )
        return f"Local classifier {record['id'][:8]}: macro-F1={score:.3f}; " + (
            "activated" if accepted else "rejected; previous model retained"
        )

    def local_guess(self, scheme, item):
        active = self.store.get("active_model", scheme)
        if not active:
            return None
        model = self.store.get("model", active["version"])
        return {
            "category": predict(model["model"], sample_text(item)).split("/"),
            "version": model["id"],
            "origin": "local_classifier_unconfirmed",
        }

    def rollback(self, scheme):
        active = self.store.get("active_model", scheme)
        if not active or not active.get("previous"):
            raise ValueError("No previous model version is available")
        self.store.put(
            "active_model",
            {"id": scheme, "version": active["previous"], "previous": active["version"]},
        )
        return "Previous local classifier restored"
