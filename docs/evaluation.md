# Evaluation: does it sound like you?

Doppelganger keeps adding techniques that should make the model sound more like
you: more sources, reply drills, group-chat handling, persona prompts. This
harness measures whether they actually help, by comparing the model's replies
with what you really wrote **in conversations it never trained on**.

## The loop

```bash
# 1. Keep ~10% of conversations out of training as a test set
python -m doppelganger audit --holdout 0.1
python -m doppelganger train

# 2. Run the model behind an OpenAI-compatible API (leave it running)
python -m doppelganger serve            # fine-tuned (base + adapter)

# 3. In another terminal: have it answer the test conversations, then score
python -m doppelganger eval generate --label finetuned
python -m doppelganger eval blind --label finetuned     # you: which reply is really yours?
python -m doppelganger eval report
```

Or open the menu (`python -m doppelganger` → **eval**), which walks through the
same steps. It offers to create the test set if there isn't one yet.

## What's measured

| Measure | What it tells you | Good |
|---|---|---|
| **chrF** | How close the wording is: character n-gram overlap with your real reply. It rewards the same words, spellings and fragments, and handles short informal text better than word-level scores. | higher |
| **Style match** | Whether the model has your habits, whatever the exact words: reply length, number of messages, emoji, `!` and `?`, starting lowercase, ending with a period, `...`, lol/haha, ALL-CAPS. Shown with the biggest differences, e.g. *replies with lol/haha: you 17%, model 100%*. | higher |
| **Blind test** (`eval blind`) | You see a message and two replies, yours and the model's, in random order, and pick yours. | **~50%**, which means you can't tell |
| **LLM judge** (`eval judge`) | The same blind test, done by an LLM shown a few of your real replies. Cheaper and repeatable, and uses the `LLM_*` settings in `.env`. | **~50%** |

For the blind test and judge, the result includes a p-value against a coin
flip. With few questions, a result like 60% is still "can't reliably tell".

## Comparing techniques

Every score and test is saved to `data/eval_history.jsonl`, and `eval report`
shows the latest result per model:

```
model                 chrF   style   you spot  judge spots                  when
--------------------------------------------------------------------------------
base                  12.4     58%        95%          88%      2026-11-07 17:20
finetuned             31.7     77%        62%          54%      2026-11-07 17:48
```

Give each setup its own `--label` to compare:

- **Base vs fine-tuned:** `serve --base`, then `eval generate --label base`.
- **With vs without drills:** train on each dataset and generate with
  `--label drills` / `--label no-drills`. Keep the same test set: `--holdout`
  always holds out the same conversations.
- **A persona card** (from [`interview`](interview.md)): `eval generate --label persona --persona`.

## Notes

- **Re-train after creating the test set.** Otherwise the model has seen the
  test conversations and the scores look better than they are.
- **The test set stays put.** Running `audit` (or `auto`, or audit from the
  menu) again keeps the same conversations out of training. Membership is
  decided on the raw conversations before redaction, so changing redaction
  settings doesn't change the test set either. Use `--holdout 0` to remove it
  on purpose (its conversations then go into training).
- **Blind tests need at least 10 answers** before a verdict is given. Fewer
  can't show anything either way.
- **Each test item** is a held-out conversation cut just before your last
  reply (all the messages you sent in a row count as one reply). The model
  answers from the turns before it, merged by side the same way training data is.
- **Generation resumes.** Replies are cached per label in
  `data/eval_generations.json`, so re-running `eval generate` only fills in
  what's missing.
- **Privacy.** The test set, model replies and history contain your messages,
  so they're gitignored and blocked by the pre-commit hook. The LLM judge sends
  your replies to the `LLM_*` endpoint, so use a local one.
