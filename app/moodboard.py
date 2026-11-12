"""Strict compatibility planner for the existing mood board."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .notion import media_block, rt

DATABASE_NAME = "// ｍｏｏｄ ｂｏａｒｄ_"
TELEGRAM_USER_ID = 511728563


@dataclass(frozen=True)
class Collection:
    tags: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()


COLLECTIONS = {
    "beauty/health": Collection((), ("Health",)),
    "career/education": Collection(("Education",)),
    "vid content ideas": Collection(("Video", "Content Idea"), ("Content Creation",)),
    "graphics ressources": Collection(("Resources",), ("Graphic Design",)),
    "graphics tutorials": Collection(("Tutorial",), ("Graphic Design",)),
    "photography+editing": Collection(("Photography",), ("Photography", "Content Creation"),
                                      ("// ｐｈｏｔｏｇｒａｐｈｙ_",)),
    "vid editing": Collection(("Video",), ("Content Creation",)),
    "IG content/captions & socials": Collection(("Content Idea",), ("Content Creation",)),
    "inspo": Collection(("Inspo",)),
    "fashion inspo": Collection(("Fashion", "Inspo"), ("Fashion",)),
    "fashion hacks": Collection(("Fashion",), ("Fashion",), ("// ｓｅｗｉｎｇ_",)),
    "makeup": Collection(("Makeup",)),
    "hair": Collection(("Hair",)),
    "diy ideas": Collection(("DIY",)),
    "Sewing/customizing": Collection(("Fashion", "DIY"), ("Fashion",), ("// ｓｅｗｉｎｇ_",)),
    "interior": Collection(("Interior",), (), ("//ｉｎｔｅｒｉｏｒ_",)),
    "streaming/gaming": Collection((), ("Gaming",), ("// ｓｔｒｅａｍｉｎｇ_",)),
    "vrchat": Collection((), ("Gaming",)),
    "japan": Collection(("Japan",)),
    "places": Collection(("Location",)),
    "language": Collection(("Language", "Education")),
    "pets": Collection(("Pets",)),
    "bestie vids": Collection(("Video", "Content Idea"), ("Content Creation",)),
}


@dataclass(frozen=True)
class Attachment:
    """A known archived object; MIME type comes from the media pipeline."""

    url: str
    filename: str
    content_type: str


def _unique(values):
    return list(dict.fromkeys(values))


async def resolve_relations(nc, schema: dict, collections: list[str]) -> dict:
    if not collections or any(name not in COLLECTIONS for name in collections):
        raise ValueError("Choose existing collection names exactly")
    resolved = {}
    for field, attr in (("Relationship", "relationships"), ("Projects", "projects")):
        names = _unique([v for name in collections for v in getattr(COLLECTIONS[name], attr)])
        if names and schema.get(field, {}).get("type") == "relation":
            resolved[field] = await nc.relation_pages(schema, field, names)
    return resolved


def _existing_values(schema: dict, field: str, names: list[str], relations: dict) -> dict:
    prop = schema.get(field, {})
    kind = prop.get("type")
    if kind in ("select", "multi_select"):
        options = {o["name"]: o["id"] for o in prop[kind]["options"]}
    elif kind == "relation":
        # Caller must resolve these from the property's actual target database.
        # Values are lists so duplicate titles can never pick an arbitrary page.
        options = {}
        for name in names:
            ids = relations.get(field, {}).get(name, [])
            if len(ids) != 1:
                raise ValueError(f"{field}: expected exactly one existing page named {name!r}")
            options[name] = ids[0]
    else:
        raise ValueError(f"{field}: unsupported or missing property type {kind!r}")
    missing = [name for name in names if name not in options]
    if missing:
        raise ValueError(f"{field}: missing existing values {missing!r}")
    values = [{"id": options[name]} for name in names]
    if kind == "select":
        if len(values) != 1:
            raise ValueError(f"{field}: select cannot hold multiple required values")
        return {kind: values[0]}
    return {kind: values}


def plan_bookmark(*, collections: list[str], link: str, tags: list[str],
                  attachments: list[Attachment], schema: dict,
                  relations: dict[str, dict[str, list[str]]] | None = None) -> dict:
    """Build one new page's properties/body without making any API requests.

    Only TikTok source links are supported. Explicit extra tags absent from the
    schema are reported and omitted. Missing required mapping values are errors.
    The returned properties are for creation, never for replacing an existing row.
    """
    source = urlsplit(link)
    host = (source.hostname or "").lower()
    if source.scheme != "https" or not any(
        host == domain or host.endswith("." + domain)
        for domain in ("tiktok.com", "tiktokv.com")
    ):
        raise ValueError("Only HTTPS TikTok source links are supported")
    chosen = _unique(collections)
    if not chosen or any(name not in COLLECTIONS for name in chosen):
        raise ValueError("Choose existing collection names exactly")
    title_fields = [name for name, prop in schema.items() if prop["type"] == "title"]
    if len(title_fields) != 1:
        raise ValueError("Expected one existing title property")
    if schema.get("Link", {}).get("type") != "url":
        raise ValueError("Link: expected an existing URL property")
    if schema.get("Tags", {}).get("type") != "multi_select":
        raise ValueError("Tags: expected an existing multi-select property")
    required_tags = _unique([t for name in chosen for t in COLLECTIONS[name].tags] + ["TikTok"])
    options = {o["name"] for o in schema["Tags"]["multi_select"]["options"]}
    unknown_tags = _unique([tag for tag in tags if tag not in options])
    all_tags = _unique(required_tags + [tag for tag in tags if tag in options])
    title = "TikTok - " + ", ".join(chosen)
    if len(title) > 1900:
        raise ValueError("Collection title exceeds supported length")
    properties = {
        title_fields[0]: {"title": rt(title)},
        "Link": {"url": link},
        "Tags": _existing_values(schema, "Tags", all_tags, {}),
    }
    for field, attr in (("Relationship", "relationships"), ("Projects", "projects")):
        names = _unique([v for name in chosen for v in getattr(COLLECTIONS[name], attr)])
        if names:
            if field == "Projects" and schema.get(field, {}).get("type") != "relation":
                raise ValueError("Projects: expected an existing relation property")
            properties[field] = _existing_values(schema, field, names, relations or {})
    blocks, files = [], []
    if attachments and schema.get("Files & media", {}).get("type") != "files":
        raise ValueError("Files & media: expected an existing files property")
    if len(attachments) > 100:
        raise ValueError("Files & media: more than 100 attachments require a different workflow")
    for item in attachments:
        url = urlsplit(item.url)
        if url.scheme != "https" or not url.hostname:
            raise ValueError(f"Attachment {item.filename!r}: expected a public HTTPS object URL")
        kind = "video" if item.content_type.startswith("video/") else (
            "image" if item.content_type.startswith("image/") else "file")
        blocks.append(media_block(kind, url=item.url))
        files.append({"name": item.filename, "type": "external", "external": {"url": item.url}})
    if files:
        properties["Files & media"] = {"files": files}
    return {"properties": properties, "children": blocks, "ignored_tags": unknown_tags}
