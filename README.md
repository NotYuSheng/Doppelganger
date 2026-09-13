<h1 align="center">Doppelganger</h1>

<p align="center">
  <strong>Fine-tune an LLM on your own chat history to mimic how you write</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#usage">Usage</a> •
  <a href="#fine-tune-your-model-lora">Fine-Tuning</a> •
  <a href="#privacy--sensitive-data">Privacy</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11--3.13-3776AB?style=flat&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/PyTorch-EE4C2C?style=flat&logo=pytorch&logoColor=white" alt="PyTorch"/>
  <img src="https://img.shields.io/badge/LLaMA--Factory-0.9.4-FF6F00?style=flat" alt="LLaMA-Factory"/>
  <img src="https://img.shields.io/badge/OpenAI--compatible-LLM-412991?style=flat&logo=openai&logoColor=white" alt="OpenAI-compatible"/>
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat" alt="License: MIT"/>
</p>

<p align="center">
  <img src="demo/demo.gif" alt="Doppelganger CLI: ingest a chat export and scan it for sensitive data before training" width="760">
</p>

---

Doppelganger fine-tunes large language models (like Qwen) on your own chat conversations, capturing how *you* write. Built on top of [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory), it turns a raw chat export into a [ShareGPT](https://github.com/hiyouga/LLaMA-Factory/blob/main/data/README.md)-formatted dataset for supervised fine-tuning (SFT), then trains a LoRA adapter on it.

Ingestion is **source-agnostic**: a small adapter parses each platform's export into a normalized message stream, and the rest of the pipeline (sessionizing, turn-merging, sensitive-data scanning, optional quality auditing, ShareGPT formatting) is shared. **Telegram** and **WhatsApp** are supported today, with **Discord** and other platforms planned — each slots in as a drop-in adapter.

> [!CAUTION]
> **Your chat history is sensitive data, and you are responsible for it.** A model fine-tuned on it can memorize and later reproduce personal identifiers, private conversations, credentials, and messages written by other people in your chats. The built-in [sensitive-data scanning](#privacy--sensitive-data) is a **safety net, not a guarantee** — both regex and LLM detection miss real cases and raise false positives. Before training, sharing, or deploying anything: **review the dataset yourself**, get consent from others whose messages are included (especially in group chats), and comply with applicable privacy laws. Treat trained adapters and merged checkpoints as sensitive too — they can leak the data they were trained on.

> [!IMPORTANT]
> **This is a for-fun, experimental project — not a production tool.** A model that imitates a real person can be misused for impersonation, deception, or social engineering, and it will happily generate convincing messages that person never actually wrote. Don't present its output as genuinely from anyone, and don't rely on it for anything that matters. Enjoy it responsibly.

Fine-tuning on your chats can capture your:

- **Writing tone, vocabulary, and phrasing**
- **Typical response lengths and structure**
- **Repeated expressions and idioms**
- **Conversational flow and habits**

> **Note**: This reflects *how you write*, not how you think — it **won't** replicate your deeper beliefs, private memories, or behaviour outside the chat. For stronger emulation, add other sources (emails, forum posts), clear prompt instructions at inference, and domain-specific data (technical messages, inside jokes).

## Features

| Feature | Description |
|---------|-------------|
| **Source-agnostic ingestion** | One adapter per platform parses an export into a normalized message stream; the rest of the pipeline is shared. Telegram + WhatsApp today; others drop in without touching the core. |
| **Conversation reconstruction** | Sessionizes messages by silence gaps **and** reply links, merges consecutive turns, and (optionally) preserves per-speaker labels in group chats. |
| **Sensitive-data scan** | Non-destructive regex scan over the built conversations — email, payment cards (checksum-validated), IP/MAC, API keys, plus pluggable country ID packs. Writes an audit report; you decide what to remove. |
| **LLM redaction** *(optional)* | An OpenAI-compatible model flags context-dependent PII (names, secrets) regex misses, into the same report and apply step. Local-first by design. |
| **LLM quality auditor** *(optional)* | Scores each conversation for coherence, quality, and pairing; drops weak samples and splits over-merged ones. |
| **ShareGPT output** | Emits exactly the format LLaMA-Factory consumes for SFT, with loss masked to your own turns. |
| **LoRA fine-tuning** | Ready-made train / export / chat configs; swap the base model in one place. |

## Quick Start

### Prerequisites

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.11–3.13 | Required by LLaMA-Factory 0.9.4 |
| PyTorch | CUDA build for your GPU | Training (see the [install matrix](https://pytorch.org/get-started/locally/)) |
| git | Latest | Clone + the dataset-hygiene pre-commit hook |
| LLM server | Any OpenAI-compatible API | **Optional** — quality auditing & LLM redaction (Ollama, vLLM, LM Studio, OpenAI) |

A CUDA-capable GPU is needed for training. Ingestion (parsing → dataset) runs fine on CPU.

### Installation

**1. Export your Telegram chat**

In **Telegram Desktop**: `Settings > Advanced > Export Telegram Data`. Select your chat(s), choose **JSON** format (not HTML), and place the result here:

```
Doppelganger/
└── data/
    └── result.json   ← place your export here
```

> **Note:** Telegram exports unzip to a dated folder like `DataExport_2025-07-09/result.json`. Move (or copy) that `result.json` to `data/result.json` — `setup.sh` looks for it there. Alternatively, point `python -m ingest` at the file directly with `--input path/to/result.json`.

> **Using WhatsApp instead?** Export a chat (`⋮ > More > Export chat > Without media`) to get a `.txt`, then run `python -m ingest --source whatsapp --input "WhatsApp Chat with Alice.txt" --self-name "Your Name"`. See [docs/sources/whatsapp.md](docs/sources/whatsapp.md) — `--self-name` is required since WhatsApp doesn't mark which participant is you.

**2. Clone and run setup**

The setup scripts create a virtual environment, install pinned dependencies (LLaMA-Factory **0.9.4**), create your `.env`, and process the export into `data/chat_sharegpt.json`.

```bash
git clone https://github.com/NotYuSheng/Doppelganger.git
cd Doppelganger

./setup.sh        # Linux / macOS
setup.bat         # Windows (from Command Prompt, not PowerShell)
```

<details>
<summary>Prefer to run it manually?</summary>

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m ingest --source telegram
```
</details>

**3. (Optional) Configure LLM features**

The core pipeline needs no LLM. To *also* enable the quality auditor and LLM redaction, copy `example.env` to `.env` (the setup scripts do this) and point it at a **local** OpenAI-compatible server (vLLM, LM Studio, llama.cpp) so your chat data stays on your machine:

```dotenv
LLM_VALIDATE=true
LLM_API_BASE_URL=http://localhost:8000/v1     # vLLM (LM Studio uses :1234/v1)
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct            # the model your server serves
LLM_API_KEY=local                             # local servers accept any value
```

**4. Fine-tune**

```bash
source venv/bin/activate
llamafactory-cli train configs/train_lora.yaml
```

> **Note:** The tracked `train_lora.yaml` defaults to the small **Qwen1.5-1.8B-Chat** so this step runs fast as an end-to-end smoke test. It's a real model but too small to convincingly mimic your writing — for real results, switch to a larger base (e.g. Qwen2.5-14B-Instruct) via a local override. See [Fine-Tune Your Model](#fine-tune-your-model-lora).

## Usage

`python -m ingest` turns a raw export into a training-ready dataset. Useful flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--source` | `telegram` | Chat source to parse (more planned) |
| `--input` | `./data/result.json` | Path to the raw export |
| `--format` | `sharegpt` | `sharegpt` (for training) or `jsonl` (intermediate) |
| `--self-name` | auto-detected | Override which sender is "you" |
| `--conversation-gap` | `3600` | Seconds of silence that start a new conversation |
| `--message-chain` | `30` | Max seconds between same-sender messages to merge into one turn |
| `--multi-speaker` | off | In group chats, keep and label each sender on the human side (your turns are never labelled) |
| `--no-audit` | off | Master off-switch: skip **all** auditing (regex scan + LLM validation) and just build the dataset |
| `--skip-redact-scan` | off | Skip only the regex sensitive-data scan |
| `--skip-validation` | off | Skip only the LLM quality validation |

### Optional: LLM quality auditing

Each extracted conversation can be scored for **coherence, quality, and pairing**, dropping or splitting weak samples before training. It talks to a **local** OpenAI-compatible server (vLLM, LM Studio, llama.cpp) so your chat data stays on your machine. It's enabled automatically when `LLM_API_KEY` or `LLM_API_BASE_URL` is set (configure it in `.env`, step 3 above).

To turn it off, set `LLM_VALIDATE=false` in `.env` (persistent) or pass `--skip-validation` for a single run. To disable **all** auditing at once — both this and the regex scan — use `--no-audit`.

### Running a local LLM (recommended: LM Studio)

The LLM features are designed to run against a **local** model so your chat data never leaves your machine. [LM Studio](https://lmstudio.ai) is the easiest way to get one running with a click-through UI:

1. Install **LM Studio** and use its search to download a model (see the table below).
2. Open the **Developer** tab → **Start Server**. It serves an OpenAI-compatible API at `http://localhost:1234/v1`.
3. In `.env`, set:
   ```dotenv
   LLM_VALIDATE=true
   LLM_API_BASE_URL=http://localhost:1234/v1
   LLM_MODEL=<the model identifier LM Studio shows for the loaded model>
   LLM_API_KEY=local
   ```

(Prefer the CLI? **vLLM** serves the same API at `http://localhost:8000/v1` with `--model Qwen/Qwen2.5-7B-Instruct`. **Ollama** also works at `http://localhost:11434/v1`.)

**Which model?** The auditor/redactor just needs solid instruction-following and JSON output — a small model is plenty. Pick by your hardware (GGUF quants in LM Studio shrink the footprint):

| Your hardware | Suggested model | Notes |
|---------------|-----------------|-------|
| CPU-only, or ≤8 GB VRAM / 16 GB RAM | **Qwen2.5-3B-Instruct** (Q4) | Fast and light; fine for scoring + PII spans |
| 8–16 GB VRAM | **Qwen2.5-7B-Instruct** (Q4/Q5) | Recommended balance of quality and speed |
| 24 GB+ VRAM | **Qwen2.5-14B-Instruct** | Best judgment on tricky/ambiguous cases |

Tiny machine? **Qwen2.5-1.5B-Instruct** or **Llama-3.2-3B-Instruct** also work, with slightly noisier results. Any OpenAI-compatible model will do — these are just sensible starting points.

## Privacy & Sensitive Data

Fine-tuning on real chat history may unintentionally encode personal identifiers, confidential conversations, or sensitive content.

> **Always review and sanitize your dataset before training.** You are responsible for compliance with privacy laws and personal data protection.

### Automated sensitive-data scan

To make that review practical, ingestion runs a **regex-based scan** over the built conversations by default. It is **non-destructive** — it only flags and warns, writing `data/redaction_report.json` (with masked previews) and printing a summary so you can decide what to do:

```
[redactor] WARNING: 3 potential sensitive item(s) detected across 2 conversations:
  EMAIL          2 hit(s) in   2 conversation(s)  [medium]
  CARD_NUMBER    1 hit(s) in   1 conversation(s)  [high]
  API_KEY        1 hit(s) in   1 conversation(s)  [high]
```

Detection works everywhere out of the box. **Universal detectors** — email, payment cards (checksum-validated), IP/MAC addresses, API keys and private keys — aren't tied to any country and always run. On top of those, optional **locale packs** add country-specific identifiers (national IDs, local phone/postal formats).

Once you've reviewed the report, act on it:

```bash
python -m ingest --source telegram --redact replace   # swap spans for [CATEGORY]
python -m ingest --source telegram --redact drop       # drop flagged conversations
python -m ingest --source telegram --skip-redact-scan  # opt out entirely
```

### Add coverage for your country

Locale packs are built to be community-contributed: each is a single drop-in module under [`ingest/redaction/`](ingest/redaction/), needing no changes to the scanner or pipeline. Adding one is three steps:

1. Copy an existing pack to `ingest/redaction/<cc>.py` (your ISO country code).
2. Register detectors with `make(...)` and `locale="<CC>"`. Back each pattern with a checksum/validator where the identifier has one — that precision is what keeps the report trustworthy instead of noisy.
3. Import your module in `ingest/redaction/__init__.py`.

Singapore ships as the worked reference ([`sg.py`](ingest/redaction/sg.py): national ID with checksum, local phone, postal code) — but the recipe is the same for any country, and **PRs for new locales are welcome**. Choose which packs run with `--redact-locales` (universal detectors always run regardless).

### LLM-assisted redaction

Regex can't catch everything (names, context-dependent secrets). With `--llm-redact`, an LLM additionally flags such spans into the **same report and the same `--redact` step** — it points at verbatim spans, never rewriting your text. To protect your data it **prefers a local endpoint**: set `LLM_API_BASE_URL` to a local OpenAI-compatible server; without one it refuses to use a hosted API unless you pass `--allow-cloud-redaction`.

```bash
LLM_API_BASE_URL=http://localhost:8000/v1 LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
  python -m ingest --source telegram --llm-redact --redact replace
```

### Keeping your data out of git

Your chat export and any generated datasets are ignored by `.gitignore` (`result.json`, `*.json`, `*.jsonl`, `DataExport*/`, `*.session`, `.env`, plus Telegram media/contacts such as `*.vcard`, `*.tgs`, `*.webp`, `*.ogg`/`*.oga`). Generic media (`.jpg`, `.mp4`, …) lives inside `DataExport*/`, which is ignored wholesale. As an extra safeguard, a pre-commit hook refuses to commit these files even if they are force-added. Enable it once per clone:

```bash
git config core.hooksPath hooks
```

To deliberately commit a blocked file, bypass the hook with `git commit --no-verify`.

## Intended Use & Responsible Use

Doppelganger is a **personal, educational project** — built for individuals to experiment with fine-tuning on **their own** chat history, for fun and learning. It is not a product, and it is **not** intended for profiling or surveilling other people, or for any commercial or deceptive use.

If you use it, please:

- **Use your own data.** Train on chats you're a participant in. Group chats include other people's messages — be considerate, and don't publish models trained on them.
- **Keep it local.** Don't publish the dataset, the trained adapter, or merged checkpoints — they can leak the conversations they were trained on.
- **Don't impersonate or deceive.** Never present generated text as something a real person actually said or wrote.
- **Respect the law.** You are responsible for complying with the privacy and data-protection laws in your jurisdiction.

In short: it's a toy for exploring how *you* write — please keep it that way.

## Fine-Tune Your Model (LoRA)

Training is configured by [`configs/train_lora.yaml`](configs/train_lora.yaml), which defaults to **Qwen1.5-1.8B-Chat** and the `chat_sharegpt` dataset registered in [`configs/dataset_info.json`](configs/dataset_info.json). Activate your venv, then run:

```bash
llamafactory-cli train configs/train_lora.yaml
```

### Customize for your model

Edit `configs/train_lora.yaml`:

| Field | Description |
|-------|-------------|
| `model_name_or_path` | Hugging Face model ID or local model path |
| `template` | Prompt template type (e.g. `qwen`, `chatml`, `default`) |
| `lora_target` | LoRA target modules (`all` works across architectures) |
| `output_dir` | Destination to save the LoRA checkpoints |

For example, to use `mistralai/Mistral-7B-Instruct-v0.2`, set `model_name_or_path` accordingly and `template: chatml`. Refer to the [LLaMA-Factory model table](https://github.com/hiyouga/LLaMA-Factory#supported-models) for recommended values.

> **Note**: Training masks the loss to your own (assistant) turns — `train_on_prompt: false`. That's why `--multi-speaker` labels on the human side are safe: the model reads them as context but never learns to produce them.

#### Keep personal tweaks out of git

The configs above are committed defaults — editing them in place shows up in `git status` and risks committing your machine-specific model/hyperparameters. To customize **without touching tracked files**, copy a config to a `*.local.yaml` name and edit that instead. Any `configs/*.local.yaml` is gitignored:

```bash
cp configs/train_lora.yaml configs/train_lora.local.yaml   # edit model, batch size, etc.
llamafactory-cli train configs/train_lora.local.yaml
```

The same works for `export_lora.local.yaml`. Your overrides stay local; the repo's defaults stay clean.

### Resume training

Uncomment and point `resume_from_checkpoint` in `configs/train_lora.yaml` at your latest checkpoint:

```yaml
resume_from_checkpoint: saves/Qwen1.5-1.8B-Chat-lora/checkpoint-400
```

### Merge the LoRA adapter with the base model

Edit [`configs/export_lora.yaml`](configs/export_lora.yaml) to match your model, then run:

```bash
llamafactory-cli export configs/export_lora.yaml
```

### Chat with your fine-tuned model

```bash
llamafactory-cli chat \
  --model_name_or_path merged/Qwen1.5-1.8B-Chat-merged \
  --template qwen
```

Update `--template` to match the one used during training.

## Activating the Environment Later

After running setup once, reactivate the venv in future sessions before running any commands:

```bash
source venv/bin/activate          # Linux / macOS
venv\Scripts\activate             # Windows (Command Prompt)
```

## Running the Tests

The ingestion pipeline (parsing, sessionizing, turn-merging, reply-threading, sensitive-data detection, ShareGPT formatting) is covered by a fast unit suite — no GPU, network, or API key required:

```bash
python -m unittest discover -s tests -t .
```

It runs in well under a second and locks in the conversion behaviour, so you can verify a change without running the full pipeline.

## Legacy Workflow

The pre-refactor, Windows-only workflow (which cloned LLaMA-Factory at HEAD) is preserved at the [`v0.1.0`](https://github.com/NotYuSheng/Doppelganger/releases/tag/v0.1.0) tag. The old `scripts/telegram_extract.py` and `scripts/convert_to_sharegpt.py` shims have been removed — use `python -m ingest` instead.

## Roadmap & Vision

Doppelganger is as much a **learning sandbox** as a tool: the aim is to explore the *full* AI toolbox for capturing how a person communicates, and to find what actually moves the needle on *"does this sound like me?"*. Today that's LoRA fine-tuning — everything below is exploratory (see the issue tracker for the live backlog).

**Shaping the model** — pre-training · fine-tuning · alignment/DPO · continual learning · synthetic data / self-instruct · multi-LoRA personas & merging · distillation to on-device · PEFT comparison

**Giving it context & memory** — RAG · long-term memory + reflection · relationship/knowledge graph · style embeddings / user-conditioning · persona-prompt quiz · MCP

**Multimodal** — voice cloning, TTS/STT · stickers / emoji / memes

**Making it act** — agentic doppelganger · multi-agent & self-play · proactive / initiative modeling

**Inference-time control** — activation steering / control vectors · prompt optimization (DSPy)

**Keeping it safe & honest** — guardrails · redaction (shipped) + offline NER · differential-privacy training · machine unlearning · memorization audits / canaries / watermarking / federated

**Knowing if it works** — evaluation, "does it sound like me?" · interpretability, "what did it learn about me?"

**More data & coverage** — more chat sources: WhatsApp, Discord, … · wider locale detector packs

> This is an experimental, for-fun project — the roadmap is a wishlist of things to explore, not a commitment.

## Star History

<a href="https://www.star-history.com/?repos=NotYuSheng%2FDoppelganger&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=NotYuSheng/Doppelganger&type=date&theme=dark&legend=top-left&sealed_token=7WTlCPQj_XixAoCueVuof3pN192njmyJiTW5UTTt4WXbsr0wzb5LDOhZ4zkX4RJOKWAJdb8Su4oTulB-6pxabdKS_ndwqMSE3-NtIzN2_bHMoQli__dI5KlpnHbYs4vf61uhwSVz8hBqe0BaG0yVGLsQa2YBE4-Tn1MKa2yxXRUSTRGp_CDBuKdrbbLk" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=NotYuSheng/Doppelganger&type=date&legend=top-left&sealed_token=7WTlCPQj_XixAoCueVuof3pN192njmyJiTW5UTTt4WXbsr0wzb5LDOhZ4zkX4RJOKWAJdb8Su4oTulB-6pxabdKS_ndwqMSE3-NtIzN2_bHMoQli__dI5KlpnHbYs4vf61uhwSVz8hBqe0BaG0yVGLsQa2YBE4-Tn1MKa2yxXRUSTRGp_CDBuKdrbbLk" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=NotYuSheng/Doppelganger&type=date&legend=top-left&sealed_token=7WTlCPQj_XixAoCueVuof3pN192njmyJiTW5UTTt4WXbsr0wzb5LDOhZ4zkX4RJOKWAJdb8Su4oTulB-6pxabdKS_ndwqMSE3-NtIzN2_bHMoQli__dI5KlpnHbYs4vf61uhwSVz8hBqe0BaG0yVGLsQa2YBE4-Tn1MKa2yxXRUSTRGp_CDBuKdrbbLk" />
 </picture>
</a>

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
