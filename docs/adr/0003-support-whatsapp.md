# ADR 0003: Support WhatsApp via its built-in chat export

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

WhatsApp support shipped in #47 (`ingest/adapters/whatsapp.py`). After ruling out
[WeChat (ADR 0001)](0001-no-wechat-support.md) and
[Discord (ADR 0002)](0002-no-discord-support.md), we checked WhatsApp against
the same criteria. ADR 0002 cites "no official schema" as one reason against
Discord, and WhatsApp's export has no official schema either, so this ADR
explains why WhatsApp is still accepted.

What we found:

1. **Export chat is a first-party feature.** You open a chat → ⋮ → More → Export
   chat on Android, or Export Chat on iPhone. It produces a plain UTF-8 `.txt`
   (iPhone wraps it as `_chat.txt` in a `.zip`). No third-party tool, no
   circumvention, and no ToS or account risk.
2. **It contains both sides of the conversation.** This is what Discord's
   official data package lacks and what training pairs need.
3. **"Request account info" doesn't help.** It returns account metadata only, with
   no message content.
4. **The format is undocumented and varies.** The prefix layout depends on locale
   and platform: date order, separators, 12/24-hour clock, iOS brackets vs the
   Android dash, and some locales spell out day periods. The export has no
   message ids, no reply links and no marker of which participant is "you".
5. **Exports are capped.** Third-party sources report a cap of roughly the most
   recent 40,000 messages without media (fewer with media), with no truncation
   notice in the file. We could not find WhatsApp's own figure.

## Decision

Keep supporting WhatsApp through its built-in **Export chat** `.txt`.

What separates it from Discord is **legitimacy, not schema stability**. The
platform's own export gives us both sides of the conversation. Discord's
complete export is only available through a third-party tool whose main use
(exporting DMs) breaks Discord's ToS, and WeChat requires decrypting the local
database. WhatsApp's undocumented format is a maintenance cost, which we accept
and absorb in the adapter: tolerant prefix regexes, date order inferred per
file, and a required `--self-name`.

Our bar for a chat source is therefore:

1. The export path is **official or otherwise legitimate**: no circumvention and
   no ToS breach, **and**
2. it contains **both sides** of a conversation.

A documented schema is preferred but not required when both conditions hold.
ADR 0002 rejected Discord mainly because it fails condition 1 for DMs and
condition 2 for the official package. Its lack of a schema made that worse but
was not the deciding reason.

## Consequences

- Locale-format breakage is expected over time. The adapter treats unparseable
  prefixes as continuation lines rather than crashing, and new variants get
  fixed as they are reported.
- Grouping falls back to time-based sessionizing, since the export has no reply
  metadata.
- Long chats may be silently truncated by WhatsApp's export cap. Users with very
  long histories should know this. Docs can mention it, but nothing in the file
  lets us detect it.
- WhatsApp export files (`WhatsApp Chat*`, `_chat.txt`, `*-WA0001.*`-style
  media) are covered by `.gitignore` and the pre-commit hook, the same as
  Telegram exports.

## References

- [`docs/sources/whatsapp.md`](../sources/whatsapp.md)
- [WhatsApp export timestamp formats (ThreadRecap)](https://www.threadrecap.com/en/blog/whatsapp-export-timestamp-formats)
- [What's inside a WhatsApp chat export (ThreadRecap)](https://www.threadrecap.com/en/blog/anatomy-whatsapp-chat-export)
- [Export more than 40,000 WhatsApp messages (PrintChat)](https://printchat.app/en/blog/export-more-than-40000-whatsapp-messages)
- [WhatsApp account info report: what it shows (dev.to)](https://dev.to/dipbhi/whatsapp-account-info-report-sample-what-it-shows-and-what-it-hides-31ie)
