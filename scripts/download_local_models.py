#!/usr/bin/env python3
"""Download local AI models for offline McDonald's Drive-Thru inference.

Downloads:
  1. Phi-4-multimodal-instruct ONNX (INT4) from Hugging Face (~5.14 GB GPU / ~3.5 GB CPU)
  2. Piper TTS voice models (4 voices, ~240 MB total)

Usage:
  python scripts/download_local_models.py                           # GPU + all 4 Piper voices
  python scripts/download_local_models.py --cpu-only                # CPU variant
  python scripts/download_local_models.py --voices amy,jenny        # GPU + specific voices
  python scripts/download_local_models.py --model-dir ./my_models
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
except ImportError:
    sys.exit(
        "ERROR: 'rich' is required. Install it with:  pip install rich"
    )

try:
    from huggingface_hub import snapshot_download
except ImportError:
    sys.exit(
        "ERROR: 'huggingface_hub' is required. Install it with:  pip install huggingface-hub"
    )

console = Console()

# ---------------------------------------------------------------------------
# Piper TTS voice models from Hugging Face
# ---------------------------------------------------------------------------
PIPER_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# Map voice IDs to their download paths and sizes
PIPER_VOICES = {
    "amy": {
        "path": "en/en_US-amy-medium",
        "filename": "en_US-amy-medium",
        "model_size_bytes": 60_000_000,
        "json_size_bytes": 500,
    },
    "jenny": {
        "path": "en/en_GB-jenny_dioco-medium",
        "filename": "en_GB-jenny_dioco-medium",
        "model_size_bytes": 60_000_000,
        "json_size_bytes": 500,
    },
    "lessac": {
        "path": "en/en_US-lessac-medium",
        "filename": "en_US-lessac-medium",
        "model_size_bytes": 60_000_000,
        "json_size_bytes": 500,
    },
    "kristin": {
        "path": "en/en_US-kristin-medium",
        "filename": "en_US-kristin-medium",
        "model_size_bytes": 60_000_000,
        "json_size_bytes": 500,
    },
}

# ---------------------------------------------------------------------------
# Hugging Face model info
# ---------------------------------------------------------------------------
HF_REPO = "microsoft/Phi-4-multimodal-instruct-onnx"


def _file_looks_valid(path: Path, min_size: int) -> bool:
    """Return True if file exists and meets minimum expected size."""
    return path.is_file() and path.stat().st_size >= min_size


def download_piper(model_dir: Path, voice_ids: list[str] | None = None) -> None:
    """Download Piper TTS voice model files.
    
    Args:
        model_dir: Base model directory
        voice_ids: List of voice IDs to download (e.g., ['amy', 'jenny']).
                   If None, download all voices.
    """
    piper_dir = model_dir / "piper"
    piper_dir.mkdir(parents=True, exist_ok=True)

    # Default to all voices if not specified
    if voice_ids is None:
        voice_ids = list(PIPER_VOICES.keys())
    
    # Validate voice IDs
    invalid_voices = [v for v in voice_ids if v not in PIPER_VOICES]
    if invalid_voices:
        console.print(
            f"  [red]✗[/red] Invalid voice ID(s): {', '.join(invalid_voices)}\n"
            f"    Available: {', '.join(PIPER_VOICES.keys())}"
        )
        raise ValueError(f"Invalid voice IDs: {invalid_voices}")

    console.rule(f"[bold cyan]Piper TTS — {len(voice_ids)} voice(s)")

    # Calculate total download size
    total_size = sum(
        PIPER_VOICES[v]["model_size_bytes"] + PIPER_VOICES[v]["json_size_bytes"]
        for v in voice_ids
    )
    console.print(
        f"  [dim]Estimated total size: ~{total_size / 1_000_000:.0f} MB[/dim]\n"
    )

    for voice_id in voice_ids:
        voice_meta = PIPER_VOICES[voice_id]
        filename = voice_meta["filename"]

        # Download .onnx file
        onnx_filename = f"{filename}.onnx"
        onnx_dest = piper_dir / onnx_filename
        onnx_url = f"{PIPER_BASE_URL}/{voice_meta['path']}/{onnx_filename}"

        if _file_looks_valid(onnx_dest, voice_meta["model_size_bytes"]):
            console.print(f"  [green]✓[/green] {onnx_filename} already exists")
        else:
            _download_file(onnx_url, onnx_dest, onnx_filename, voice_meta["model_size_bytes"])

        # Download .onnx.json file
        json_filename = f"{filename}.onnx.json"
        json_dest = piper_dir / json_filename
        json_url = f"{PIPER_BASE_URL}/{voice_meta['path']}/{json_filename}"

        if _file_looks_valid(json_dest, voice_meta["json_size_bytes"]):
            console.print(f"  [green]✓[/green] {json_filename} already exists")
        else:
            _download_file(json_url, json_dest, json_filename, voice_meta["json_size_bytes"])

        console.print()


def _download_file(
    url: str, dest: Path, filename: str, min_size: int
) -> None:
    """Download a single file with progress bar.
    
    Args:
        url: Download URL
        dest: Destination file path
        filename: Display filename
        min_size: Minimum expected file size
    """
    console.print(f"  [yellow]↓[/yellow] Downloading {filename} …")

    try:
        with Progress(
            TextColumn("[bold blue]{task.fields[filename]}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("download", filename=filename, total=None)

            # Stream download with progress
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "mcdonalds-ai-drivethru/1.0")
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = resp.headers.get("Content-Length")
                if total:
                    progress.update(task, total=int(total))

                downloaded = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress.update(task, completed=downloaded)

        # Verify
        if not _file_looks_valid(dest, min_size):
            console.print(f"  [red]✗[/red] {filename} appears incomplete — please retry")
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded file {filename} is too small")
        else:
            console.print(f"  [green]✓[/green] {filename} downloaded successfully")

    except Exception as exc:
        console.print(f"  [red]✗[/red] Failed to download {filename}: {exc}")
        dest.unlink(missing_ok=True)
        raise


def download_phi4(model_dir: Path, cpu_only: bool) -> None:
    """Download Phi-4-multimodal ONNX model from Hugging Face."""
    phi4_dir = model_dir / "phi4-multimodal"
    phi4_dir.mkdir(parents=True, exist_ok=True)

    variant = "cpu" if cpu_only else "gpu"
    console.rule(f"[bold cyan]Phi-4-multimodal ONNX — {variant.upper()} variant")

    # Check if model already present by looking for key files
    marker_patterns = list(phi4_dir.glob(f"{variant}/*.onnx*"))
    if marker_patterns:
        console.print(
            f"  [green]✓[/green] Found {len(marker_patterns)} ONNX file(s) "
            f"in {phi4_dir / variant} — skipping download"
        )
        console.print(
            "    [dim](delete the directory to force re-download)[/dim]"
        )
        return

    console.print(
        f"  [yellow]↓[/yellow] Downloading {HF_REPO} ({variant}/*) …\n"
        f"    This is a large download (~{'3.5' if cpu_only else '5.14'} GB). "
        "Please be patient."
    )

    try:
        snapshot_download(
            repo_id=HF_REPO,
            allow_patterns=[f"{variant}/*"],
            local_dir=str(phi4_dir),
            local_dir_use_symlinks=False,
        )
        # Verify download
        onnx_files = list(phi4_dir.glob(f"{variant}/*.onnx*"))
        if onnx_files:
            console.print(
                f"  [green]✓[/green] Phi-4 {variant.upper()} model downloaded "
                f"({len(onnx_files)} file(s))"
            )
        else:
            console.print(
                "  [red]✗[/red] Download completed but no .onnx files found — "
                "check Hugging Face repo structure"
            )
    except Exception as exc:
        console.print(f"  [red]✗[/red] Phi-4 download failed: {exc}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download local AI models for McDonald's AI Drive-Thru"
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help="Download the CPU variant instead of GPU (smaller, no CUDA needed)",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        default="./models",
        help="Base directory for model downloads (default: ./models)",
    )
    parser.add_argument(
        "--voices",
        type=str,
        default=None,
        help=(
            "Comma-separated list of voice IDs to download "
            f"(available: {', '.join(PIPER_VOICES.keys())}). "
            "Default: download all voices."
        ),
    )
    args = parser.parse_args()

    model_dir = Path(args.model_dir).resolve()
    console.print(f"\n[bold]Model directory:[/bold] {model_dir}\n")

    # Parse voice IDs
    voice_ids = None
    if args.voices:
        voice_ids = [v.strip() for v in args.voices.split(",")]

    download_piper(model_dir, voice_ids=voice_ids)
    console.print()
    download_phi4(model_dir, cpu_only=args.cpu_only)

    console.print()
    console.rule("[bold green]All downloads complete")
    console.print(
        "\nNext steps:\n"
        "  1. Set [bold]LOCAL_MODE_ENABLED=true[/bold] in app/backend/.env\n"
        "  2. Start the app:  [dim]python app/backend/app.py[/dim]\n"
    )


if __name__ == "__main__":
    main()
