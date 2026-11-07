"""The individual pipeline steps the runner exposes.

Each step is a thin wrapper over existing code: ``parse``/``audit`` reuse the
``ingest`` modules; ``train``/``merge``/``chat`` shell out to ``llamafactory-cli``.
Every step returns a process-style exit code (0 = success).
"""

import json
import math
import os
import subprocess
import tempfile
from typing import List, Optional, Sequence, Tuple, Union

from ingest import banner, redactor, sharegpt, sources
from ingest.cli import _load_dotenv
from ingest.validator import validate_samples

# ── Canonical artifact paths (one source of truth) ───────────────────────────
DATA_DIR = "data"
DEFAULT_INPUT = sources.DEFAULT_INPUT
RAW_PATH = os.path.join(DATA_DIR, "chat_dataset.jsonl")        # parse output
DATASET_PATH = os.path.join(DATA_DIR, "chat_sharegpt.json")    # audit output (trains)
REPORT_PATH = os.path.join(DATA_DIR, "redaction_report.json")


def _resolve_config(stem: str) -> str:
    """Prefer a local override (``*.local.yaml``) over the tracked default."""
    local = os.path.join("configs", f"{stem}.local.yaml")
    return local if os.path.exists(local) else os.path.join("configs", f"{stem}.yaml")


def train_config() -> str:
    return _resolve_config("train_lora")


def export_config() -> str:
    return _resolve_config("export_lora")


def _read_yaml(path: str) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def adapter_dir() -> Optional[str]:
    """Where training writes the LoRA adapter (``output_dir`` in the config)."""
    try:
        return _read_yaml(train_config()).get("output_dir")
    except Exception:  # missing file, malformed YAML, or PyYAML not installed
        return None


# ── Epoch advisor ─────────────────────────────────────────────────────────────
# Style-SFT wants roughly this many optimisation steps total — enough to learn
# the voice, not so many it memorises specific chats.
_TARGET_STEPS = 300
_MAX_EPOCHS = 3


def dataset_size() -> int:
    """Number of conversations in the training dataset (0 if not built yet)."""
    try:
        with open(DATASET_PATH, encoding="utf-8") as f:
            return len(json.load(f))
    except (OSError, ValueError):
        return 0


def effective_batch(cfg: dict, num_gpus: int = 1) -> int:
    def _int(key: str) -> int:
        try:
            return int(cfg.get(key) or 1)
        except (TypeError, ValueError):
            return 1
    return _int("per_device_train_batch_size") * _int("gradient_accumulation_steps") * max(1, num_gpus)


def recommend_epochs(n_samples: int, eff_batch: int) -> Tuple[int, int, List[str]]:
    """Recommend an epoch count from dataset size, plus warnings.

    Targets ~``_TARGET_STEPS`` optimisation steps, but caps epochs low: on small
    datasets, hitting the step budget would just mean memorising the same few
    chats, so we cap and warn rather than crank epochs. Returns
    ``(epochs, steps_per_epoch, warnings)``.
    """
    eff_batch = max(1, eff_batch)
    steps_per_epoch = max(1, math.ceil(n_samples / eff_batch))
    epochs = max(1, round(_TARGET_STEPS / steps_per_epoch))
    epochs = min(epochs, _MAX_EPOCHS)

    warnings: List[str] = []
    if n_samples == 0:
        warnings.append("no dataset yet — run `parse` + `audit` first.")
    elif n_samples < 1000:
        epochs = min(epochs, 2)
        warnings.append(
            f"small dataset ({n_samples} samples) — the model will memorise. "
            "More chats helps far more than more epochs.")
    elif n_samples >= 10000:
        epochs = 1
    return epochs, steps_per_epoch, warnings


def _advise_epochs(cfg: dict, num_gpus: int) -> int:
    """Print an epoch recommendation for the current dataset; return it."""
    n = dataset_size()
    eff = effective_batch(cfg, num_gpus)
    rec, spe, warnings = recommend_epochs(n, eff)
    print(f"[advisor] Dataset: {n} samples · effective batch {eff} "
          f"→ {spe} steps/epoch. Recommended: {rec} epoch(s).")
    configured = cfg.get("num_train_epochs")
    if configured is not None and abs(float(configured) - rec) >= 1:
        print(f"[advisor] config sets {configured} epoch(s); recommended {rec}. "
              f"Pass `--epochs auto` to use {rec}, or `--epochs N` to set your own.")
    for w in warnings:
        print(f"[advisor] ⚠️  {w}")
    return rec


