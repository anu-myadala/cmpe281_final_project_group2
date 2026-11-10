"""CLAP encoder (laion/clap-htsat-unfused through transformers): 512-d audio and text embeddings, CPU.

transformers is imported lazily, so the rest of the worker (and the unit tests, which use a fake
encoder) do not need it. The weights come from HF_HOME, a named volume in docker-compose.c4.yml,
so they are downloaded once.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Protocol, Sequence

import numpy as np
import torch

from .audio import CLAP_RATE

log = logging.getLogger("c4.encoder")


class Encoder(Protocol):
    dim: int

    def embed_audio(self, samples_48k: np.ndarray) -> torch.Tensor:
        """(dim,) projected audio embedding, not normalized."""

    def embed_text(self, texts: Sequence[str]) -> torch.Tensor:
        """(len(texts), dim) projected text embeddings, not normalized."""


class ClapEncoder:
    dim = 512

    def __init__(self, model_id: str = "laion/clap-htsat-unfused") -> None:
        self.model_id = model_id
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            from transformers import ClapModel, ClapProcessor
            log.info("loading %s (first start downloads it to HF_HOME)", self.model_id)
            self._processor = ClapProcessor.from_pretrained(self.model_id)
            model = ClapModel.from_pretrained(self.model_id)
            model.eval()
            self._model = model
            log.info("%s loaded on CPU", self.model_id)

    @torch.no_grad()
    def embed_audio(self, samples_48k: np.ndarray) -> torch.Tensor:
        self.load()
        inputs = self._processor(audios=[samples_48k], sampling_rate=CLAP_RATE, return_tensors="pt")
        return self._model.get_audio_features(**inputs)[0]

    @torch.no_grad()
    def embed_text(self, texts: Sequence[str]) -> torch.Tensor:
        self.load()
        inputs = self._processor(text=list(texts), return_tensors="pt", padding=True)
        return self._model.get_text_features(**inputs)


__all__: List[str] = ["Encoder", "ClapEncoder"]
