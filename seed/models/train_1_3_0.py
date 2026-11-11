"""sound-classifier 1.3.0: the same head as 1.2.0 (512 -> 128 -> 7 on CLAP embeddings), trained on the same
550 training clips with label smoothing 0.1, and scored on the same 237 held-out clips."""
import json, os, sys, hashlib
sys.path.insert(0, os.environ["C4_SRC"])
from pathlib import Path
import numpy as np, torch, torch.nn.functional as F
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_recall_fscore_support, accuracy_score, confusion_matrix
from c4_worker.config import CLASSES
from c4_worker.classifiers import build_head, load_artifact
from c4_worker.encoder import ClapEncoder
d = np.load(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
X, y = torch.tensor(d["X"]).float(), d["y"]
tr, te = train_test_split(np.arange(len(y)), test_size=0.3, random_state=281, stratify=y)
torch.manual_seed(1300)
net = build_head(); opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
Xtr, ytr = F.normalize(X[tr], dim=-1), torch.tensor(y[tr])
counts = torch.bincount(ytr, minlength=len(CLASSES)).float(); w = (counts.sum() / counts) / len(CLASSES)
for epoch in range(300):
    net.train(); perm = torch.randperm(len(tr))
    for b in range(0, len(tr), 64):
        j = perm[b:b + 64]; opt.zero_grad()
        F.cross_entropy(net(Xtr[j]), ytr[j], weight=w, label_smoothing=0.1).backward(); opt.step()
net.eval()
p = out / "model.pt"; torch.save({"kind": "head", "state_dict": net.state_dict(), "classes": list(CLASSES), "embedding_dim": 512}, p)
clf = load_artifact(p, ClapEncoder())          # C4's own loader; a head needs no CLAP text weights
res = [clf.classify(X[i])[0] for i in te]
pred = [CLASSES.index(c) for c, _ in res]; conf = np.array([q for _, q in res])
pr, rc, f1, sup = precision_recall_fscore_support(y[te], pred, labels=range(len(CLASSES)), zero_division=0)
m = {"version": "1.3.0", "kind": "head", "training": "550 course clips, label smoothing 0.1, 300 epochs, seed 1300",
     "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "sizeBytes": p.stat().st_size,
     "macroF1": round(float(f1.mean()), 4), "accuracy": round(float(accuracy_score(y[te], pred)), 4),
     "perClass": {c: {"precision": round(float(pr[i]), 4), "recall": round(float(rc[i]), 4), "f1": round(float(f1[i]), 4),
                      "support": int(sup[i])} for i, c in enumerate(CLASSES)},
     "confusion": confusion_matrix(y[te], pred, labels=range(len(CLASSES))).tolist(),
     "meanTopConfidence": round(float(conf.mean()), 4)}
(out / "metrics.json").write_text(json.dumps(m, indent=2))
print(json.dumps({k: m[k] for k in ("macroF1", "accuracy", "meanTopConfidence", "sha256", "sizeBytes")}))
print({c: m["perClass"][c]["f1"] for c in CLASSES})
