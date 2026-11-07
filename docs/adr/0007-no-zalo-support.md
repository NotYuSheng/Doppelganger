# ADR 0007: Do not support Zalo as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Zalo is the dominant messenger in Vietnam, with roughly 75M monthly users. We
found **no official export** that produces a readable chat history:

- Zalo PC writes official backup files (`.zdb`, `backup_zalo_*.zl.zip`), but
  they are only for restoring into Zalo and are not documented as readable.
- Readable exports come only from **third-party tools**: scrapers that log in
  as the user by QR code, or tools that read Zalo PC's local message store
  directly.

Neither route meets the "legitimate export path" bar from
[ADR 0003](0003-support-whatsapp.md). Automating a logged-in account is a
ToS risk, and reading the local store relies on internals Zalo does not expose.

## Decision

Doppelganger will **not** support Zalo. There will be no adapter and no
documentation of third-party Zalo exporters.

## Revisit when

Zalo ships an official, readable chat export, or documents its backup format.

## Consequences

- Vietnamese users on Zalo are not covered.
- We avoid building on scrapers or undocumented local storage.

## References

- [Apify: Zalo exporter (third-party scraper)](https://apify.com/kazkn/zalo-member-profile-exporter.md)
- [Zalo PC extractor skill (third-party)](https://skillsmp.com/creators/hbui290/zl-extractor/skill)
