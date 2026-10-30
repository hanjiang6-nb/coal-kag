#!/usr/bin/env python3
"""Native Qwen3-VL paper-text CPT, with measured benchmarking and reload checks.

No chat template or image tensors are supplied. The prepared corpus is an EOS
separated continuous text stream: EOS does not isolate document attention.
All reports/checkpoints are generated local artifacts and must stay out of Git.
Run --help without importing the GPU dependencies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone


VERSIONS = {"transformers": "4.57.6", "peft": "0.18.1", "bitsandbytes": "0.49.2", "accelerate": "1.12.0"}
PROJECTION = re.compile(r"model\.language_model\.layers\.\d+\.(?:self_attn\.(?:q|k|v|o)_proj|mlp\.(?:gate|up|down)_proj)")
RUNTIME = None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_runtime():
    global RUNTIME, np, torch
    import numpy as np
    import torch
    from types import SimpleNamespace
    from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3VLForConditionalGeneration
    from peft import LoraConfig, PeftModel, TaskType, get_peft_model, get_peft_model_state_dict, prepare_model_for_kbit_training, set_peft_model_state_dict
    from peft.utils.save_and_load import load_peft_weights
    from torch.utils.data import DataLoader
    from torch.utils.tensorboard import SummaryWriter

    actual = {name: importlib.metadata.version(name) for name in VERSIONS}
    if actual != VERSIONS:
        raise RuntimeError("Pinned training dependency mismatch: " + json.dumps(actual))
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("A real CUDA GPU with BF16 support is required")
    if not torch.__version__.startswith("2.8.") or torch.version.cuda != "12.8":
        raise RuntimeError("This runner requires the verified PyTorch 2.8 / CUDA 12.8 runtime")
    RUNTIME = SimpleNamespace(**locals())
    return RUNTIME


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def numerical_policy(deterministic):
    """Use supported PyTorch controls; never alter tolerances or model kernels."""
    torch.use_deterministic_algorithms(deterministic, warn_only=False)
    if deterministic:
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cuda.enable_cudnn_sdp(False)
        torch.backends.cuda.enable_mem_efficient_sdp(False)
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_math_sdp(True)
    return {"mode": "deterministic" if deterministic else "fast", "torch": torch.__version__,
            "cuda": torch.version.cuda, "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
            "cudnn_deterministic": torch.backends.cudnn.deterministic,
            "cudnn_benchmark": torch.backends.cudnn.benchmark,
            "matmul_allow_tf32": torch.backends.cuda.matmul.allow_tf32,
            "cudnn_allow_tf32": torch.backends.cudnn.allow_tf32,
            "flash_sdp": torch.backends.cuda.flash_sdp_enabled(), "math_sdp": torch.backends.cuda.math_sdp_enabled(),
            "efficient_sdp": torch.backends.cuda.mem_efficient_sdp_enabled(),
            "cudnn_sdp": torch.backends.cuda.cudnn_sdp_enabled()}


def exact_tree_audit(expected, actual, path, result):
    """Compare every state tensor exactly, without materializing a second model."""
    result["compared_nodes"] += 1
    equal = True
    if isinstance(expected, torch.Tensor):
        result["compared_tensors"] += 1
        result["compared_tensor_elements"] += expected.numel()
        equal = isinstance(actual, torch.Tensor) and expected.dtype == actual.dtype and expected.shape == actual.shape
        if equal:
            equal = torch.equal(expected.detach().cpu(), actual.detach().cpu())
    elif isinstance(expected, np.ndarray):
        result["compared_numpy_arrays"] += 1
        equal = isinstance(actual, np.ndarray) and expected.dtype == actual.dtype and np.array_equal(expected, actual)
    elif isinstance(expected, dict):
        equal = isinstance(actual, dict) and expected.keys() == actual.keys()
        if equal:
            for key in expected:
                exact_tree_audit(expected[key], actual[key], path + "/" + str(key), result)
    elif isinstance(expected, (list, tuple)):
        equal = type(actual) is type(expected) and len(expected) == len(actual)
        if equal:
            for index, (first, second) in enumerate(zip(expected, actual)):
                exact_tree_audit(first, second, path + "/" + str(index), result)
    else:
        equal = type(expected) is type(actual) and expected == actual
    if not equal:
        result["mismatch_count"] += 1
        if len(result["mismatch_paths"]) < 50:
            result["mismatch_paths"].append(path)


def audit_restored_state(model, optimizer, scheduler, state, adapter_weights, args):
    result = {"time_utc": utc_now(), "comparison": "exact equality, no tolerance", "compared_nodes": 0,
              "compared_tensors": 0, "compared_tensor_elements": 0, "compared_numpy_arrays": 0,
              "mismatch_count": 0, "mismatch_paths": [], "numerical_policy_sha256": args.numerical_policy_sha256}
    expected = {"adapter": adapter_weights, "optimizer": state["optimizer"], "scheduler": state["scheduler"],
                "rng": state["rng"], "optimizer_parameter_names": state.get("optimizer_parameter_names")}
    actual = {"adapter": RUNTIME.get_peft_model_state_dict(model), "optimizer": optimizer.state_dict(),
              "scheduler": scheduler.state_dict(), "rng": rng_state(),
              "optimizer_parameter_names": [name for name, parameter in model.named_parameters() if parameter.requires_grad]}
    exact_tree_audit(expected, actual, "state", result)
    result["status"] = "passed" if result["mismatch_count"] == 0 else "failed"
    write_json(args.output_dir / "resume_state_audit.json", result)
    if result["status"] != "passed":
        raise AssertionError("Exact resume state audit failed: " + json.dumps(result["mismatch_paths"]))
    return result


def rng_state():
    return {"python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state(), "cuda": torch.cuda.get_rng_state_all()}


def restore_rng(state):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"].cpu())
    torch.cuda.set_rng_state_all([value.cpu() for value in state["cuda"]])


def cpu_tree(value):
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: cpu_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cpu_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(cpu_tree(item) for item in value)
    return value


class Events:
    def __init__(self, output_dir, tensorboard_dir, mode):
        self.handle = (output_dir / (mode + "_steps.jsonl")).open("a", encoding="utf-8", buffering=1)
        run_name = output_dir.name + "_" + mode + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tb_dir = str(Path(tensorboard_dir) / run_name)
        self.writer = RUNTIME.SummaryWriter(self.tb_dir)

    def log(self, event, step=0, **fields):
        record = {"time_utc": utc_now(), "event": event, "step": step, **fields}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        self.handle.write(line + "\n")
        print(line, flush=True)
        for key, value in fields.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.writer.add_scalar(event + "/" + key, value, step)
        self.writer.flush()

    def close(self):
        self.writer.close()
        self.handle.close()


class CorpusBlocks:
    """Map-style dataset; each worker opens its own read-only NumPy memmaps."""
    def __init__(self, directory, vocab_size, verify_hashes=True):
        self.directory = Path(directory)
        self.metadata = read_json(self.directory / "metadata.json")
        self.ids_path = str(self.directory / "input_ids.npy")
        self.mask_path = str(self.directory / "attention_mask.npy")
        ids = np.load(self.ids_path, mmap_mode="r", allow_pickle=False)
        masks = np.load(self.mask_path, mmap_mode="r", allow_pickle=False)
        if ids.dtype != np.dtype("int32") or masks.dtype != np.dtype("uint8"):
            raise ValueError("Corpus must use int32 input_ids and uint8 attention_mask")
        if ids.ndim != 2 or masks.shape != ids.shape or ids.shape[1] not in (2048, 4096):
            raise ValueError("Invalid prepared corpus shapes: " + str(ids.shape))
        if list(ids.shape) != self.metadata.get("shape") or not self.metadata.get("packing_verified"):
            raise ValueError("Corpus metadata does not certify these packed arrays")
        if verify_hashes:
            for path, key in ((self.ids_path, "input_ids_sha256"), (self.mask_path, "attention_mask_sha256")):
                if sha256_file(path) != self.metadata.get(key):
                    raise ValueError("Prepared array hash mismatch: " + path)
        valid, skipped, input_tokens, supervised_tokens = [], [], 0, 0
        for start in range(0, len(ids), 256):
            values, mask = ids[start:start + 256], masks[start:start + 256]
            if np.any(values < 0) or np.any(values >= vocab_size):
                raise ValueError("Out-of-vocabulary token in " + str(directory))
            if np.any(mask > 1) or np.any(mask[:, 1:] > mask[:, :-1]):
                raise ValueError("Expected binary, right-padded attention masks")
            counts = mask[:, 1:].sum(axis=1, dtype=np.int64)
            for offset, count in enumerate(counts):
                (valid if count else skipped).append(start + offset)
            input_tokens += int(mask.sum(dtype=np.int64))
            supervised_tokens += int(counts.sum())
        self.indices = np.asarray(valid, dtype=np.int64)
        self.shape = tuple(ids.shape)
        self.audit = {"raw_blocks": len(ids), "usable_blocks": len(valid), "skipped_zero_target_rows": skipped,
                      "real_input_tokens": input_tokens, "supervised_shifted_tokens": supervised_tokens,
                      "note": "Each independent block's first input token has no predecessor and is not a prediction target."}
        self._ids = self._masks = None
        if not len(self.indices):
            raise ValueError("No real shifted targets in " + str(directory))

    def __len__(self):
        return len(self.indices)

    def __getstate__(self):
        state = dict(self.__dict__)
        state["_ids"] = state["_masks"] = None
        return state

    def __getitem__(self, index):
        # Import here too: Windows/spawn workers do not inherit parent globals.
        import numpy as worker_np
        import torch as worker_torch
        if self._ids is None:
            self._ids = worker_np.load(self.ids_path, mmap_mode="r", allow_pickle=False)
            self._masks = worker_np.load(self.mask_path, mmap_mode="r", allow_pickle=False)
        row = int(self.indices[index])
        return {"input_ids": worker_torch.from_numpy(self._ids[row].astype(worker_np.int64)),
                "attention_mask": worker_torch.from_numpy(self._masks[row].astype(worker_np.int64))}


def make_loader(dataset, batch_size, workers, seed, epoch=0, shuffle=False):
    generator = torch.Generator().manual_seed(seed + epoch)
    kwargs = {"batch_size": batch_size, "shuffle": shuffle, "num_workers": workers,
              "pin_memory": True, "drop_last": False, "generator": generator}
    if workers:
        kwargs.update(prefetch_factor=2, persistent_workers=False)
    return RUNTIME.DataLoader(dataset, **kwargs)


def gpu_batch(batch):
    ids = batch["input_ids"].to("cuda", non_blocking=True)
    mask = batch["attention_mask"].to("cuda", non_blocking=True)
    labels = ids.clone().masked_fill(mask == 0, -100)
    return {"input_ids": ids, "attention_mask": mask, "labels": labels, "use_cache": False}


def target_count(batch):
    return int(batch["attention_mask"][:, 1:].sum().item())


def load_model(args, adapter_dir=None):
    import bitsandbytes as bnb
    tokenizer = RUNTIME.AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
    kwargs = dict(local_files_only=True, trust_remote_code=False, dtype=torch.bfloat16,
                  attn_implementation="sdpa", device_map={"": 0}, low_cpu_mem_usage=True)
    if args.precision == "qlora":
        kwargs["quantization_config"] = RUNTIME.BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=["model.visual", "lm_head"])
    model = RUNTIME.Qwen3VLForConditionalGeneration.from_pretrained(str(args.model_dir), **kwargs)
    model.config.use_cache = False
    model.config.text_config.use_cache = False
    targets = [name for name, _ in model.named_modules() if PROJECTION.fullmatch(name)]
    if len(targets) != 448 or model.config.text_config.num_hidden_layers != 64:
        raise ValueError("Expected exactly 448 language projections in this Qwen3-VL-32B model")
    if any(isinstance(module, bnb.nn.Linear4bit) for module in model.visual.modules()):
        raise AssertionError("Vision must not be quantized")
    if isinstance(model.lm_head, bnb.nn.Linear4bit):
        raise AssertionError("lm_head must not be quantized")
    if args.precision == "qlora":
        for name in targets:
            if not isinstance(model.get_submodule(name), bnb.nn.Linear4bit):
                raise AssertionError("Language projection was not quantized: " + name)
        model = RUNTIME.prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=adapter_dir is None,
            gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model.requires_grad_(False)
    if adapter_dir is None:
        config = RUNTIME.LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.0,
                                   bias="none", task_type=RUNTIME.TaskType.CAUSAL_LM, target_modules=targets)
        model = RUNTIME.get_peft_model(model, config)
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model = RUNTIME.PeftModel.from_pretrained(model, str(adapter_dir), is_trainable=False, local_files_only=True)
    # A passive assertion: a genuine text-only forward must never enter vision.
    def reject_visual_forward(module, inputs):
        raise AssertionError("Text-only CPT unexpectedly called the visual encoder")
    model.get_base_model().visual.register_forward_pre_hook(reject_visual_forward)
    counts = Counter()
    trainable_names = []
    trainable_count = 0
    for name, parameter in model.named_parameters():
        counts[str(parameter.dtype)] += parameter.numel()
        if parameter.requires_grad:
            if ".language_model.layers." not in name or not any(x in name for x in (".lora_A.", ".lora_B.")):
                raise AssertionError("Unexpected trainable base/vision/head parameter: " + name)
            trainable_names.append(name)
            trainable_count += parameter.numel()
    expected = 134217728 * args.rank // 16
    if adapter_dir is None and (trainable_count != expected or len(trainable_names) != 896):
        raise AssertionError("Unexpected LoRA parameter count: " + str(trainable_count))
    if adapter_dir is not None and trainable_count:
        raise AssertionError("Reload verification model must be frozen")
    audit = {"precision": args.precision, "actual_dtype_stored_elements": dict(counts),
             "trainable_parameters": trainable_count, "expected_training_parameters": expected,
             "trainable_tensors": len(trainable_names), "trainable_names": trainable_names,
             "target_modules": targets, "vision_quantized": False, "lm_head_quantized": False,
             "pixel_values_supplied": False, "visual_forward_is_rejected": True,
             "gradient_checkpointing_use_reentrant": False,
             "nonquantized_precision_policy": "PEFT prepare converts all non-Params4bit BF16/FP16 parameters to FP32" if args.precision == "qlora" else "BF16 base; PEFT adapters may be FP32",
             "base_config_sha256": sha256_file(args.model_dir / "config.json")}
    return model, tokenizer, audit


def optimizer_for(model, learning_rate):
    return torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=learning_rate, weight_decay=0.01, fused=True)


def audit_gradients(model, require_a):
    a_nonzero = b_nonzero = 0
    missing = []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            if parameter.grad is not None:
                raise AssertionError("Frozen base parameter has a gradient: " + name)
            continue
        if parameter.grad is None:
            missing.append(name)
            continue
        if not bool(torch.isfinite(parameter.grad).all().item()):
            raise FloatingPointError("Non-finite gradient: " + name)
        nonzero = bool(torch.count_nonzero(parameter.grad).item())
        a_nonzero += int(nonzero and ".lora_A." in name)
        b_nonzero += int(nonzero and ".lora_B." in name)
    if missing or not b_nonzero or (require_a and not a_nonzero):
        raise AssertionError("LoRA gradient audit failed: missing=%d A=%d B=%d" % (len(missing), a_nonzero, b_nonzero))
    return {"a_nonzero_gradients": a_nonzero, "b_nonzero_gradients": b_nonzero, "frozen_base_gradients_none": True}


def update(model, optimizer, batches, audit_step=0):
    model.train()
    optimizer.zero_grad(set_to_none=True)
    counts = [target_count(batch) for batch in batches]
    total = sum(counts)
    if total == 0:
        raise ValueError("A training update has zero shifted targets")
    probe = next(p for name, p in model.named_parameters() if ".lora_B." in name)
    before = probe.detach().clone() if audit_step else None
    nll_sum = 0.0
    for batch, count in zip(batches, counts):
        if not count:
            continue
        inputs = gpu_batch(batch)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            result = model(**inputs)
            loss = result.loss
        loss_number = float(loss.detach().item())
        if not math.isfinite(loss_number):
            raise FloatingPointError("Non-finite real training loss")
        (loss * (count / total)).backward()
        nll_sum += loss_number * count
        del result, loss, inputs
    audit = audit_gradients(model, require_a=audit_step >= 2) if audit_step else {}
    norm = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0, error_if_nonfinite=True)
    optimizer.step()
    if audit_step and torch.equal(before, probe.detach()):
        raise AssertionError("The audited LoRA B parameter did not change after optimizer.step")
    optimizer.zero_grad(set_to_none=True)
    return {"loss": nll_sum / total, "effective_tokens": total, "gradient_norm": float(norm.item()), **audit}


def evaluate(model, dataset, args, batches=None):
    model_was_training = model.training
    model.eval()
    numerator, denominator, seen = 0.0, 0, 0
    loader = make_loader(dataset, args.eval_microbatch, args.workers, args.seed)
    with torch.inference_mode():
        for batch in loader:
            count = target_count(batch)
            if not count:
                continue
            with torch.autocast("cuda", dtype=torch.bfloat16):
                result = model(**gpu_batch(batch))
            loss = float(result.loss.item())
            if not math.isfinite(loss):
                raise FloatingPointError("Non-finite holdout loss")
            numerator += loss * count
            denominator += count
            seen += 1
            del result
            if seen >= (batches or args.eval_batches):
                break
    model.train(model_was_training)
    if denominator == 0:
        raise ValueError("Holdout contains no effective shifted targets")
    nll = numerator / denominator
    return {"nll": nll, "perplexity": math.exp(nll) if nll < 700 else None,
            "effective_tokens": denominator, "batches": seen, "token_weighted": True,
            "scope": "fixed initial holdout batches; not a task-quality or memorization test"}


def reference_result(model, batch, eos_id):
    was_training = model.training
    model.eval()
    ids, mask = batch["input_ids"], batch["attention_mask"]
    positions = sorted(set((0, 1, ids.shape[1] // 2, int(mask[0].sum().item()) - 1)))
    vocabulary = sorted(set((0, 1, 42, eos_id, int(ids[0, 0].item()), int(ids[0, 1].item()))))
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        result = model(**gpu_batch(batch))
    selected = result.logits[:, positions, :][:, :, vocabulary].float().cpu()
    loss = float(result.loss.item())
    if not math.isfinite(loss) or not bool(torch.isfinite(selected).all().item()):
        raise FloatingPointError("Non-finite checkpoint reference")
    model.train(was_training)
    return {"loss": loss, "selected_logits": selected.tolist(), "positions": positions,
            "vocabulary_ids": vocabulary, "effective_tokens": target_count(batch),
            "input_sha256": hashlib.sha256(ids.numpy().tobytes() + mask.numpy().tobytes()).hexdigest()}


def retain_checkpoints(args):
    """Remove only complete checkpoint directories certified as this run's own."""
    root = args.output_dir / "checkpoints"
    if not root.exists():
        return
    if root.is_symlink() or root.resolve() != args.output_dir.resolve() / "checkpoints":
        raise ValueError("Refusing checkpoint retention through a redirected directory")
    completed = []
    for entry in root.iterdir():
        if not re.fullmatch(r"step-\d{8}", entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        marker = entry / "CHECKPOINT_COMPLETE.json"
        if marker.is_symlink() or not marker.is_file():
            continue
        metadata = read_json(marker)
        if metadata.get("run_id") == args.run_id and metadata.get("status") == "complete":
            completed.append((metadata["progress"]["optimizer_step"], entry))
    for _, entry in sorted(completed)[:-args.keep_checkpoints]:
        resolved = entry.resolve(strict=True)
        if resolved.parent != root.resolve(strict=True) or not resolved.is_relative_to(args.output_dir.resolve()):
            raise ValueError("Checkpoint retention target escaped the run directory")
        # Refuse nested symlinks as well; never follow a redirected tree during
        # recursive removal. Partial saves and earlier runs remain untouched.
        if any(child.is_symlink() for child in entry.rglob("*")):
            raise ValueError("Refusing to remove a checkpoint containing a symlink")
        shutil.rmtree(resolved)


def ensure_checkpoint_space(directory, estimated_bytes, minimum_free_gib):
    """Reserve room for this write and a usable disk margin before creating files."""
    usage = shutil.disk_usage(directory)
    required = estimated_bytes + math.ceil(minimum_free_gib * 1024 ** 3)
    if usage.free < required:
        raise OSError("Insufficient checkpoint disk space: free=%d, next_save_estimate=%d, required_with_margin=%d; existing checkpoints are preserved" %
                      (usage.free, estimated_bytes, required))
    return {"free_bytes_before_save": usage.free, "estimated_save_bytes": estimated_bytes,
            "minimum_free_gib": minimum_free_gib}


def save_checkpoint(model, tokenizer, optimizer, scheduler, args, progress, reference_batch, name):
    destination = args.output_dir / name
    if destination.exists():
        raise FileExistsError("Checkpoint already exists; resume into a new output directory: " + str(destination))
    tensor_bytes = sum(p.numel() * p.element_size() for p in model.parameters() if p.requires_grad)
    tensor_bytes += sum(value.numel() * value.element_size() for state in optimizer.state.values()
                        for value in state.values() if isinstance(value, torch.Tensor))
    # Actual adapter + optimizer tensors, tokenizer files and generous metadata overhead.
    tokenizer_bytes = sum(p.stat().st_size for p in args.model_dir.iterdir()
                          if p.is_file() and p.suffix not in (".safetensors", ".bin"))
    disk = ensure_checkpoint_space(args.output_dir, tensor_bytes + tokenizer_bytes + 128 * 1024 ** 2,
                                   args.minimum_free_disk_gib)
    # Incomplete saves have unique names and never occupy the final checkpoint
    # path, so retrying from an earlier completed checkpoint remains possible.
    path = destination.with_name(destination.name + ".incomplete-" + uuid.uuid4().hex)
    path.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(str(path), safe_serialization=True, save_embedding_layers=False)
    tokenizer.save_pretrained(str(path))
    torch.save(cpu_tree(reference_batch), path / "reference_batch.pt")
    reference = reference_result(model, reference_batch, tokenizer.eos_token_id)
    write_json(path / "reference.json", reference)
    state = {"progress": progress, "optimizer": optimizer.state_dict(),
             "scheduler": scheduler.state_dict() if scheduler is not None else None,
             "rng": rng_state(), "configuration": serialized_args(args),
             "optimizer_parameter_names": [key for key, parameter in model.named_parameters() if parameter.requires_grad],
             "dataset_signature": args.dataset_signature,
             "base_config_sha256": sha256_file(args.model_dir / "config.json")}
    torch.save(state, path / "training_state.pt")
    write_json(path / "CHECKPOINT_COMPLETE.json", {"status": "complete", "run_id": args.run_id, "time_utc": utc_now(), "progress": progress,
               "adapter_sha256": sha256_file(path / "adapter_model.safetensors"), "dataset_signature": args.dataset_signature,
               "disk_space_check": disk})
    path.rename(destination)
    write_json(args.output_dir / "LATEST_CHECKPOINT.json", {"path": str(destination.resolve()), "progress": progress})
    retain_checkpoints(args)
    return str(destination.resolve())


def serialized_args(args):
    return {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}


def adapter_snapshot(model):
    return cpu_tree(RUNTIME.get_peft_model_state_dict(model))


def preflight(model, dataset, args, events):
    optimizer = optimizer_for(model, args.learning_rate)
    loader = iter(make_loader(dataset, 1, args.workers, args.seed))
    for step in (1, 2):
        try:
            batch = next(loader)
        except StopIteration:
            loader = iter(make_loader(dataset, 1, args.workers, args.seed))
            batch = next(loader)
        metrics = update(model, optimizer, [batch], audit_step=step)
        events.log("preflight", step, **metrics)
    del optimizer, loader
    gc.collect()
    torch.cuda.empty_cache()


def benchmark(model, tokenizer, datasets, args, events, audit):
    initial = adapter_snapshot(model)
    initial_rng = rng_state()
    baseline = evaluate(model, datasets["val"], args)
    events.log("baseline_validation", **baseline)
    # Audit both LoRA initialization steps before timing any candidate.
    preflight(model, datasets["train"], args, events)
    candidates, best = [], None
    best_adapter = best_optimizer = best_rng = None
    total_memory = torch.cuda.get_device_properties(0).total_memory
    for microbatch in args.benchmark_batches:
        if microbatch > len(datasets["train"]):
            break
        RUNTIME.set_peft_model_state_dict(model, initial)
        restore_rng(initial_rng)
        optimizer = optimizer_for(model, args.learning_rate)
        loader = iter(make_loader(datasets["train"], microbatch, args.workers, args.seed))
        optimizer_metrics = None
        try:
            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            elapsed, tokens, times = 0.0, 0, []
            for step in range(args.benchmark_warmup_steps + args.benchmark_measure_steps):
                torch.cuda.synchronize()
                started = time.perf_counter()
                try:
                    batch = next(loader)
                except StopIteration:
                    loader = iter(make_loader(datasets["train"], microbatch, args.workers, args.seed))
                    batch = next(loader)
                optimizer_metrics = update(model, optimizer, [batch])
                torch.cuda.synchronize()
                duration = time.perf_counter() - started
                measured = step >= args.benchmark_warmup_steps
                if measured:
                    elapsed += duration
                    tokens += optimizer_metrics["effective_tokens"]
                    times.append(duration)
                events.log("benchmark_step", step + 1, microbatch=microbatch, measured=measured,
                           seconds=duration, **optimizer_metrics)
            peak_reserved = torch.cuda.max_memory_reserved()
            row = {"microbatch": microbatch, "status": "measured", "measured_steps": len(times),
                   "effective_tokens": tokens, "seconds": elapsed, "tokens_per_second": tokens / elapsed,
                   "step_seconds": times, "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                   "peak_reserved_bytes": peak_reserved, "total_vram_bytes": total_memory,
                   "vram_headroom_fraction": (total_memory - peak_reserved) / total_memory,
                   "satisfies_10_percent_vram_headroom": peak_reserved <= 0.9 * total_memory,
                   "gradient_accumulation": 1}
            candidates.append(row)
            events.log("benchmark_candidate", len(candidates), **row)
            if row["satisfies_10_percent_vram_headroom"] and (best is None or row["tokens_per_second"] > best["tokens_per_second"]):
                best = row
                best_adapter = adapter_snapshot(model)
                best_optimizer = cpu_tree(optimizer.state_dict())
                best_rng = rng_state()
        except torch.cuda.OutOfMemoryError:
            candidates.append({"microbatch": microbatch, "status": "cuda_oom", "peak_allocated_bytes": torch.cuda.max_memory_allocated()})
            events.log("benchmark_oom", len(candidates), microbatch=microbatch)
            optimizer.zero_grad(set_to_none=True)
            del optimizer, loader
            gc.collect()
            torch.cuda.empty_cache()
            break
        del optimizer, loader
        gc.collect()
        torch.cuda.empty_cache()
        write_json(args.output_dir / "benchmark_report.json", {"status": "in_progress", "candidates": candidates, "best": best})
    if best is None:
        write_json(args.output_dir / "benchmark_report.json", {"status": "no_safe_candidate", "candidates": candidates})
        raise RuntimeError("No measured microbatch retained at least 10% VRAM headroom")
    RUNTIME.set_peft_model_state_dict(model, best_adapter)
    restore_rng(best_rng)
    optimizer = optimizer_for(model, args.learning_rate)
    optimizer.load_state_dict(best_optimizer)
    after = evaluate(model, datasets["val"], args)
    reference_batch = next(iter(make_loader(datasets["val"], args.eval_microbatch, args.workers, args.seed)))
    checkpoint = save_checkpoint(model, tokenizer, optimizer, None, args,
                                 {"mode": "benchmark", "optimizer_step": args.benchmark_warmup_steps + args.benchmark_measure_steps,
                                  "microbatch": best["microbatch"]}, reference_batch, "benchmark_adapter")
    report = {"status": "complete", "time_utc": utc_now(), "candidates": candidates, "best": best,
              "baseline_validation": baseline, "after_validation": after, "checkpoint": checkpoint,
              "training_started_from_fresh_seed": True, "benchmark_state_must_not_initialize_formal_training": True,
              "audit": audit, "tensorboard_dir": events.tb_dir,
              "quality_conclusion": "No task-quality improvement claim; compare independent holdout and downstream behavior."}
    write_json(args.output_dir / "benchmark_report.json", report)
    events.log("benchmark_complete", checkpoint=checkpoint, selected_microbatch=best["microbatch"], tokens_per_second=best["tokens_per_second"])


def train(model, tokenizer, datasets, args, events, audit):
    loader_length = math.ceil(len(datasets["train"]) / args.microbatch)
    total_steps = args.max_steps or args.epochs * math.ceil(loader_length / args.grad_accum)
    optimizer = optimizer_for(model, args.learning_rate)
    warmup_steps = min(total_steps - 1, math.ceil(total_steps * args.warmup_ratio))
    # A positive first warmup update lets the two-step LoRA audit observe the
    # mathematically expected B update, followed by nonzero A gradients.
    def lr_factor(step):
        if warmup_steps and step < warmup_steps:
            return (step + 1) / warmup_steps
        fraction = min(1.0, max(0.0, (step - warmup_steps) / max(1, total_steps - warmup_steps)))
        return 0.5 * (1.0 + math.cos(math.pi * fraction))
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_factor)
    progress = {"mode": "train", "optimizer_step": 0, "epoch": 0, "next_batch_index": 0, "effective_tokens": 0,
                "total_steps": total_steps}
    baseline = None
    if args.resume_from_checkpoint:
        path = args.resume_from_checkpoint
        if not (path / "CHECKPOINT_COMPLETE.json").is_file():
            raise ValueError("Cannot resume an incomplete checkpoint")
        state = torch.load(path / "training_state.pt", map_location="cpu", weights_only=False)
        if state["progress"]["mode"] != "train":
            raise ValueError("A benchmark checkpoint cannot initialize formal training")
        previous = state["configuration"]
        for key in ("precision", "rank", "microbatch", "grad_accum", "learning_rate", "seed", "epochs", "max_steps", "warmup_ratio",
                    "deterministic", "numerical_policy_sha256"):
            if previous.get(key) != serialized_args(args)[key]:
                raise ValueError("Resume configuration differs: " + key)
        if state["dataset_signature"] != args.dataset_signature or state["base_config_sha256"] != audit["base_config_sha256"]:
            raise ValueError("Resume base model or prepared corpus differs")
        complete = read_json(path / "CHECKPOINT_COMPLETE.json")
        if sha256_file(path / "adapter_model.safetensors") != complete["adapter_sha256"]:
            raise ValueError("Resume adapter checksum mismatch")
        adapter_weights = RUNTIME.load_peft_weights(str(path), device="cpu")
        RUNTIME.set_peft_model_state_dict(model, adapter_weights)
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        restore_rng(state["rng"])
        restored = audit_restored_state(model, optimizer, scheduler, state, adapter_weights, args)
        events.log("resume_state_audit", status=restored["status"], compared_tensors=restored["compared_tensors"],
                   compared_tensor_elements=restored["compared_tensor_elements"])
        del adapter_weights
        progress.update(state["progress"])
    else:
        baseline = evaluate(model, datasets["val"], args)
        events.log("baseline_validation", **baseline)
    reference_batch = next(iter(make_loader(datasets["val"], args.eval_microbatch, args.workers, args.seed)))
    start_epoch = progress["epoch"]
    epoch = start_epoch
    while progress["optimizer_step"] < total_steps:
        loader = make_loader(datasets["train"], args.microbatch, args.workers, args.seed, epoch, shuffle=True)
        iterator = iter(loader)
        batch_index = progress["next_batch_index"] if epoch == start_epoch else 0
        for _ in range(batch_index):
            next(iterator)
        while batch_index < len(loader) and progress["optimizer_step"] < total_steps:
            window = [next(iterator) for _ in range(min(args.grad_accum, len(loader) - batch_index))]
            torch.cuda.synchronize()
            started = time.perf_counter()
            step = progress["optimizer_step"] + 1
            used_learning_rate = optimizer.param_groups[0]["lr"]
            metrics = update(model, optimizer, window, audit_step=step if step <= 2 else 0)
            scheduler.step()
            torch.cuda.synchronize()
            duration = time.perf_counter() - started
            batch_index += len(window)
            progress.update(optimizer_step=step, epoch=epoch, next_batch_index=batch_index,
                            effective_tokens=progress["effective_tokens"] + metrics["effective_tokens"])
            events.log("train_step", step, epoch=epoch, seconds=duration,
                       tokens_per_second=metrics["effective_tokens"] / duration,
                       learning_rate=used_learning_rate, peak_allocated_bytes=torch.cuda.max_memory_allocated(), **metrics)
            if args.eval_every and step % args.eval_every == 0:
                events.log("validation", step, **evaluate(model, datasets["val"], args))
            if args.save_every and step % args.save_every == 0:
                checkpoint = save_checkpoint(model, tokenizer, optimizer, scheduler, args, dict(progress), reference_batch,
                                             "checkpoints/step-%08d" % step)
                events.log("checkpoint", step, path=checkpoint)
        del iterator, loader
        if batch_index >= loader_length:
            epoch += 1
            progress.update(epoch=epoch, next_batch_index=0)
    after = evaluate(model, datasets["val"], args)
    test = evaluate(model, datasets["test"], args) if "test" in datasets else None
    checkpoint = save_checkpoint(model, tokenizer, optimizer, scheduler, args, dict(progress), reference_batch, "final")
    write_json(args.output_dir / "train_report.json", {"status": "complete", "progress": progress, "baseline_validation": baseline,
               "after_validation": after, "final_test": test, "checkpoint": checkpoint, "audit": audit,
               "tensorboard_dir": events.tb_dir, "quality_conclusion": "Loss measurements do not establish downstream task improvement."})
    events.log("train_complete", progress["optimizer_step"], checkpoint=checkpoint, **after)


