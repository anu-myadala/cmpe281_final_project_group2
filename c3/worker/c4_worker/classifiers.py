"""The two kinds of model.pt artifact and how each turns a CLAP embedding into the top 3 classes.

  {'kind': 'head', 'state_dict', 'classes', 'embedding_dim': 512}
      Linear(512,128) -> ReLU -> Dropout -> Linear(128,7) on the L2-normalized audio embedding
      (optional 'normalize': False feeds the raw embedding, if the head was trained that way).
  {'kind': 'zero_shot', 'classes', 'prompts': {class: [text, ...]}, 'temperature'?: 0.01}
      text embeddings of the prompts are computed once at load time and averaged per class;
      scores = softmax(cosine(audio, class) / temperature).

Classes must be exactly siren, crash, glass_break, scream, honking, traffic, environmental, in that
order. Anything else raises ArtifactError, and the ModelManager keeps the previous model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import torch
import torch.nn.functional as F
from torch import nn

from .config import CLASSES
from .encoder import Encoder

TopK = List[Tuple[str, float]]


class ArtifactError(Exception):
    """The artifact is unreadable or not in one of the two formats."""


def build_head(embedding_dim: int = 512, hidden: int = 128, n_classes: int = len(CLASSES),
               dropout: float = 0.2) -> nn.Sequential:
    return nn.Sequential(nn.Linear(embedding_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
                         nn.Linear(hidden, n_classes))


def _top_k(probs: torch.Tensor, classes: Tuple[str, ...], k: int = 3) -> TopK:
    values, idx = torch.topk(probs, k=min(k, len(classes)))
    return [(classes[int(i)], round(float(v), 4)) for v, i in zip(values, idx)]


class HeadClassifier:
    kind = "head"

    def __init__(self, state_dict: Dict[str, torch.Tensor], embedding_dim: int, normalize: bool = True) -> None:
        self.net = build_head(embedding_dim)
        self.net.load_state_dict(_strip_prefix(state_dict, self.net.state_dict().keys()), strict=True)
        self.net.eval()
        self.normalize = normalize

    @torch.no_grad()
    def classify(self, embedding: torch.Tensor) -> TopK:
        x = embedding.float().reshape(1, -1)
        if self.normalize:
            x = F.normalize(x, dim=-1)
        return _top_k(torch.softmax(self.net(x), dim=-1)[0], CLASSES)


class ZeroShotClassifier:
    kind = "zero_shot"

    def __init__(self, prompts: Dict[str, List[str]], encoder: Encoder, temperature: float = 0.01) -> None:
        if temperature <= 0:
            raise ArtifactError("temperature must be > 0")
        rows = []
        for cls in CLASSES:
            texts = prompts.get(cls)
            if not texts or not all(isinstance(t, str) and t for t in texts):
                raise ArtifactError(f"zero_shot: no prompts for {cls}")
            rows.append(F.normalize(F.normalize(encoder.embed_text(texts).float(), dim=-1).mean(dim=0), dim=-1))
        self.text = torch.stack(rows)          # (7, dim)
        self.temperature = temperature

    @torch.no_grad()
    def classify(self, embedding: torch.Tensor) -> TopK:
        a = F.normalize(embedding.float().reshape(1, -1), dim=-1)
        return _top_k(torch.softmax((a @ self.text.T)[0] / self.temperature, dim=-1), CLASSES)


def _strip_prefix(state_dict: Dict[str, Any], expected) -> Dict[str, Any]:
    """Accepts 'net.0.weight' as well as '0.weight' (a head saved from a module wrapping the Sequential)."""
    expected = set(expected)
    if set(state_dict) == expected:
        return state_dict
    stripped = {}
    for key, value in state_dict.items():
        tail = ".".join(key.split(".")[-2:])
        if tail not in expected or tail in stripped:
            return state_dict  # let load_state_dict report the mismatch
        stripped[tail] = value
    return stripped


def load_artifact(path: Path, encoder: Encoder):
    """model.pt -> HeadClassifier | ZeroShotClassifier. Raises ArtifactError."""
    try:
        data = torch.load(str(path), map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001  (pickle, zip and torch errors alike)
        first_line = (str(exc).strip().splitlines() or [""])[0][:200]   # torch's messages run to several paragraphs
        raise ArtifactError(f"unreadable artifact (not a torch file?): {type(exc).__name__}: {first_line}") from exc
    if not isinstance(data, dict) or "kind" not in data:
        raise ArtifactError("artifact is not a dict with a 'kind' key")
    classes = tuple(data.get("classes") or ())
    if classes != CLASSES:
        raise ArtifactError(f"classes {list(classes)} differ from {list(CLASSES)}")
    kind = data["kind"]
    try:
        if kind == "head":
            dim = int(data.get("embedding_dim", 0))
            if dim != encoder.dim:
                raise ArtifactError(f"embedding_dim {dim} != encoder {encoder.dim}")
            if not isinstance(data.get("state_dict"), dict):
                raise ArtifactError("head: state_dict missing")
            return HeadClassifier(data["state_dict"], dim, bool(data.get("normalize", True)))
        if kind == "zero_shot":
            if not isinstance(data.get("prompts"), dict):
                raise ArtifactError("zero_shot: prompts missing")
            return ZeroShotClassifier(data["prompts"], encoder, float(data.get("temperature", 0.01)))
    except ArtifactError:
        raise
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise ArtifactError(f"{kind}: {type(exc).__name__}: {exc}") from exc
    raise ArtifactError(f"unknown kind {kind!r}")
