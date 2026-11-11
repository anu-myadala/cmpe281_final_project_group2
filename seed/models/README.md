# Sound-classifier versions of the seed

The seed registers two real versions of `sound-classifier` with the scores below. Both are artifacts the
C4 worker (`c3/worker`) loads: `1.1.0` is CLAP zero-shot with text prompts (no training, softmax temperature
0.05, the worker's default) and is ACTIVE
after the seed; `1.2.0` is a small head (512 -> 128 -> 7) trained on CLAP embeddings and is REGISTERED.

Data: the course Smart Car `Audio_Datasets` folder (professor's shared drive). 787 clips, one class per
folder: Emergency_sirens (ambulance files) -> siren, car_crash_dataset -> crash, glass_breaking_dataset ->
glass_break, Human_Scream (originals, no `_aug` copies) -> scream, Alert_sounds (car horns, not the `2-`
negatives) -> honking, road_traffic_dataset -> traffic, Environmental_Sounds (originals) -> environmental;
at most 120 per class. Each clip is prepared like a car's clip: 16 kHz mono, a 5 s window (or padded),
then CLAP `laion/clap-htsat-unfused`. Split: 550 training and 237 test clips, stratified, `random_state=281`.
Both versions are scored on the same 237 test clips; `1.2.0` never saw them.

| Class | F1 1.1.0 | F1 1.2.0 | F1 1.3.0 | Test clips |
| --- | --- | --- | --- | --- |
| siren | 0.91 | 1.00 | 1.00 | 36 |
| crash | 0.68 | 0.98 | 0.98 | 27 |
| glass_break | 0.85 | 0.98 | 0.98 | 30 |
| scream | 0.93 | 0.99 | 0.97 | 36 |
| honking | 0.69 | 1.00 | 1.00 | 36 |
| traffic | 0.57 | 1.00 | 1.00 | 36 |
| environmental | 0.14 | 0.99 | 1.00 | 36 |
| **macro-F1** | **0.68** | **0.99** | **0.99** | 237 |
| accuracy | 0.72 | 0.99 | 0.99 | |

`evaluation.json` holds every number, the confusion matrices and the test file names. Files of one class
may be cut from the same longer recordings, so the scores of `1.2.0` and `1.3.0` are likely higher than on new audio.

## 1.3.0: the version uploaded in the demo

`1.3.0/model.pt` is not seeded. It is the file the administrator uploads on the AI models page in the
demo (Upload new version), so that the registry shows the whole path: upload, checksum, REGISTERED,
activation, deployment to the worker, rollback. It is the same head as `1.2.0`, trained on the same 550
training clips with label smoothing 0.1 (`train_1_3_0.py`) and scored on the same 237 test clips. Its
scores equal those of `1.2.0` to two decimals, but it is less overconfident: its mean top confidence on the
test clips is 0.88 against 0.99 for `1.2.0`, which matters because C3 compares the confidence with its rule
thresholds. `1.3.0/metrics.json` holds its scores; `1.3.0/per-class.json` is the per-class block to paste into
the upload dialog (macro-F1 0.99, accuracy 0.99).

Rebuild (needs the clips as `clips/<class>/NNN.wav`, each with `NNN.src` holding its dataset file name, and
torch, transformers, scikit-learn):

```bash
C4_SRC=c3/worker python seed/models/embed_clips.py clips emb.npz
C4_SRC=c3/worker python seed/models/train_and_score.py emb.npz out/
C4_SRC=c3/worker python seed/models/train_1_3_0.py emb.npz seed/models/1.3.0
```