def reload_check(args, events):
    path = args.checkpoint or Path(read_json(args.output_dir / "LATEST_CHECKPOINT.json")["path"])
    if not (path / "CHECKPOINT_COMPLETE.json").is_file():
        raise ValueError("Checkpoint is incomplete")
    state = torch.load(path / "training_state.pt", map_location="cpu", weights_only=False)
    if state["configuration"]["precision"] != args.precision or state["configuration"]["rank"] != args.rank:
        raise ValueError("Reload precision/rank must match the saved training configuration")
    if state["configuration"].get("numerical_policy_sha256") != args.numerical_policy_sha256:
        raise ValueError("Reload numerical policy differs or was not recorded by this checkpoint")
    if state["dataset_signature"] != args.dataset_signature or state["base_config_sha256"] != sha256_file(args.model_dir / "config.json"):
        raise ValueError("Reload data or base model fingerprint differs")
    complete = read_json(path / "CHECKPOINT_COMPLETE.json")
    if sha256_file(path / "adapter_model.safetensors") != complete["adapter_sha256"]:
        raise ValueError("Adapter checksum differs from complete checkpoint")
    model, tokenizer, audit = load_model(args, adapter_dir=path)
    batch = torch.load(path / "reference_batch.pt", map_location="cpu", weights_only=True)
    expected = read_json(path / "reference.json")
    actual = reference_result(model, batch, tokenizer.eos_token_id)
    first = torch.tensor(expected["selected_logits"], dtype=torch.float32)
    second = torch.tensor(actual["selected_logits"], dtype=torch.float32)
    logits_match = torch.allclose(first, second, atol=args.reload_atol, rtol=args.reload_rtol)
    loss_match = math.isclose(expected["loss"], actual["loss"], abs_tol=args.reload_atol, rel_tol=args.reload_rtol)
    inputs_match = expected["input_sha256"] == actual["input_sha256"]
    report = {"status": "passed" if logits_match and loss_match and inputs_match else "failed",
              "checkpoint": str(path), "fresh_process_base_and_adapter_reload": True,
              "expected": expected, "actual": actual, "max_selected_logit_absolute_difference": float((first - second).abs().max().item()),
              "loss_absolute_difference": abs(expected["loss"] - actual["loss"]),
              "atol": args.reload_atol, "rtol": args.reload_rtol, "audit": audit}
    write_json(args.output_dir / "reload_check_report.json", report)
    events.log("reload_check", status=report["status"], checkpoint=str(path), loss_absolute_difference=report["loss_absolute_difference"],
               max_selected_logit_absolute_difference=report["max_selected_logit_absolute_difference"])
    if report["status"] != "passed":
        raise AssertionError("Fresh checkpoint reload differs from the saved real-data reference")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("benchmark", "train", "reload-check"), required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--precision", choices=("qlora", "bf16"), default="qlora")
    parser.add_argument("--deterministic", action="store_true", help="Strict deterministic CUDA/SDPA diagnostics; default uses fast kernels")
    parser.add_argument("--microbatch", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=0, help="0 uses --epochs; otherwise overrides the epoch limit")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--eval-microbatch", type=int, default=1)
    parser.add_argument("--eval-batches", type=int, default=8)
    parser.add_argument("--eval-every", type=int, default=100)
    parser.add_argument("--save-every", type=int, default=100)
    parser.add_argument("--keep-checkpoints", type=int, default=2, help="Keep the latest N complete periodic checkpoints created by this run")
    parser.add_argument("--minimum-free-disk-gib", type=float, default=10.0, help="Required free disk margin in addition to the next checkpoint write")
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--benchmark-batches", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    parser.add_argument("--benchmark-warmup-steps", type=int, default=1)
    parser.add_argument("--benchmark-measure-steps", type=int, default=3)
    parser.add_argument("--tensorboard-dir", type=Path, default=Path("/root/tf-logs"))
    parser.add_argument("--resume-from-checkpoint", type=Path)
    parser.add_argument("--checkpoint", type=Path, help="Reload-check checkpoint; default reads LATEST_CHECKPOINT.json")
    parser.add_argument("--reload-atol", type=float, default=1e-4)
    parser.add_argument("--reload-rtol", type=float, default=1e-4)
    args = parser.parse_args(argv)
    positive = (args.microbatch, args.grad_accum, args.epochs, args.rank, args.eval_microbatch,
                args.eval_batches, args.benchmark_warmup_steps, args.benchmark_measure_steps, *args.benchmark_batches)
    if min(positive) <= 0 or args.keep_checkpoints < 1 or args.max_steps < 0 or args.workers < 0 or args.eval_every < 0 or args.save_every < 0:
        parser.error("Batch sizes, steps, epochs and rank must be positive; counts cannot be negative")
    if not math.isfinite(args.learning_rate) or args.learning_rate <= 0 or not 0 <= args.warmup_ratio < 1:
        parser.error("Invalid learning rate or warmup ratio")
    if not math.isfinite(args.minimum_free_disk_gib) or args.minimum_free_disk_gib < 0:
        parser.error("Disk margin must be finite and nonnegative")
    if args.resume_from_checkpoint and args.mode != "train":
        parser.error("Resume is only supported in train mode")
    args.model_dir = args.model_dir.resolve(strict=True)
    args.dataset_dir = args.dataset_dir.resolve(strict=True)
    args.output_dir = args.output_dir.resolve()
    for source in (args.model_dir, args.dataset_dir):
        if args.output_dir == source or args.output_dir.is_relative_to(source) or source.is_relative_to(args.output_dir):
            parser.error("Output must be separate from and outside the base model and dataset directories")
    if args.mode != "reload-check" and not args.resume_from_checkpoint and args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("A new benchmark/train run needs an absent or empty output directory")
    summary_path = args.dataset_dir / "dataset_summary.json"
    summary = read_json(summary_path)
    if summary.get("status") != "complete" or (args.dataset_dir / "PREPARATION_INCOMPLETE.json").exists():
        raise ValueError("Dataset preparation is incomplete")
    args.dataset_signature = sha256_file(summary_path)
    for item in summary["settings"]["tokenizer_files"]:
        path = args.model_dir / item["name"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError("The model tokenizer differs from the prepared corpus: " + item["name"])
    # cuBLAS reads this during CUDA initialization: configure before importing
    # torch or asking load_runtime() to inspect the real GPU.
    if args.deterministic:
        workspace = os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        if workspace not in (":4096:8", ":16:8"):
            raise ValueError("Deterministic mode needs CUBLAS_WORKSPACE_CONFIG=:4096:8 or :16:8 before CUDA initialization")
    load_runtime()
    args.numerical_policy = numerical_policy(args.deterministic)
    args.numerical_policy_sha256 = hashlib.sha256(json.dumps(args.numerical_policy, sort_keys=True).encode("utf-8")).hexdigest()
    args.trainer_script_sha256 = sha256_file(Path(__file__).resolve())
    seed_everything(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest["configuration"].get("numerical_policy_sha256") != args.numerical_policy_sha256:
            raise ValueError("Existing output run has a different or unrecorded numerical policy")
        args.run_id = manifest["run_id"]
    else:
        args.run_id = uuid.uuid4().hex
        write_json(manifest_path, {"run_id": args.run_id, "created_at_utc": utc_now(), "configuration": serialized_args(args)})
    events = Events(args.output_dir, args.tensorboard_dir, args.mode)
    try:
        gpu = torch.cuda.get_device_properties(0)
        events.log("run_start", configuration=serialized_args(args), runtime={**VERSIONS, "torch": torch.__version__,
                   "cuda": torch.version.cuda, "python": sys.version, "gpu": gpu.name, "gpu_capability": list(torch.cuda.get_device_capability(0)),
                   "gpu_total_memory": gpu.total_memory}, tensorboard_dir=events.tb_dir)
        if args.mode == "reload-check":
            reload_check(args, events)
        else:
            model, tokenizer, audit = load_model(args)
            datasets = {name: CorpusBlocks(args.dataset_dir / name, model.config.text_config.vocab_size)
                        for name in (("train", "val", "test") if args.mode == "train" else ("train", "val"))}
            audit["datasets"] = {name: dataset.audit for name, dataset in datasets.items()}
            audit["dataset_signature"] = args.dataset_signature
            write_json(args.output_dir / "model_and_data_audit.json", audit)
            events.log("model_ready", trainable_parameters=audit["trainable_parameters"], allocated_bytes=torch.cuda.memory_allocated())
            (benchmark if args.mode == "benchmark" else train)(model, tokenizer, datasets, args, events, audit)
    except BaseException as error:
        events.log("run_failed", error_type=type(error).__name__, message=str(error))
        raise
    finally:
        events.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
