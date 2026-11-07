# Reply drills: creating training data on purpose

Chat exports only contain the conversations that happened to take place. They
are mostly logistics and banter with a few people, and they're missing the
situations you'd most want a doppelganger to handle: giving advice, disagreeing,
apologising, explaining your work, reacting to news. **Reply drills** let you
fill those gaps deliberately.

A drill shows a realistic incoming message, and you type how you'd actually
reply:

```
[3/10] You let them down
A close friend messages you:
  │ kinda hurt you didn't show up yesterday tbh
you › ah man I'm really sorry
    › work ran way late and my phone died, no excuse though
    ›
```

Each answer becomes a clean training pair: a real message to reply to, your own
words, and nobody else's data.

## Running drills

```bash
python -m doppelganger                    # menu → drills
python -m doppelganger drills             # asks how comprehensive (below)
python -m doppelganger drills --level quick
python -m doppelganger drills --count 10 --situation conflict
python -m doppelganger drills --report    # what you've covered so far
```

**How comprehensive?** You choose a coverage level up front and see roughly
how long it will take (about 30 seconds per reply):

| Level | Goal | From scratch |
|---|---|---|
| **Quick** | every situation once | 12 messages, ~6 min |
| **Standard** | every situation twice, mixed audiences | 24, ~12 min |
| **Thorough** | every situation for every audience | 48, ~24 min |
| **Complete** | every message in the library | 96, ~48 min |

Levels build on each other and count what you've already answered. After a
Quick session, Standard shows "12 to go" and Quick shows "done ✓". Drills are
served one situation after another, so stopping early still leaves your
coverage balanced, and each prompt shows roughly how much time is left.

- **Typing:** each line you type is a separate message, like texting. Press
  Enter on an empty line to send.
- **Skipping and stopping:** an empty first line skips a drill, and `/stop`
  ends the session.
- **Saving:** every answer is saved immediately to `data/elicited.jsonl`, so
  you can stop at any point and come back later. Nothing is lost.

## What gets asked

The [scenario library](../ingest/drills/scenarios.json) covers 12 situations
across 4 audiences (a close friend, family, a colleague, someone you don't know
well), with 96 messages in all:

| Situations | |
|---|---|
| Asking for advice · Making plans · Asking for something you'd turn down · You let them down | |
| Disagreement · Good news · Bad news · Asking you to explain | |
| Asking your opinion · Asking for a recommendation · Catching up · Asking for feedback | |

**Coverage-driven:** levels fill the biggest gaps first, and `--count` serves
the situations you've answered least, spread across audiences. Messages you've
already answered aren't repeated until you've done them all. `--report` shows a
situation × audience table and lists what's not covered yet.

## Using your answers

Nothing extra to do. `parse` and `auto` in the menu find `data/elicited.jsonl`
like any other export, so it's ticked in the export checklist alongside your
chats. On the command line it's a source like the others:

```bash
python -m ingest --input elicited:data/elicited.jsonl
python -m ingest --input telegram:data/result.json --input elicited:data/elicited.jsonl
```

Your answers can still contain personal details you typed, so they go through
the same sensitive-data scan as chat exports. `data/elicited.jsonl` is
gitignored and blocked by the pre-commit hook.

## Tips

- **Reply as you really would** to that person, not as you think you should.
  Short, messy, emoji: whatever is actually you.
- **Start with Quick.** Six minutes gives one answer for every situation, then
  build up to Standard or Thorough over a few sessions. Coverage matters more
  than volume.
- **Drills complement chat history.** Real chats teach the model your everyday
  rhythm; drills teach the situations your chats are missing.

This is phase 1 of [#14](https://github.com/NotYuSheng/Doppelganger/issues/14).
Phase 2 is the [persona interview](interview.md); still to come are pick-or-fix
(once a model is trained) and a style card.
