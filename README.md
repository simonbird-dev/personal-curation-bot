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
