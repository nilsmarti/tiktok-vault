"""Create the TikTok Vault database under a Notion page.

Usage (inside the container or any env with httpx):
  python scripts/setup_notion_db.py --token ntn_xxx --parent-page <page-id-or-url> [--title "TikTok Vault"]

The integration must already be connected to the parent page (page ••• → Connections).
"""
from __future__ import annotations

import argparse
import re
import sys

import httpx

KINDS = ("place", "recipe", "product", "inspiration", "tutorial", "other")


def page_id(s: str) -> str:
    m = re.search(r"([0-9a-f]{32})", s.replace("-", ""))
    if not m:
        sys.exit("Could not find a 32-char page id in --parent-page")
    return m.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--parent-page", required=True)
    ap.add_argument("--title", default="TikTok Vault")
    a = ap.parse_args()

    props = {
        "Name": {"title": {}},
        "URL": {"url": {}},
        "Creator": {"rich_text": {}},
        "Kind": {"select": {"options": [{"name": k} for k in KINDS]}},
        "Tags": {"multi_select": {"options": []}},
        "Summary": {"rich_text": {}},
        "Places": {"rich_text": {}},
        "Map": {"url": {}},
        "Posted": {"date": {}},
        "Saved": {"date": {}},
        "Status": {"select": {"options": [{"name": "Inbox", "color": "blue"},
                                          {"name": "Reviewed", "color": "green"},
                                          {"name": "Done", "color": "gray"}]}},
        "Format": {"select": {"options": [{"name": "Video"}, {"name": "Slideshow"}]}},
        "Confidence": {"number": {"format": "number"}},
        "Archive": {"rich_text": {}},
    }
    r = httpx.post(
        "https://api.notion.com/v1/databases",
        headers={"Authorization": f"Bearer {a.token}", "Notion-Version": "2022-06-28"},
        json={"parent": {"type": "page_id", "page_id": page_id(a.parent_page)},
              "title": [{"type": "text", "text": {"content": a.title}}],
              "properties": props},
        timeout=30,
    )
    if r.is_error:
        sys.exit(f"{r.status_code}: {r.text}")
    db = r.json()
    print(f"Created: {db['url']}\nnotion_database_id: {db['id'].replace('-', '')}")
    print("Tip: add a Gallery view with card preview = Page cover for browsing.")


if __name__ == "__main__":
    main()
