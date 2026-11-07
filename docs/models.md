# Choosing a base model

Doppelganger fine-tunes a **LoRA adapter** on top of an existing open model
with [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) (pinned to
**0.9.4**). The adapter learns *how you write*; the base model supplies
everything else. Which base model you pick matters as much as your data.

The model is set by two lines in your training config. Copy
`configs/train_lora.yaml` to `configs/train_lora.local.yaml` (gitignored) and
edit:

```yaml
model_name_or_path: Qwen/Qwen2.5-7B-Instruct   # Hugging Face ID or a local path
template: qwen                                  # must match the model family
```

The tracked default, **Qwen1.5-1.8B-Chat**, is only a fast smoke test: it's
too small (and too old) to convincingly sound like you.

## Kinds of models

| Kind | Examples | Fit for a doppelganger |
|---|---|---|
| **Chat / instruct** | Qwen2.5-Instruct, Llama-3.x-Instruct, Gemma | ✅ **The default choice.** Already conversational; LoRA adapts the style. |
| **Hybrid reasoning**: can think before answering, or switch it off | Qwen3, GLM-4.5 | ✅ **Good, with one setting** (`enable_thinking: false`, below). Natural chat replies, with reasoning still available. |
| **Always-reasoning**: think before every answer | DeepSeek-R1 distills, Phi-4-reasoning | ❌ **Poor fit.** They always reason first, and short chat replies with no reasoning fight that. |
| **Base / pretrained**: no chat tuning | Qwen2.5-7B (without "-Instruct") | ⚠️ Needs much more data to become conversational. Rarely worth it here. |
| **Mixture-of-experts (MoE)** | Qwen3-30B-A3B, gpt-oss-20b | ⚠️ Fast at reply time (only part of the model runs per token), but training memory follows the **total** size. |
| **Multimodal**: images, audio | Qwen3-VL, Qwen3-Omni, GLM-4.5V | 🔭 Not used yet. Relevant to stickers/memes ([#30](https://github.com/NotYuSheng/Doppelganger/issues/30)) and voice ([#29](https://github.com/NotYuSheng/Doppelganger/issues/29)); LLaMA-Factory can fine-tune them. |
| **Specialist**: code, maths | DeepSeek-Coder, Seed-Coder | ❌ Tuned for other tasks. |

## Size

| Size | Hardware (LoRA, roughly) | What to expect |
|---|---|---|
| ~1–4B | 8 GB GPU, even CPU for tests | Fast, cheap; noticeably weaker at sounding like a person. Good for trying the pipeline. |
| **7–14B** | 16–24 GB GPU | **The sweet spot**: convincing style at a manageable cost. |
| 32B+ | 48 GB+, or QLoRA / several GPUs | Better nuance; much slower and costlier to train and run. |

QLoRA (4-bit) roughly halves the memory needed; see LLaMA-Factory's docs for
`quantization_bit`.

## Templates

`template` must match the model family, or the model sees its conversations in
the wrong format. Common ones in LLaMA-Factory 0.9.4:

| Family | `template` |
|---|---|
| Qwen2 / Qwen2.5 | `qwen` |
| Qwen3 (hybrid reasoning) | `qwen3` |
| Llama 3.x | `llama3` |
| Gemma | `gemma` |
| Mistral | `mistral` |
| DeepSeek-R1 distills | `deepseekr1` |
| gpt-oss | `gpt_oss` |
| GLM-4.5 (MoE) | `glm4_moe` |

The full list is in the [LLaMA-Factory model
table](https://github.com/hiyouga/LLaMA-Factory#supported-models).

## Reasoning models: set `enable_thinking: false`

Hybrid reasoning models write their reasoning inside `<think>…</think>` before
answering. Doppelganger's training data is your chat replies, which have no
reasoning in them.

**The trap:** for a reply without a `<think>` block, LLaMA-Factory 0.9.4's
reasoning template inserts an empty one, and with the default
`enable_thinking: true` it trains the model to produce that empty block. In
effect, you teach the model to stop thinking.

**The fix:** train in no-think mode:

```yaml
model_name_or_path: Qwen/Qwen3-8B
template: qwen3
enable_thinking: false   # chat replies trained without reasoning; thinking mode is left alone
```

With `false`, the empty block becomes part of the input and isn't trained on,
so the model learns to reply directly in no-think mode, which is what a
doppelganger does, and its thinking mode is disturbed much less. `chat` and
`serve` (and so `eval`) pass the same setting from your config, so the model
replies in the mode it was trained in.

For **always-reasoning** models there's no clean option. Training replies
without reasoning conflicts with how they work. Prefer a hybrid or chat model.

*Not yet explored:* training the model to **reason about how you'd reply**
before replying (e.g. "they're asking a favour on short notice; I usually
decline with a joke"), using explanations written by a teacher model, measured
with the [evaluation harness](evaluation.md) against plain fine-tuning.

## Checking your choice

Whatever you pick, measure it instead of guessing: keep a test set
(`audit --holdout 0.1`), train, then compare models with the [evaluation
harness](evaluation.md). Give each model its own label (`eval generate --label
qwen2.5-7b`, `--label qwen3-8b`, …) and use `eval report`.
