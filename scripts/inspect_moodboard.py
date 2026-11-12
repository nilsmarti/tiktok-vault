"""Read-only preflight for the partner's existing database.

Run on the host with the partner token supplied through the container env file:
python -m scripts.inspect_moodboard --database-id <database ID>
Never prints tokens; never creates or modifies pages or schema.
"""
from __future__ import annotations

import argparse
import asyncio
import os

import httpx

from app.moodboard import COLLECTIONS, DATABASE_NAME, plan_bookmark
from app.notion import Notion


async def inspect(database_id: str) -> int:
    token = os.environ.get("NOTION_TOKEN_PARTNER")
    if not token:
        print("Missing NOTION_TOKEN_PARTNER in the host environment file.")
        return 1
    async with httpx.AsyncClient() as http:
        nc = Notion(http, token)
        database = await nc._req("GET", f"/databases/{database_id}")
        title = "".join(t.get("plain_text", t.get("text", {}).get("content", ""))
                        for t in database.get("title", []))
        print(f"Database: {title!r}")
        if title != DATABASE_NAME:
            print(f"Expected exact database name {DATABASE_NAME!r}; stopping.")
            return 1
        schema = database["properties"]
        relations = {}
        for field, prop in schema.items():
            kind = prop["type"]
            print(f"Property: {field!r} ({kind})")
            if kind in ("select", "multi_select"):
                print("  Existing options:", [o["name"] for o in prop[kind]["options"]])
        for field, attr in (("Relationship", "relationships"), ("Projects", "projects")):
            prop = schema.get(field, {})
            if prop.get("type") != "relation":
                continue
            target = prop["relation"].get("database_id")
            if not target:
                print(f"{field}: no accessible legacy relation target; check sharing/API version.")
                return 1
            target_schema = await nc.database_schema(target)
            title_field = next(n for n, p in target_schema.items() if p["type"] == "title")
            relations[field] = {}
            names = sorted({name for c in COLLECTIONS.values() for name in getattr(c, attr)})
            for name in names:
                ids, cursor = [], None
                while True:
                    body = {"filter": {"property": title_field, "title": {"equals": name}},
                            "page_size": 100}
                    if cursor:
                        body["start_cursor"] = cursor
                    result = await nc._req("POST", f"/databases/{target}/query", json=body)
                    for page in result["results"]:
                        actual = "".join(t.get("plain_text", t.get("text", {}).get("content", ""))
                                         for t in page["properties"][title_field]["title"])
                        if actual == name:
                            ids.append(page["id"])
                    if not result.get("has_more"):
                        break
                    cursor = result["next_cursor"]
                relations[field][name] = ids
                print(f"{field}: {name!r}: {len(ids)} exact matching pages")
        failures = 0
        if schema.get("Files & media", {}).get("type") != "files":
            print("FAIL Files & media: expected an existing files property")
            failures += 1
        for name in COLLECTIONS:
            try:
                plan_bookmark(collections=[name], link="https://www.tiktok.com/@preflight/video/0",
                              tags=[], attachments=[], schema=schema, relations=relations)
            except ValueError as exc:
                print(f"FAIL {name!r}: {exc}")
                failures += 1
            else:
                print(f"OK {name!r}")
        print("Read-only preflight complete. Failures:", failures)
        return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-id", required=True)
    args = parser.parse_args()
    try:
        code = asyncio.run(inspect(args.database_id.replace("-", "")))
    except Exception as exc:
        # The Notion client can include API response text in exceptions. Keep
        # operational failures concise without printing potentially private data.
        print(f"Preflight could not complete ({type(exc).__name__}); check access and database ID.")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
