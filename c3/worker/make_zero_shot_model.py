"""Build a zero-shot model.pt for the C4 worker, its .sha256 and the metrics json for the C1 upload page.

The artifact holds only class names and text prompts: the worker computes the CLAP text embeddings
of the prompts when it loads the model, so this script needs torch but not transformers or CLAP.

    c3/worker/.venv/bin/python c3/worker/make_zero_shot_model.py     # -> $SCICP_DATA/work/models/zero_shot_1.3.1/
    c3/worker/.venv/bin/python c3/worker/make_zero_shot_model.py --version 1.3.2 --temperature 0.07 --out /some/dir

--temperature divides the cosine similarities before the softmax. CLAP's audio-text cosines lie in a
narrow band (about -0.05 to 0.5), so 0.01 (1.3.0, CLAP's own logit scale) gave 1.00 for nearly every
clip. 0.05 was chosen on the five C2 clips: top-1 0.67 - 0.91, still above C3's 0.60 / 0.70 thresholds
for a clear sound; 0.07 gave 0.52 - 0.80 (two clips under 0.60), 0.03 gave 0.87 - 0.99. Higher = softer.

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

DEFAULT_TEMPERATURE = 0.05   # = c4_worker.classifiers.DEFAULT_ZERO_SHOT_TEMPERATURE (a test checks it)
DEFAULT_VERSION = "1.3.1"
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
    models_dir = Path(os.environ.get("SCICP_DATA", "/Users/ray/Documents/scicp-data")) / "work" / "models"
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=DEFAULT_VERSION,
                    help="the version you will type in the upload dialog; names the output folder")
    ap.add_argument("--out", type=Path, help="output folder (default $SCICP_DATA/work/models/zero_shot_<version>)")
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE,
                    help="softmax temperature on the cosine similarities (higher = softer); default %(default)s")
    args = ap.parse_args()
    if args.temperature <= 0:
        ap.error("--temperature must be > 0")
    args.out = args.out or models_dir / f"zero_shot_{args.version}"

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
        "note": f"sound-classifier {args.version}: zero-shot CLAP (laion/clap-htsat-unfused) with text prompts, "
                f"temperature {args.temperature}; NOT evaluated, metrics are placeholders",
    }
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    print(f"version          {args.version} (temperature {args.temperature})")
    print(f"model.pt         {model_path} ({len(data)} bytes)")
    print(f"sha256           {sha}")
    print(f"metrics.json     {args.out / 'metrics.json'}")
    print("perClass for the upload dialog:")
    print(json.dumps(metrics["perClass"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
