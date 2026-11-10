"""Build a zero-shot model.pt for the C4 worker, its .sha256 and the metrics json for the C1 upload page.

The artifact holds only class names and text prompts: the worker computes the CLAP text embeddings
of the prompts when it loads the model, so this script needs torch but not transformers or CLAP.

    c3/worker/.venv/bin/python c3/worker/make_zero_shot_model.py            # -> $SCICP_DATA/work/models/zero_shot/
    c3/worker/.venv/bin/python c3/worker/make_zero_shot_model.py --out /some/dir --temperature 0.01

Then on the AI models page: upload model.pt, paste the sha256 if asked, and paste metrics.json's
macroF1 / accuracy / perClass. The metrics are ZEROS because the model has not been evaluated (no
audio is read here); BR-10 then requires `force` to activate it over a better-scored version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import torch

CLASSES = ["siren", "crash", "glass_break", "scream", "honking", "traffic", "environmental"]
PROMPTS = {
    "siren": ["an ambulance siren", "a police car siren wailing", "a fire truck siren"],
    "crash": ["a car crash", "a loud vehicle collision", "metal crunching in a car accident"],
    "glass_break": ["breaking glass", "a window shattering", "glass smashing"],
    "scream": ["a person screaming", "a woman screaming in fear", "a loud human scream"],
    "honking": ["a car horn honking", "a vehicle horn beeping", "drivers honking their horns"],
    "traffic": ["road traffic noise", "cars driving past on a street", "the hum of traffic on a highway"],
    "environmental": ["ambient environmental sounds", "quiet background noise", "wind and birds outdoors"],
}


def main() -> int:
    default_out = Path(os.environ.get("SCICP_DATA", "/Users/ray/Documents/scicp-data")) / "work" / "models" / "zero_shot"
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=default_out)
    ap.add_argument("--temperature", type=float, default=0.01, help="softmax temperature on cosine similarity")
    args = ap.parse_args()

    assert list(PROMPTS) == CLASSES and all(2 <= len(p) <= 4 for p in PROMPTS.values())
    args.out.mkdir(parents=True, exist_ok=True)
    model_path = args.out / "model.pt"
    torch.save({"kind": "zero_shot", "classes": CLASSES, "prompts": PROMPTS, "temperature": args.temperature},
               model_path)
    data = model_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    (args.out / "model.pt.sha256").write_text(f"{sha}  model.pt\n")

    # The shape of C1's MetricsBody (c1/api/app/schemas/__init__.py) and the upload dialog's fields.
    metrics = {
        "macroF1": 0.0,
        "accuracy": 0.0,
        "perClass": {c: {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0} for c in CLASSES},
        "note": "zero-shot CLAP (laion/clap-htsat-unfused) with text prompts; NOT evaluated, metrics are placeholders",
    }
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    print(f"model.pt         {model_path} ({len(data)} bytes)")
    print(f"sha256           {sha}")
    print(f"metrics.json     {args.out / 'metrics.json'}")
    print("perClass for the upload dialog:")
    print(json.dumps(metrics["perClass"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
