# ADR 0002: Do not support Discord as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Discord was listed as a planned chat source. Following
[ADR 0001](0001-no-wechat-support.md), we looked at how Discord chat history can
be exported and whether a stable, legitimate input exists for an adapter.

What we found:

1. **Discord's official data package contains only your own messages.** It is a
   legitimate, self-serve export (Settings → Data & Privacy → Request all of my
   data), but Discord's help article says the `messages/` folder contains "all
   the messages that you have sent on Discord". Other people's messages are not
   included. Doppelganger needs the other side of each conversation as the prompt
   in a training pair, so this package cannot produce usable data on its own.
2. **The only complete exporter is third-party.** DiscordChatExporter (DCE) exports
   both sides of channels and DMs as JSON. It is open source and actively
   maintained, but **its JSON format has no official, published schema**. Field
   names are known only from third-party parsers and a community-written spec,
   and could change in any release without notice.
3. **Exporting DMs with DCE breaks Discord's ToS.** DMs can only be exported with a
   user token, which counts as self-botting under Discord's Terms of Service.
   DCE's own community says so, and some accounts have been banned. The
   ToS-compliant bot-token route covers only server channels where a bot can be
   added, and cannot export DMs at all. DMs are where most personal conversation
   lives.
4. **Discord offers no official API for exporting a user's own message history.**

Unlike WeChat, supporting DCE output would put the repository at no legal risk.
The problem is that we would be building on an undocumented third-party format
whose main real-world use breaks the platform's ToS.

## Decision

Doppelganger will **not** support Discord for now. Specifically:

- No Discord adapter in `ingest/adapters/`, either for the official data package
  or for DiscordChatExporter output.
- No documentation that walks users through exporting Discord history with user
  tokens.

## Revisit when

We will reconsider once one of these is true:

- Discord's official data package (or another official export) includes both
  sides of a conversation, or
- a ToS-compliant exporter that covers DMs publishes a stable, versioned
  output schema.

## Consequences

- Discord users cannot use Doppelganger with their Discord history.
- We do not maintain a parser for an unversioned format we don't control.
- We do not point users toward an export method that risks their account.
- The README and pipeline docs no longer list Discord as planned.

## References

- [Discord: Your Discord Data Package](https://support.discord.com/hc/en-us/articles/360004957991-Your-Discord-Data-Package)
- [DiscordChatExporter discussion #889: ToS violation, yes or no?](https://github.com/Tyrrrz/DiscordChatExporter/discussions/889)
- [DiscordChatExporter discussion #1338: Do I need a bot account?](https://github.com/Tyrrrz/DiscordChatExporter/discussions/1338)
- [DiscordChatExporter issue #1508: Potential account ban](https://github.com/Tyrrrz/DiscordChatExporter/issues/1508)
- [Community DCE JSON spec (unofficial)](https://github-users.yhajoa.workers.dev/Roachbones/discordless/blob/main/dcejson_spec.md)
