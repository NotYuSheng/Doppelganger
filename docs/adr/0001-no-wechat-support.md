# ADR 0001: Do not support WeChat as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Ingestion is source-agnostic: each platform gets an adapter under
`ingest/adapters/` that turns the platform's export into `NormalizedMessage`s.
Users asked about adding WeChat, so we looked at how WeChat chat history can be
exported.

What we found:

1. **WeChat has no official chat export.** The in-app "Export Personal Data" tool
   covers Moments, Favorites and account information only. WeChat's FAQ says chat
   history cannot be exported because it is not stored on WeChat servers. GDPR
   access requests do not return message content either.
2. **The built-in "Back up on PC" is not usable.** It writes a proprietary backup
   that only the WeChat client can restore, and third-party tools cannot read it.
3. **The only data source is the encrypted local database.** WeChat 4.x keeps
   messages in encrypted SQLite (SQLCipher-style) files. Every community exporter
   first pulls the database key out of the running client, usually by reading
   process memory, sometimes needing admin rights or re-signing the app, and then
   decrypts the files.
4. **Tencent is taking these tools down.** Since July 2025, Tencent has sent
   GitHub DMCA notices against 30+ WeChat export and decryption repositories,
   claiming they circumvent its encryption (DMCA §1201). More WeChat-related
   notices followed in September and October 2026. Well-known projects such as
   `ylytdeng/wechat-decrypt` now return HTTP 451, and at least one surviving
   exporter has paused automatic key extraction for this reason.
5. **The decrypted schema is undocumented and unstable.** Community write-ups
   disagree on details such as table naming and the encryption scheme, and the
   schema changes between client versions.

## Decision

Doppelganger will **not** support WeChat for now. Specifically:

- No WeChat adapter in `ingest/adapters/`.
- No code that extracts keys, decrypts, or reads WeChat's local databases.
- No documentation that links to or explains third-party WeChat decryption tools.

## Revisit when

We will reconsider once a **legitimate export path** exists, meaning one of:

- WeChat/Tencent ships an official chat-history export in a readable format, or
- a WeChat exporter becomes available that Tencent sanctions or does not
  dispute and that needs no circumvention of client encryption.

At that point a WeChat adapter would parse only that export's output, the same
way the Telegram adapter parses `result.json`.

## Consequences

- WeChat users, a large share of Chinese-speaking users, cannot use Doppelganger
  with their WeChat history.
- The repository stays clear of DMCA takedown risk, which could otherwise affect
  the whole project rather than just one adapter.
- We avoid maintaining a parser for an undocumented, frequently changing schema.
- When asked, maintainers can point to this ADR instead of re-investigating.

## References

- [WeChat FAQ for Data Export](https://cs.help.wechat.com/hc/en-us/articles/11916362921871-FAQ-for-Data-Export)
- [GitHub DMCA notice: Tencent, 2026-01-08](https://github.com/github/dmca/blob/master/2026/01/2026-01-08-tencent.md)
- Further WeChat DMCA notices: [#21587](https://github.com/github/dmca/pull/21587),
  [#21588](https://github.com/github/dmca/pull/21588),
  [#21847](https://github.com/github/dmca/pull/21847)
- [noyb WeChat data access complaint (2025)](https://noyb.eu/sites/default/files/2025-07/WeChat_data%20access_complaint_EN_redacted.pdf)
