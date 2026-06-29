# Personal Curation Bot

Private local-first bot project for Simon.

This is **not** Speedlab OS, not a public SaaS, not a growth-hacking system, and not an auto-poster. Speedlab is using this as a real bounded project to prove the idea → build → QA → handover loop while producing a useful personal tool.

## Current implemented slice

This first slice implements the local app core only:

```text
message/link intake -> category queue -> threshold check -> draft package folder
```

It does **not** yet connect to Telegram, Instagram, Pinterest, or any live account.

## Current hard boundary

Instagram native app drafts are device-local according to Instagram Help. Meta's content publishing API supports publishing workflows for professional accounts, but the official docs do not describe a normal API for creating personal-account native drafts.

So the practical first implementation target is:

- prepare post packages locally, ready for Simon to post manually; or
- later, after a separate Security Mechanic review, test an approved account/device automation lane.

No credentials belong in this repo.

## Run tests

```bash
python3 -m unittest discover -s tests -v
```

## Local demo

```bash
python3 -m curation_bot.cli ingest --category finds --url https://www.instagram.com/p/example1/
python3 -m curation_bot.cli ingest --category finds --url https://www.instagram.com/p/example2/
python3 -m curation_bot.cli status
```

Default storage root is `./data/`.

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
