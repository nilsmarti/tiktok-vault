# Partner mood board compatibility

Target database: `// ｍｏｏｄ ｂｏａｒｄ_`, ID `53efcbca55ab4ce9b50cb8340cfc841d`.
Telegram user: `511728563`. Token: host-only `NOTION_TOKEN_PARTNER`.

## Live schema verification (2026-11-11)

The partner profile is enabled on the host after read-only inspection. The title
field is `Name`; `Link` is a URL; `Tags` and `Relationship` are multi-selects;
`Files & media` is files. `Colors`, `fake`, and `Created` are left untouched.
All explicit tags in the sample (`Touch Designer`, `Video`) exist.

Related database access has been granted, making `Projects` visible. The user
removed a trailing space from the sewing project title to match the mapping.
The partner approved omitting `Tech/Setup` from streaming/gaming tags: that
collection now assigns the platform tag, Gaming relationship, and streaming
project, plus any explicitly supplied tags that already exist.

No schema or option changes were made by the app, and no substitute values were selected.
Live shortcut submissions and the structured collection import have been verified.

## Deployment checkpoint (2026-11-12)

Bot, worker, and Redis are deployed on the homelab host. All 51 tests pass, and
read-only preflight verified all 23 collection mappings against the existing board.
The partner's 1,046-post import finished with 1,031 saved and 15 failed; the queue
was empty at the final audit. A private, gitignored text report lists the failures
with source metadata and logged reasons for manual recovery using local downloads.
Source exports, reports, host credentials, and runtime configuration stay out of Git.

Work is paused at this deployed TikTok checkpoint. Future scope is Instagram and X
post support; neither platform is implemented yet.

## What the old workflow establishes

A bookmark contains a source link, one or more explicitly selected collections,
optional explicit tags, and every associated media attachment. All attachments
belong to one bookmark page. Existing tags and relationship/project values must
be reused exactly. Fields not covered by these rules must be left unset.

| Collection (exact) | Tags | Relationship | Projects (existing related page) |
|---|---|---|---|
| beauty/health | | Health | |
| career/education | Education | | |
| vid content ideas | Video, Content Idea | Content Creation | |
| graphics ressources | Resources | Graphic Design | |
| graphics tutorials | Tutorial | Graphic Design | |
| photography+editing | Photography | Photography, Content Creation | // ｐｈｏｔｏｇｒａｐｈｙ_ |
| vid editing | Video | Content Creation | |
| IG content/captions & socials | Content Idea | Content Creation | |
| inspo | Inspo | | |
| fashion inspo | Fashion, Inspo | Fashion | |
| fashion hacks | Fashion | Fashion | // ｓｅｗｉｎｇ_ |
| makeup | Makeup | | |
| hair | Hair | | |
| diy ideas | DIY | | |
| Sewing/customizing | Fashion, DIY | Fashion | // ｓｅｗｉｎｇ_ |
| interior | Interior | | //ｉｎｔｅｒｉｏｒ_ |
| streaming/gaming | | Gaming | // ｓｔｒｅａｍｉｎｇ_ |
| vrchat | | Gaming | |
| japan | Japan | | |
| places | Location | | |
| language | Language, Education | | |
| pets | Pets | | |
| bestie vids | Video, Content Idea | Content Creation | |

Keep spelling, capitalization, full-width Unicode, and spaces exactly as shown.
In particular `graphics ressources` is intentional, and the interior project
has no space after `//`, unlike the other projects.

## Replacement behavior

- Source `Link` points to the canonical TikTok post for duplicate detection.
- Page title: `TikTok - <collection>`. For multiple explicit collections, the
  planner joins their exact names with `, `, preserving input order.
- `Tags` is the deduplicated union of collection tags, `TikTok`, and explicit
  input tags that exactly match existing options. Unknown input tags are
  reported and omitted; missing required collection/platform values are errors.
- AI summaries, transcripts and extracted notes can go in the page body. AI
  generated tags must not flow into the strict property planner as explicit tags.
- `Relationship` must be inspected: it could be select, multi-select, or relation.
  Single-select cannot represent mappings that require two values and must fail
  rather than silently drop one. Relations require exact target-page resolution.
- `Projects` must be a relation. Resolve against its actual target database;
  zero or multiple exact-title matches are errors. Never create a project.
- Every archived attachment gets a permanent public R2 URL, an external entry in
  `Files & media`, and a corresponding video, image, or file body block based on
  the media pipeline's known MIME type. Do not determine type from URL suffixes.
- The original requirement to upload bytes into Notion is superseded by the
  requested R2 workflow. No Cobalt dependency or Notion video upload is needed.
