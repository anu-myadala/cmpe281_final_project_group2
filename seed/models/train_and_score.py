"""Builds the two sound-classifier versions and scores both on the same held-out clips.
1.1.0 zero_shot: CLAP + text prompts (C4's format, no training), softmax temperature 0.05 (C4's default).
1.2.0 head: Linear(512,128)-ReLU-Dropout-Linear(128,7) trained on CLAP embeddings of the training clips."""
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
X, y, names = torch.tensor(d["X"]).float(), d["y"], d["names"]
idx = np.arange(len(y))
tr, te = train_test_split(idx, test_size=0.3, random_state=281, stratify=y)
PROMPTS = {
    "siren": ["an ambulance siren", "a police car siren wailing", "a fire truck siren"],
    "crash": ["a car crash", "a loud vehicle collision", "metal crunching in a car accident"],
    "glass_break": ["breaking glass", "a window shattering", "glass smashing"],
    "scream": ["a person screaming", "a woman screaming in fear", "a loud human scream"],
    "honking": ["a car horn honking", "a vehicle horn beeping", "drivers honking their horns"],
    "traffic": ["road traffic noise", "cars driving past on a street", "the hum of traffic on a highway"],
    "environmental": ["ambient environmental sounds", "quiet background noise", "wind and birds outdoors"],
}
def metrics(true, pred):
    p, r, f, s = precision_recall_fscore_support(true, pred, labels=range(len(CLASSES)), zero_division=0)
    return {"macroF1": round(float(f.mean()), 4), "accuracy": round(float(accuracy_score(true, pred)), 4),
            "perClass": {c: {"precision": round(float(p[i]), 4), "recall": round(float(r[i]), 4),
                             "f1": round(float(f[i]), 4), "support": int(s[i])} for i, c in enumerate(CLASSES)},
            "confusion": confusion_matrix(true, pred, labels=range(len(CLASSES))).tolist()}
def save(obj, name):
    p = out/name/"model.pt"; p.parent.mkdir(exist_ok=True); torch.save(obj, p)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()
enc = ClapEncoder(); enc.load()
# 1.1.0 zero-shot, scored through C4's own loader
zs_path, zs_sha = save({"kind": "zero_shot", "classes": list(CLASSES), "prompts": PROMPTS, "temperature": 0.05}, "1.1.0")
zs = load_artifact(zs_path, enc)
zs_pred = [CLASSES.index(zs.classify(X[i])[0][0]) for i in te]
m_zs = metrics(y[te], zs_pred)
# 1.2.0 head trained on the training clips only
torch.manual_seed(281)
net = build_head(); opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
Xtr, ytr = F.normalize(X[tr], dim=-1), torch.tensor(y[tr])
counts = torch.bincount(ytr, minlength=len(CLASSES)).float(); w = (counts.sum()/counts)/len(CLASSES)
for epoch in range(300):
    net.train(); perm = torch.randperm(len(tr))
    for b in range(0, len(tr), 64):
        j = perm[b:b+64]; opt.zero_grad()
        loss = F.cross_entropy(net(Xtr[j]), ytr[j], weight=w); loss.backward(); opt.step()
net.eval()
hd_path, hd_sha = save({"kind": "head", "state_dict": net.state_dict(), "classes": list(CLASSES), "embedding_dim": 512}, "1.2.0")
hd = load_artifact(hd_path, enc)
hd_pred = [CLASSES.index(hd.classify(X[i])[0][0]) for i in te]
m_hd = metrics(y[te], hd_pred)
report = {"dataset": "CMPE 281 Smart Car Audio_Datasets (professor's shared drive), 787 clips, 7 classes",
          "split": {"train": int(len(tr)), "test": int(len(te)), "random_state": 281, "stratified": True},
          "clipPrep": "5 s at 16 kHz like a car's clip (random 5 s window or zero-padded), CLAP laion/clap-htsat-unfused",
          "1.1.0": {"kind": "zero_shot", "sha256": zs_sha, "sizeBytes": zs_path.stat().st_size, **m_zs},
          "1.2.0": {"kind": "head", "sha256": hd_sha, "sizeBytes": hd_path.stat().st_size, "trainedOn": int(len(tr)), **m_hd},
          "testFiles": [str(names[i]) for i in te]}
(out/"evaluation.json").write_text(json.dumps(report, indent=2))
for v in ("1.1.0", "1.2.0"):
    r = report[v]; print(v, r["kind"], "macroF1", r["macroF1"], "acc", r["accuracy"])
    print("   ", {c: r["perClass"][c]["f1"] for c in CLASSES})
