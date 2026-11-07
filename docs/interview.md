# Persona interview: telling the model who you are

Fine-tuning on your messages teaches the model **how** you write: your rhythm,
slang and punctuation. It doesn't give it a clear picture of **who** you are:
what you do, who the important people in your life are, what you're into and
what you care about. Those are rarely spelled out in chats.

The persona interview captures that as a **persona card**: a short profile of
you that sits in the model's system prompt for every reply. It's a conversation
about you, modelled on the life-story interviews used in research. Agents built from two-hour
interview transcripts matched people's own survey answers 85% as well as the
people did themselves two weeks later ([Park et al.
2024](https://arxiv.org/abs/2411.10109)).

> **A profile, not a memory.** The card is deliberately small and static: a few
> paragraphs about who you are, the same for every reply. It doesn't let the
> model look up specific past conversations, events or details from your
> message history. That's retrieval-augmented memory, explored separately in
> [#16](https://github.com/NotYuSheng/Doppelganger/issues/16).

```bash
python -m doppelganger interview          # or: menu → interview
```

## What it asks

Nine topics, one after another:

1. **About you**: name, how you'd describe yourself
2. **Work or study**: what your days are like
3. **The people in your life**: who you talk to most, and how
4. **Where you live and your week**
5. **Interests**: hobbies, shows, music, food
6. **Opinions**: what you feel strongly about
7. **What matters to you**: values, goals
8. **How you text**: emoji, slang, catchphrases
9. **A recent story**: mostly so there's an answer in your natural
   storytelling voice as training data. The card doesn't list events.

**How in-depth?** You choose up front, with a time estimate (about a minute
per question):

| Depth | | Time |
|---|---|---|
| **Short** | one question per topic | ~9 min |
| **Standard** | with follow-up questions | ~27 min |
| **Deep** | more follow-ups, more detail | ~45 min |

**Follow-ups:** with a local LLM configured (`LLM_API_BASE_URL` + `LLM_MODEL`
in `.env`), follow-up questions react to your answers and dig for specifics
and stories, and the interviewer moves on when a topic is covered. Without one,
it asks fixed follow-up questions. Your answers are personal, so a hosted LLM
is refused unless you pass `--allow-cloud`.

**Answering:** write as much or as little as you like. Press Enter on an empty
line to send. `/skip` skips a question, `/next` moves to the next topic, and
`/stop` ends the interview. Everything is saved as you go; run it again to
continue from the next topic.

## What you get

**1. A persona card** (`data/persona.md`): a short profile of who you are,
covering background, work, the people in your life (and how you talk to each),
your week, interests, opinions, values, and a **How you text** section. That section
combines what you said with habits measured from your own answers: reply
length, lowercase, full stops, emoji, lol/haha. With an LLM, the card is
written from your transcript, and the prompt forbids inventing anything you
didn't say. Without one, it lists your answers by topic. Edit it freely; it's
plain Markdown.

- `chat` uses it as the system prompt automatically (`--no-persona` to turn
  that off).
- Rebuild it any time from your answers with `interview --card`.

**2. Training data.** Your answers become training conversations in your own
words: each topic's opening question, follow-ups and answers form one
conversation, so a follow-up like "How did you get into that?" keeps the answer
it refers to. They're saved to `data/elicited.jsonl` next to your [reply
drills](drills.md). The next `parse` / `auto` picks them up automatically.

## Does it help? Measure it

With an [evaluation test set](evaluation.md), compare the model with and
without the card:

```bash
python -m doppelganger serve                                   # leave running
python -m doppelganger eval generate --label finetuned
python -m doppelganger eval generate --label persona --persona  # same model + persona card
python -m doppelganger eval report
```

## Privacy

The interview is about you, so `data/elicited.jsonl` and `data/persona.md` are
gitignored and blocked by the pre-commit hook, and answers only go to an LLM you
configured (local by default). Mentioning other people by name is up to you;
they go through the same sensitive-data scan as everything else when the
answers become training data.

This is phase 2 of [#14](https://github.com/NotYuSheng/Doppelganger/issues/14).