- Do not populate default `Status`, `Kind`, `Archive`, `Saved`, etc. merely because
  they happen to exist. Do not add/rename database properties or create options.
- Instagram and X remain unsupported source platforms. Collections mentioning
  IG are still valid choices for TikTok inspiration.

Notion arrays replace property values; they do not append. The offline planner
only produces a *new page* payload. Updating an existing page would need to fetch
and merge complete existing file/tag/relation arrays first.

## Android shortcut input

The partner profile uses `notion_mode: moodboard`. The bot accepts the shortcut's
plain collection name and numbered tags directly:

```text
selected_collection: vid content ideas

link: https://vm.tiktok.com/ZN8PXSHgU/

tags: [0]: Touch Designer
[1]: Video
```

`attachements_link:` (including its original spelling) can be removed from the
shortcut. If still present it is ignored, never fetched or forwarded to the job.
Tags may also be a JSON string array. Collections may be a plain name, numbered
entries, or a JSON string array. Names are matched exactly. Tags are optional.
One shortcut must contain exactly one source TikTok URL; multiple links are
rejected rather than creating multiple pages. Bare links and unclassified favorites
exports are rejected because they lack collections. Sorted collection JSON imports
are supported: the entire file is validated before queueing, with explicit aliases
for exported folder names. `beauty_health` maps to `beauty/health` (Relationship:
Health); `career_education` maps to `career/education` (Tag: Education), and
`graphics_ressources_websites` reuses `graphics ressources`.
Original records, captions and creator metadata are preserved in the archive;
the creator and original collection also appear in the page body. Local thumbnail
paths are retained as metadata only; fresh thumbnails are generated from media.
Unavailable media produces an error; metadata-only fallback is not enabled.
Uploading a JSON file to the bot starts its import. Local inspection does not.
Standard profiles retain their existing plain-link and favorites-import flows.

Once database access has been verified, add this under `profiles` on the host
(replace the database placeholder; do not enable before credentials are ready):

```yaml
partner:
  telegram_user_id: 511728563
  notion_token: ${NOTION_TOKEN_PARTNER}
  notion_database_id: REPLACE_WITH_EXISTING_DATABASE_ID
  notion_mode: moodboard
  video_mode: external
  focus: "creative inspiration, graphic design, video, fashion, DIY and photography"
```

## Implemented now

`app/moodboard.py` contains all 23 mappings and an offline, deterministic page
planner. It writes only existing select/multi-select IDs and resolved relation
IDs. Tests cover every collection, missing/ambiguous values, multiple collections,
Unicode names, unknown tags, and preserving every attachment in both locations.

`scripts/inspect_moodboard.py` performs read-only schema inspection, exact relation
target lookup, and validates every collection once credentials are available.
It never creates pages, properties, options, or related records.

The parser, job context, and profile-specific worker branch are implemented.
The worker resolves existing relation targets, archives all source media in R2,
and verifies every external attachment in the saved page property and paginated
body before recording success. The partner profile is enabled; missing required
values still return explicit errors. Her mode uses external media and cover URLs;
it does not upload media into Notion. Standard profiles keep their existing path.

If a page already exists for the source URL, it is reused without modifying its
classification. For pages created by this pipeline, saved media metadata is used
to recheck attachments after a partial failure. Missing attachments are reported
with filename and failed location; no replacement page is created. Existing
manually saved rows are left untouched. Automatic attachment repair is not yet
implemented; a failed verification needs inspection of the existing page.

## Setup procedure and remaining verification

1. Get the existing *source database* link/ID and save its integration secret on
   the host. Share the mood board and the databases targeted by its relations
   with that same integration. A linked-view page alone is insufficient.
2. Run the inspector (after copying source and rebuilding the image):

   ```bash
   sudo docker run --rm --env-file /opt/tiktok-vault/.env \
     tiktok-vault-worker python -m scripts.inspect_moodboard --database-id DATABASE_ID
   ```

3. Verify exact property names, title field, available options, relation targets,
   one-versus-multiple data sources, and whether existing Link values are canonical.
   The app currently pins the legacy database API, which needs a single source.
4. Collection selection and message cardinality are settled by the shortcut
   format above. Keep the source `Link` field for retry/deduplication recovery.
5. Enable her profile and test a video and a slideshow. Check property IDs, project
   links, public playback, and deduplication before importing a backlog.

## API references

- [Page properties and array replacement semantics](https://developers.notion.com/reference/page-property-values)
- [Schema types, legacy API and relation targets](https://developers.notion.com/reference/property-object)
