"""Offline, read-only musical style reference. Never opens projects or plugins."""

from functools import lru_cache
import json

from .settings import ROOT


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT / "resources/styles/catalog.json").read_text(encoding="utf-8"))


def summary(item):
    fields = ("id", "name", "english", "kind", "year", "family", "aliases", "relatedFamilies", "yearDisplay")
    return {k: item.get(k) for k in fields} | {
        "tags": list(
            dict.fromkeys(
                n["label"] for notes in item["sections"].values() for n in notes if n["label"] != "编曲切入"
            )
        )
    }


def get(ident):
    from .service import DomainError

    item = next((x for x in catalog()["items"] if x["id"] == ident), None)
    if item is None:
        raise DomainError("style_not_found", "没有找到这个音乐风格。", 404)
    edges = [e for e in catalog()["edges"] if ident in (e["source"], e["target"])]
    related = {e["source"] if e["target"] == ident else e["target"] for e in edges}
    return {
        **item,
        "relations": edges,
        "related_styles": [summary(x) for x in catalog()["items"] if x["id"] in related],
    }


def dispatch(op, args):
    data = catalog()
    if op == "styles.get":
        return get(args.style_id)
    if op == "styles.index":
        return {
            "categories": data["categories"],
            "items": [summary(x) for x in data["items"]],
            "total": sum(x["kind"] == "genre" for x in data["items"]),
            "source": data["source"],
            "retrieved_at": data["retrieved_at"],
            "edge_count": len(data["edges"]),
        }
    if op == "styles.graph":
        return {
            "nodes": [
                {k: x.get(k) for k in ("id", "name", "english", "kind", "year", "family")}
                for x in data["items"]
            ],
            "edges": data["edges"],
        }
    if args.action in ("detail", "related"):
        if not args.style_id:
            from .service import DomainError

            raise DomainError("style_id_required", "请指定风格 ID。", 422)
        value = get(args.style_id)
        if args.action == "related":
            related = value["related_styles"][: args.limit]
            selected = {item["id"] for item in related}
            return {
                "id": value["id"],
                "name": value["name"],
                "relations": [
                    e for e in value["relations"] if e["source"] in selected or e["target"] in selected
                ][: args.limit],
                "items": related,
            }
        # Bounded references keep a large reading list out of the model context.
        return {
            k: v[: args.limit] if k in ("tracks", "related_styles", "relations", "sources") else v
            for k, v in value.items()
        }
    query = args.query.strip().casefold()
    parts = query.split()
    matches = []
    for item in data["items"]:
        if item["kind"] != "genre":
            continue
        if (
            args.category
            and args.category != item["family"]
            and args.category not in (item["relatedFamilies"] or [])
        ):
            continue
        compact = summary(item)
        text = " ".join(
            [item["id"], item["name"], item["english"], *(item["aliases"] or []), *compact["tags"]]
        ).casefold()
        if all(part in text for part in parts):
            matches.append(compact)
    matches.sort(
        key=lambda x: (
            x["name"].casefold() != query,
            x["english"].casefold() != query,
            x["year"] is None,
            x["year"] or 0,
            x["id"],
        )
    )
    return {"total": len(matches), "items": matches[: args.limit]}
