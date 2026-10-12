"""Categories are generated data, never enumerated domain labels."""

S = {"type": "string", "maxLength": 4000}
B = {"type": "boolean"}


def arr(item):
    return {"type": "array", "items": item, "maxItems": 100}


def obj(**fields):
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


RECOGNIZE = obj(
    items=arr(obj(id=S, summary=S, evidence=S, entity=S, needs_search=B, query=S, ambiguity=arr(S)))
)
PROPOSE = obj(
    composition=S,
    schemes=arr(obj(name=S, rationale=S, instruction=S, folders=arr(S), examples=arr(S))),
)
CLASSIFY = obj(
    items=arr(
        obj(
            id=S,
            category=arr(S),
            reason=S,
            confidence={"type": "number", "minimum": 0, "maximum": 1},
            needs_review=B,
            alternatives=arr(S),
            source_urls=arr(S),
            relations=arr(obj(subject=S, relation=S, object=S, source_url=S)),
        )
    )
)

RESEARCH = obj(items=arr(obj(id=S, needs_search=B, query=S)))
