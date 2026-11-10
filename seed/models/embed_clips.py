"""CLAP embeddings of the evaluation clips, prepared exactly as the C4 worker prepares a car's clip:
5 s at 16 kHz (C2's fit: a random 5 s window, or zero-padded), then 48 kHz, then CLAP audio features."""
import glob, random, sys, time
from pathlib import Path
import os; sys.path.insert(0, os.environ["C4_SRC"])
import numpy as np, torch
from c4_worker.audio import decode_wav, resample_to_48k
from c4_worker.encoder import ClapEncoder
from c4_worker.config import CLASSES
root = Path(sys.argv[1]); out = Path(sys.argv[2])
def fit(x, rng, n=16000*5):
    if len(x) >= n:
        s = rng.randint(0, len(x)-n); return x[s:s+n]
    o = np.zeros(n, np.float32); off = rng.randint(0, n-len(x)); o[off:off+len(x)] = x; return o
enc = ClapEncoder(); enc.load(); rng = random.Random(2026)
X, y, names = [], [], []; t0 = time.time()
for ci, cls in enumerate(CLASSES):
    for f in sorted((root/cls).glob("*.wav")):
        x = fit(decode_wav(f.read_bytes()), rng)
        X.append(enc.embed_audio(resample_to_48k(x)).numpy()); y.append(ci)
        names.append((root/cls/(f.stem+".src")).read_text().strip())
    print(cls, len(y), f"{time.time()-t0:.0f}s", flush=True)
np.savez(out, X=np.stack(X), y=np.array(y), names=np.array(names))
