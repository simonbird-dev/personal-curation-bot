# Personal Curation Bot

Private local-first bot project for Simon.

This is **not** Speedlab OS, not a public SaaS, not a growth-hacking system, and not an auto-poster. Speedlab is using this as a real bounded project to prove the idea → build → QA → handover loop while producing a useful personal tool.

## Current implemented slice

This slice now implements the local app core plus a tested Telegram-style intake adapter:

```text
Telegram-style message/link text -> stream/category queue -> threshold check -> draft package folder
```

It does **not** yet connect to a dedicated live Telegram bot token. The transport is deliberately separated so the parser/state rules can be used now from the CLI and then wired to a dedicated bot chat later without changing the storage model.

## Current hard boundary

Instagram native app drafts are device-local according to Instagram Help. Meta's content publishing API supports publishing workflows for professional accounts, but the official docs do not describe a normal API for creating personal-account native drafts.

So the practical first implementation target is:

- prepare post packages locally, ready for Simon to post manually; or
- later, after a separate Security Mechanic review, test an approved account/device automation lane.

No credentials belong in this repo.

## Run tests

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Local Telegram-style demo

```bash
. .venv/bin/activate
curation-bot --data-root ./data telegram-message /finds /house slide 5 https://www.instagram.com/p/example1/
curation-bot --data-root ./data telegram-message /live https://www.instagram.com/reel/example2/
curation-bot --data-root ./data telegram-message /mixes https://soundcloud.com/example/set
curation-bot --data-root ./data telegram-message /fashion https://pin.it/example
curation-bot --data-root ./data telegram-message /status
```

The command returns the exact reply text the future dedicated Telegram transport should send back to Simon. Production stream batches are set to 8 items for `/finds/<dynamic-genre>`, `/live`, and `/fashion`; `/mixes` batches at 5. `/finds` genres are not predefined: any slash genre such as `/house`, `/techno`, or `/deep-tribal` creates and files into its own queue. When text says `slide N`, Telegram intake preserves that as selected-media intent because the Instagram URL is still only a post/carousel link; a later capture step must extract the actual single tile before draft automation. Tests use separate small fixture configs so the real defaults do not get dragged back to proof-mode thresholds.

## Local demo

```bash
python3 -m curation_bot.cli ingest --category finds --url https://www.instagram.com/p/example1/
python3 -m curation_bot.cli ingest --category finds --url https://www.instagram.com/p/example2/
python3 -m curation_bot.cli status
```

Default storage root is `./data/`.

## Selected-media capture without Instagram login

The no-login pipeline can now turn an existing Apify `detailedData` dataset into a sanitised capture record and queue that selected media into the bot.

This command **does not log into Instagram, open a browser, post, download media, or call Apify live**. It only uses a dataset JSON file that already exists on the VM.

```bash
PYTHONPATH=src python3 -m curation_bot.cli --data-root /tmp/curation-demo capture-apify \
  --category finds \
  --dataset /path/to/apify-detailedData-dataset.json \
  --source-url 'https://www.instagram.com/p/ABC123/?img_index=2' \
  --selected-slide 2 \
  --stream /finds
```

Outputs:

- `capture_records/<category>/<shortcode>-slideN.json` — sanitised metadata contract, with raw media URLs redacted.
- `queues/<category>/*.json` — queued bot item referencing the capture record.
- `draft_packages/<category>/<package-id>/media_manifest.json` — selected-media storage contract.
- A draft package is still created automatically when the category threshold is reached.

## Media download executor boundary

The package media executor now refuses to run unless an explicit approved provider is supplied. The only supported provider in this slice is `local-fixture`, which copies a local test file into the package media path and marks that selected media item as downloaded.

This command **does not call Apify live, use raw media URLs, log into Instagram, open a browser, or download anything from the internet**.

```bash
PYTHONPATH=src python3 -m curation_bot.cli execute-media-download \
  --package /tmp/curation-demo/draft_packages/finds/PACKAGE_ID \
  --provider local-fixture \
  --fixture-file /path/to/local-test-file.mp4 \
  --selected-shortcode CHILD2
```

For multi-item packages, `--selected-shortcode` is required so one fixture file cannot silently be copied to the wrong selected slide.

## Package readiness check

Before any Instagram/browser automation, check whether the package is genuinely ready:

```bash
PYTHONPATH=src python3 -m curation_bot.cli check-package-readiness \
  --package /tmp/curation-demo/draft_packages/finds/PACKAGE_ID
```

The command reports whether all selected media files exist under `package/media/`, lists blockers such as missing files or unsafe media paths, and returns the safe next step. It performs no browser, account, provider, or download action.

## Manual review pack

For a no-account-risk handoff, build a local review pack from any draft package:

```bash
PYTHONPATH=src python3 -m curation_bot.cli build-manual-review-pack \
  --package /tmp/curation-demo/draft_packages/finds/PACKAGE_ID
```

Outputs under `draft_package/manual_review/`:

- `caption.txt` — default caption draft from local package metadata.
- `media_checklist.json` — readiness state, blockers, warnings, and selected-media paths.
- `manual_review_pack.md` — human-readable review/checklist artefact.

This command performs no Instagram login, browser automation, Apify live call, external media download, or publish/share action.

## Instagram account switching

The bot separates saved content/category state from Instagram login state.

- Content/category queues live under the chosen bot data root, usually `./data/`.
- Instagram browser sessions live under `.runtime/instagram-accounts/<account-id>/browser-profile/`.
- The active Instagram account pointer lives under `.runtime/active-instagram-account.json`.

That means you can test with one Instagram account now and change the connected account later without deleting saved content/categories.

Commands:

```bash
# choose or switch the active local Instagram account profile
PYTHONPATH=src python -m curation_bot.instagram_accounts_cli set-active --account-id test

# log into that account profile from the VM terminal
PYTHONPATH=src python -m curation_bot.instagram_login terminal-login

# later, switch to another account profile without touching content queues
PYTHONPATH=src python -m curation_bot.instagram_accounts_cli set-active --account-id main
PYTHONPATH=src python -m curation_bot.instagram_login terminal-login
```

## Instagram draft preparation spike

The live browser automation command uploads prepared media into Instagram web and stops before the final Share/Post action.

Before it opens Instagram, `prepare-draft` runs the package readiness gate. It refuses incomplete packages until `check-package-readiness` reports ready, so browser automation cannot silently upload the wrong slide, a missing file, or an unfulfilled media plan.

Safety boundary: it may click through upload/crop/filter screens, but it must not click final Share/Post.

```bash
# check whether the active browser profile is logged in
PYTHONPATH=src python -m curation_bot.instagram_automation check-session

# prepare a draft from a package and explicit media file, stopping before final Share/Post
PYTHONPATH=src python -m curation_bot.instagram_automation prepare-draft \
  /path/to/draft_package \
  --media /path/to/image-or-video \
  --caption "Test draft from personal curation bot"
```

A package can also include media files under `package/media/`; then `--media` is optional.