def _config_with_overrides(cfg_path: str, overrides: dict) -> str:
    """Write a temp copy of the config with the given keys overridden."""
    cfg = _read_yaml(cfg_path)
    cfg.update(overrides)
    import yaml
    fd, path = tempfile.mkstemp(prefix="train_override_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return path


def _has_tensorboard() -> bool:
    import importlib.util
    return importlib.util.find_spec("tensorboard") is not None


# ── Post-training visibility ──────────────────────────────────────────────────
# Everything here runs AFTER llamafactory-cli exits, so it has zero effect on
# fine-tuning speed. Best-effort: any failure is swallowed.
_SPARK = "▁▂▃▄▅▆▇█"


def _loss_summary(log_path: str) -> Optional[str]:
    rows = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # e.g. a half-written last line
    except OSError:
        return None
    losses = [r["loss"] for r in rows if isinstance(r, dict) and isinstance(r.get("loss"), (int, float))]
    if not losses:
        return None
    lo, hi = min(losses), max(losses)
    spark = "".join(
        _SPARK[int((l - lo) / (hi - lo) * (len(_SPARK) - 1)) if hi > lo else 0]
        for l in losses)
    return f"loss {losses[0]:.2f} → {losses[-1]:.2f} (min {lo:.2f})  {spark}"


def _lora_magnitudes(adapter_path: str) -> Optional[List[Tuple[str, float]]]:
    """Relative LoRA update size per layer type, via the cheap ||B||·||A|| proxy
    (no large B@A matmuls — keeps the summary near-instant)."""
    try:
        from safetensors import safe_open
    except ImportError:
        return None
    if not os.path.exists(adapter_path):
        return None
    import collections
    import re
    a_norm, b_norm = {}, {}
    try:
        with safe_open(adapter_path, "pt") as f:
            for k in f.keys():
                if "lora_A" in k:
                    a_norm[k.replace("lora_A", "")] = f.get_tensor(k).float().norm().item()
                elif "lora_B" in k:
                    b_norm[k.replace("lora_B", "")] = f.get_tensor(k).float().norm().item()
    except Exception:  # unreadable adapter, torch missing, ...
        return None
    mods: dict = collections.defaultdict(float)
    for base, an in a_norm.items():
        if base in b_norm:
            m = re.search(r"(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)", base)
            mods[m.group(1) if m else "other"] += an * b_norm[base]
    return sorted(mods.items(), key=lambda kv: -kv[1])


def summarize_run(output_dir: str) -> None:
    """Print a post-training recap: loss trend, where LoRA moved, and the plot
    path. Runs only after training exits, so it never slows fine-tuning."""
    print("\n=== training summary ===")
    ls = _loss_summary(os.path.join(output_dir, "trainer_log.jsonl"))
    if ls:
        print(ls)
    mags = _lora_magnitudes(os.path.join(output_dir, "adapter_model.safetensors"))
    if mags:
        peak = max(v for _, v in mags) or 1.0
        print("LoRA update magnitude by layer type (||B||·||A|| proxy):")
        for name, v in mags:
            print(f"  {name:11} {'█' * int(v / peak * 24)} {v:.1f}")
    png = os.path.join(output_dir, "training_loss.png")
    if os.path.exists(png):
        print(f"loss plot: {png}")


# ── Status (for the menu's checkmarks / out-of-order guards) ──────────────────
def parse_done() -> bool:
    return os.path.exists(RAW_PATH)


def audit_done() -> bool:
    return os.path.exists(DATASET_PATH)


def train_done() -> bool:
    out = adapter_dir()
    return bool(out) and os.path.exists(os.path.join(out, "adapter_model.safetensors"))


# ── Steps ─────────────────────────────────────────────────────────────────────
def parse(
    source: str = "telegram",
    input_path: Union[str, Sequence[str], None] = None,
    self_name: Union[str, Sequence[str], None] = None,
    conversation_gap: int = 3600,
    message_chain: int = 30,
    multi_speaker: bool = False,
    locales: Optional[List[str]] = None,
    scan: bool = True,
    group_chats: str = "keep",
    show_next: bool = True,
    country_hint: bool = True,
) -> int:
    """Stage 1: export → conversations → (scan). Writes raw samples + report.

    No redaction is applied here — that's :func:`audit`. The raw ``.jsonl`` holds
    unredacted text and is gitignored; treat it as sensitive.

    ``input_path`` and ``self_name`` may be lists to combine several exports
    (see :mod:`ingest.sources` for the ``source:path`` / ``source:name`` forms).
    """
    try:
        specs = sources.resolve_specs(input_path, source, self_name)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    missing = [s.path for s in specs if not os.path.exists(s.path)]
    if missing:
        print(f"error: input not found: {', '.join(missing)}\n"
              f"       Place your export there or pass --input.")
        return 1

    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        samples, results = sources.load_samples(
            specs,
            conversation_gap=conversation_gap,
            message_chain=message_chain,
            multi_speaker=multi_speaker,
            group_chats=group_chats,
        )
    except sources.SourceLoadError as e:  # malformed export, bad permissions, unknown self
        print(f"error: failed to parse export: {e}")
        return 1
    print(f"Extracted {len(samples)} conversation samples.")
    sources.print_summary(results)
    sources.print_group_advice(results, group_chats, multi_speaker)

    if scan:
        report = redactor.scan_samples(samples, locales=locales)
        redactor.write_report(report, REPORT_PATH)
        redactor.print_summary(report, REPORT_PATH, mode="off", country_hint=country_hint)

    written = sharegpt.write_jsonl(samples, RAW_PATH)
    print(f"Wrote {written} raw conversation samples to {RAW_PATH}.")
    if show_next:
        print(f"Next: review {REPORT_PATH}, then run `audit`.")
    return 0


def _previous_locales() -> Optional[List[str]]:
    """The detector selection the last ``parse`` scanned with (from its report),
    so ``audit`` redacts with the same countries chosen in the guided setup."""
    try:
        with open(REPORT_PATH, encoding="utf-8") as f:
            locales = json.load(f).get("locales")
    except (OSError, ValueError, AttributeError):
        return None
    return locales if isinstance(locales, list) and locales else None


def audit(
    redact: str = "replace",
    locales: Optional[List[str]] = None,
    validate: bool = True,
    country_hint: bool = True,
    holdout: Optional[float] = None,
) -> int:
    """Stage 2: review scan → redact → (optional LLM quality audit) → dataset.

    Consumes the raw ``.jsonl`` from :func:`parse` and writes the training-ready
    ShareGPT dataset.

    ``holdout`` keeps that fraction of conversations out of training as a test
    set for ``eval``. ``None`` (the default) keeps whatever test set already
    exists, so re-running audit never leaks it into training; ``0`` explicitly
    removes it. Membership is decided on the raw conversations, before
    redaction, so changing redaction settings keeps the same test set.
    """
    from doppelganger import evaluate
    try:
        fraction = evaluate.resolve_fraction(holdout)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    if not parse_done():
        print(f"error: {RAW_PATH} not found — run `parse` first.")
        return 1

    samples = sharegpt.load_jsonl_samples(RAW_PATH)
    print(f"Loaded {len(samples)} conversation samples from {RAW_PATH}.")
    samples, held = evaluate.split_holdout(samples, fraction)

    if locales is None:
        locales = _previous_locales()

    # Re-derive the scan summary so audit is self-contained (regex is cheap).
    report = redactor.scan_samples(samples, locales=locales)
    redactor.write_report(report, REPORT_PATH)
    redactor.print_summary(report, REPORT_PATH, mode=redact, country_hint=country_hint)

    if redact != "off":
        before = len(samples)
        samples = redactor.apply(samples, redact, locales=locales)
        print(f"[redactor] Applied --redact {redact}: {before} -> {len(samples)} samples.")
        held = redactor.apply(held, redact, locales=locales)  # the test set is redacted the same way

    if validate:
        samples = validate_samples(samples)  # self-disables if no LLM configured

    if fraction > 0:
        evaluate.write_holdout(held, fraction=fraction)
        verb = "Kept the existing test set:" if holdout is None else "Kept"
        print(f"[eval] {verb} {len(held)} conversation(s) out of training "
              f"({evaluate.HOLDOUT_PATH}); evaluate with `eval` after training.")
    elif holdout == 0 and evaluate.remove_holdout():
        print("[eval] Removed the test set (--holdout 0): its conversations are in the "
              "training data now.")

    written = sharegpt.write_sharegpt(samples, DATASET_PATH)
    print(f"Wrote {written} ShareGPT samples to {DATASET_PATH}.")
    try:
        cfg = _read_yaml(train_config())
        rec, spe, _ = recommend_epochs(written, effective_batch(cfg))
        print(f"Next: run `train` (recommended ~{rec} epoch(s) for {written} samples).")
    except Exception:  # missing/malformed config or PyYAML — the hint is optional
        print("Next: run `train`.")
    return 0


def _llamafactory(args: List[str], gpus: Optional[str] = None) -> int:
    env = dict(os.environ)
    if gpus:
        env["CUDA_VISIBLE_DEVICES"] = gpus
    shown = [a if len(a) <= 80 else a[:60].replace("\n", " ") + "…" for a in args]
    print(f"$ llamafactory-cli {' '.join(shown)}"
          + (f"   (CUDA_VISIBLE_DEVICES={gpus})" if gpus else ""))
    try:
        return subprocess.run(["llamafactory-cli", *args], env=env).returncode
    except FileNotFoundError:
        print("error: llamafactory-cli not found. Activate the venv (and `pip install -r requirements.txt`).")
        return 127


def train(config: Optional[str] = None, gpus: Optional[str] = None,
          epochs: Optional[str] = None, tensorboard: bool = False) -> int:
    """Stage 3: LoRA fine-tune via LLaMA-Factory.

    ``epochs`` may be ``None`` (use the config value), an integer string, or
    ``"auto"`` (use the size-based recommendation). ``tensorboard`` wires a live
    dashboard (negligible in-loop cost). A post-training summary prints after
    llamafactory exits, so neither affects fine-tuning speed materially.
    """
    if not audit_done():
        print(f"warning: {DATASET_PATH} not found — run `parse` + `audit` (or `auto`) first.")
        return 1

    cfg_path = config or train_config()
    if not os.path.exists(cfg_path):
        print(f"error: training config not found: {cfg_path}")
        return 1
    try:
        cfg = _read_yaml(cfg_path)
    except Exception as e:  # malformed YAML or PyYAML not installed
        print(f"error: failed to read training config: {e}")
        return 1
    num_gpus = len(gpus.split(",")) if gpus else 1
    recommended = _advise_epochs(cfg, num_gpus)
    output_dir = cfg.get("output_dir")

    overrides: dict = {}
    if epochs == "auto":
        overrides["num_train_epochs"] = float(recommended)
        print(f"[advisor] --epochs auto → training for {recommended} epoch(s).")
    elif epochs is not None:
        overrides["num_train_epochs"] = float(epochs)
        print(f"[advisor] --epochs {epochs} → overriding config.")
    if tensorboard:
        if _has_tensorboard():
            logdir = os.path.join(output_dir or "saves", "runs")
            overrides.update(report_to="tensorboard", logging_dir=logdir)
            print(f"[tensorboard] live dashboard — in another terminal run:\n"
                  f"              tensorboard --logdir {logdir}")
        else:
            print("[tensorboard] not installed — skipping (pip install tensorboard).")

    # Overrides go through a throwaway temp config we clean up afterwards.
    temp_cfg = None
    if overrides:
        cfg_path = temp_cfg = _config_with_overrides(cfg_path, overrides)

    try:
        rc = _llamafactory(["train", cfg_path], gpus=gpus)
    finally:
        if temp_cfg and os.path.exists(temp_cfg):
            try:
                os.remove(temp_cfg)
            except OSError:
                pass

    if rc == 0 and output_dir:
        try:
            summarize_run(output_dir)  # post-training only — no effect on training speed
        except Exception as e:  # the summary is a nicety; never fail a finished run
            print(f"[summary] skipped: {e}")
    return rc


def merge(config: Optional[str] = None, gpus: Optional[str] = None) -> int:
    """Stage 4 (optional): merge the LoRA adapter into the base model."""
    cfg_path = config or export_config()
    if not os.path.exists(cfg_path):
        print(f"error: export config not found: {cfg_path}")
        return 1
    return _llamafactory(["export", cfg_path], gpus=gpus)


def chat(config: Optional[str] = None, gpus: Optional[str] = None, persona: bool = True) -> int:
    """Stage 5: chat with the fine-tuned model (base + adapter, nothing merged).

    Uses the same training ``config`` as :func:`train` so a custom ``--config``
    run points at the right adapter / model / template.
    """
    cfg_path = config or train_config()
    if not os.path.exists(cfg_path):
        print(f"error: training config not found: {cfg_path}")
        return 1
    try:
        cfg = _read_yaml(cfg_path)
    except Exception as e:  # malformed YAML or PyYAML not installed
        print(f"error: failed to read training config: {e}")
        return 1
    out = cfg.get("output_dir")
    if not out or not os.path.exists(os.path.join(out, "adapter_model.safetensors")):
        print(f"warning: no trained adapter at {out!r} — run `train` first.")
        return 1
    args = [
        "chat",
        "--model_name_or_path", cfg.get("model_name_or_path", ""),
        "--adapter_name_or_path", out,
        "--template", cfg.get("template", "default"),
        "--finetuning_type", "lora",
        "--infer_dtype", "bfloat16",
    ]
    from ingest.interview import PERSONA_PATH, load_card
    card = load_card(PERSONA_PATH) if persona else None
    if card:
        args += ["--default_system", card]
        print(f"Using your persona card ({PERSONA_PATH}) as the system prompt.")
    return _llamafactory(args, gpus=gpus)


def serve(config: Optional[str] = None, gpus: Optional[str] = None,
          base: bool = False, port: int = 8000) -> int:
    """Serve the model behind an OpenAI-compatible API (for ``eval generate``).

    By default it's the fine-tuned model (base + adapter); ``base=True`` serves
    the base model alone, for a before/after comparison.
    """
    cfg_path = config or train_config()
    try:
        cfg = _read_yaml(cfg_path)
    except Exception as e:  # missing/malformed config or PyYAML not installed
        print(f"error: failed to read training config {cfg_path}: {e}")
        return 1
    args = ["api", "--model_name_or_path", cfg.get("model_name_or_path", ""),
            "--template", cfg.get("template", "default"), "--infer_dtype", "bfloat16"]
    if not base:
        out = cfg.get("output_dir")
        if not out or not os.path.exists(os.path.join(out, "adapter_model.safetensors")):
            print(f"warning: no trained adapter at {out!r} — run `train` first (or use --base).")
            return 1
        args += ["--adapter_name_or_path", out, "--finetuning_type", "lora"]
    os.environ["API_PORT"] = str(port)
    print(f"Serving the {'base' if base else 'fine-tuned'} model at http://localhost:{port}/v1 "
          "(Ctrl-C to stop). In another terminal: python -m doppelganger eval")
    return _llamafactory(args, gpus=gpus)


def auto(
    do_train: bool = False,
    redact: str = "replace",
    gpus: Optional[str] = None,
    show_banner: bool = True,
    **parse_kwargs,
) -> int:
    """Run the pipeline end-to-end, unattended (like the old one-shot ingest).

    parse → audit (redact, no review pause) → dataset. With ``do_train`` it also
    chains training. Redaction defaults to ``replace`` so it never trains on raw
    PII without an explicit opt-out (``redact='off'``).
    """
    if show_banner:
        banner.print_banner()
    print("\n=== auto: running the full pipeline (no review pause) ===")
    if redact == "off":
        print("WARNING: --redact off — sensitive data will NOT be removed.")
    # audit re-scans, so skip parse's own scan and its "next step" hints.
    parse_kwargs.setdefault("scan", False)
    rc = parse(show_next=False, **parse_kwargs)
    if rc:
        return rc
    rc = audit(redact=redact, locales=parse_kwargs.get("locales"),
               country_hint=parse_kwargs.get("country_hint", True))
    if rc:
        return rc
    if do_train:
        rc = train(gpus=gpus)
    return rc


def drills_done() -> bool:
    from ingest import drills
    return bool(drills.load_answers(drills.DEFAULT_PATH))


# Ordered for the menu.
STEPS = [
    ("drills", "Answer reply drills: create training data on purpose (optional)", drills_done),
    ("interview", "Persona interview: tell it who you are (optional)", lambda: os.path.exists(
        os.path.join(DATA_DIR, "persona.md"))),
    ("parse", "Parse export → conversations + sensitive-data scan", parse_done),
    ("audit", "Review scan → redact → training-ready dataset", audit_done),
    ("train", "LoRA fine-tune on the dataset", train_done),
    ("merge", "Merge the LoRA adapter into the base model (optional)", lambda: False),
    ("chat", "Chat with the fine-tuned model", lambda: False),
    ("eval", "Measure how much the model sounds like you", lambda: os.path.exists(
        os.path.join(DATA_DIR, "eval_history.jsonl"))),
]


def load_env() -> None:
    _load_dotenv()
