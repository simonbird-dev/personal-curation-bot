# Instagram Device Automation Lane

Status: approved direction from Simon, not yet credentialed or live-tested.

## Product target

Simon wants the private Telegram bot to do the hassle work:

```text
Telegram link -> extract selected media -> add to category queue -> when target count reached -> create Instagram draft(s) ready for Simon to review/post
```

This project intentionally does **not** use Meta's official publishing API as the preferred path.

## Non-negotiable scope

- Personal/private tool for Simon only.
- Not Speedlab OS core.
- Not public SaaS.
- Not growth automation.
- Not auto-engagement.
- Not auto-posting without Simon review.

## Automation path

The chosen route is device/browser automation logged into Simon's Instagram account from the VM or an approved browser session.

Likely implementation shape:

1. A locked local browser profile dedicated to this bot.
2. Simon logs into Instagram manually once in that browser/profile.
3. Bot reuses that local session/profile; it must not know or store the password directly.
4. Bot uploads prepared media/caption through Instagram web UI.
5. Bot stops at the final review/draft point rather than pressing final Share/Post.
6. Simon manually reviews and posts.

## Credential rule

Do not put Instagram username, password, cookies, 2FA codes, recovery codes, or session dumps into:

- source files
- markdown docs
- Git commits
- logs
- Telegram messages
- test fixtures

If the VM/browser session stores cookies, that is treated as sensitive local runtime state and must stay outside Git.

## Immediate technical unknowns to test

- Whether Instagram web UI allows the desired draft/save stopping point from the VM browser.
- Whether draft state persists in the same VM browser/session.
- Whether video/image upload is reliable through browser automation.
- Whether Instagram flags VM automation or requires frequent re-auth/2FA.
- Whether we can stop at a safe "ready for Simon" point without accidentally publishing.

## First live automation spike

The first spike should use one harmless test media item and a non-sensitive caption.

Success criteria:

- browser profile opens Instagram logged in or asks Simon to log in manually;
- upload flow reaches the final pre-share screen or a save-draft state;
- bot does **not** click Share/Post;
- no credentials are printed or committed;
- screenshots/logs avoid private feed/DM exposure where possible.

Stop conditions:

- Instagram asks for password/2FA and Simon is not present;
- automation is about to publish;
- account checkpoint/risk warning appears;
- UI path is unstable or ambiguous;
- private DMs/feed/personal data would be captured in logs/screenshots.
