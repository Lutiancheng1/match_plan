#!/usr/bin/env python3
"""LoRA fine-tuning of Qwen3.5-VL-9B on football observation data using mlx-vlm."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MODEL = "/Users/niannianshunjing/.omlx/models/Qwen3.5-VL-9B-8bit-MLX-CRACK"
DEFAULT_TRAIN_DATA = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/training_data/frame_conversations.jsonl"
)
DEFAULT_EVAL_DATA = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/training_data/holdout_eval.jsonl"
)
DEFAULT_ADAPTER_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/training/adapters")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MLX Vision LoRA fine-tuning for football observation model.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Base model path or HF repo ID.")
    parser.add_argument("--train-data", type=Path, default=DEFAULT_TRAIN_DATA)
    parser.add_argument("--eval-data", type=Path, default=DEFAULT_EVAL_DATA)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--iters", type=int, default=1000)
    parser.add_argument("--steps-per-eval", type=int, default=100)
    parser.add_argument("--steps-per-save", type=int, default=200)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--grad-checkpoint", action="store_true", help="Use gradient checkpointing.")
    parser.add_argument("--dry-run", action="store_true", help="Print config without running.")
    return parser.parse_args()


def check_dependencies() -> bool:
    try:
        import mlx_vlm  # noqa: F401
        return True
    except ImportError:
        print("mlx-vlm not installed. Run: pip install mlx-vlm")
        return False


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main() -> int:
    args = parse_args()

    if not check_dependencies():
        return 1

    if not args.train_data.exists():
        print(f"Training data not found: {args.train_data}")
        print("Run build_training_dataset.py first (after auto_prelabel_frames.py).")
        return 1

    args.adapter_dir.mkdir(parents=True, exist_ok=True)
    run_name = f"football_obs_vlora_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    adapter_path = args.adapter_dir / run_name
    adapter_path.mkdir(parents=True, exist_ok=True)

    config = {
        "run_name": run_name,
        "model": args.model,
        "train_data": str(args.train_data),
        "eval_data": str(args.eval_data),
        "adapter_path": str(adapter_path),
        "lora_rank": args.lora_rank,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "iters": args.iters,
        "max_seq_length": args.max_seq_length,
        "grad_checkpoint": args.grad_checkpoint,
        "framework": "mlx-vlm",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    if args.dry_run:
        print("DRY RUN config:")
        print(json.dumps(config, indent=2))
        return 0

    # Save config
    (adapter_path / "training_config.json").write_text(json.dumps(config, indent=2))

    # Import mlx_vlm components
    import mlx.optimizers as optim
    from mlx_vlm import load as load_model
    from mlx_vlm.trainer import (
        TrainingArgs,
        VisionDataset,
        find_all_linear_names,
        get_peft_model,
        print_trainable_parameters,
        train,
    )

    print(f"Loading model: {args.model}")
    model, processor = load_model(args.model)

    # Apply LoRA via get_peft_model
    print(f"Applying LoRA (rank={args.lora_rank})")
    linear_layers = find_all_linear_names(model)
    print(f"  Found {len(linear_layers)} linear layers for LoRA")
    model = get_peft_model(model, linear_layers, rank=args.lora_rank, alpha=0.1, dropout=0.0)
    print_trainable_parameters(model)

    # Load datasets
    print(f"Loading training data: {args.train_data}")
    train_records = load_jsonl(args.train_data)
    print(f"  {len(train_records)} training examples")

    eval_records = []
    if args.eval_data.exists():
        eval_records = load_jsonl(args.eval_data)
        print(f"  {len(eval_records)} eval examples")

    # Get model config
    model_config = model.config.to_dict() if hasattr(model.config, "to_dict") else {}

    # Create datasets
    train_dataset = VisionDataset(train_records, model_config, processor)

    val_dataset = None
    if eval_records:
        val_dataset = VisionDataset(eval_records, model_config, processor)

    # Training args
    training_args = TrainingArgs(
        batch_size=args.batch_size,
        iters=args.iters,
        learning_rate=args.learning_rate,
        steps_per_eval=args.steps_per_eval,
        steps_per_save=args.steps_per_save,
        max_seq_length=args.max_seq_length,
        adapter_file=str(adapter_path / "adapters.safetensors"),
        grad_checkpoint=args.grad_checkpoint,
    )

    # Optimizer
    optimizer = optim.Adam(learning_rate=args.learning_rate)

    print(f"\nStarting LoRA fine-tuning: {run_name}")
    print(f"  Adapter output: {adapter_path}")
    print(f"  Iters: {args.iters}, Batch: {args.batch_size}, LR: {args.learning_rate}")
    print()

    try:
        train(
            model=model,
            optimizer=optimizer,
            train_dataset=train_dataset,
            val_dataset=val_dataset,
            args=training_args,
            train_on_completions=True,
        )
        config["finished_at"] = datetime.now(timezone.utc).isoformat()
        config["exit_code"] = 0
        print(f"\nTraining complete. Adapter saved to: {adapter_path}")
    except Exception as exc:
        config["finished_at"] = datetime.now(timezone.utc).isoformat()
        config["exit_code"] = 1
        config["error"] = str(exc)
        print(f"\nTraining failed: {exc}")

    (adapter_path / "training_config.json").write_text(json.dumps(config, indent=2))
    return config.get("exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
