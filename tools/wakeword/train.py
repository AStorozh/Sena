import os, sys, glob, time, logging
import numpy as np
import torch
from openwakeword.utils import AudioFeatures
from augment import augment

# Custom training of the "Sena" detector (second attempt). Differences from the stock auto_train:
# gentle noise (speech stays intelligible), the model is picked by test recall under a cap on false activations per hour.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
W = os.path.dirname(os.path.abspath(__file__))
C, D, F = os.path.join(W, "clips"), os.path.join(W, "data"), os.path.join(W, "features")
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
Fx = AudioFeatures(device="cpu")
bg = glob.glob(os.path.join(D, "background", "*.wav")); rir = glob.glob(os.path.join(D, "rirs", "*.wav"))


def features(split, rounds, seed):
    # clean clips + `rounds` gently noised copies; cached on disk
    out = os.path.join(F, f"v3_{split}.npy")
    if os.path.exists(out):
        return np.load(out)
    files = sorted(glob.glob(os.path.join(C, split, "*", "*.wav")))
    clean = augment(files, bg, rir, rounds=1, seed=seed, p_bg=0.0, p_rir=0.0)
    noisy = augment(files, bg, rir, rounds=rounds, seed=seed + 1)
    hard = augment(files, bg, rir, rounds=1, seed=seed + 2, snr=(0, 12), p_bg=1.0, p_rir=0.6)   # a harder round: a noisy cafe, a street
    t = time.time()
    x = np.array([Fx._get_embeddings(a) for a in clean + noisy + hard], np.float32)
    logging.info(f"{split}: {len(files)} клипов -> {len(x)} примеров за {time.time()-t:.0f} с")
    np.save(out, x)
    return x


pos, neg = features("pos_train", 3, 10), features("neg_train", 2, 20)
pos_t, neg_t = features("pos_test", 1, 30), features("neg_test", 1, 40)
acav = np.load(os.path.join(D, "acav.npy"), mmap_mode="r")
val = np.load(os.path.join(D, "validation_set_features.npy"))
val = torch.from_numpy(np.array([val[i:i + 16] for i in range(0, len(val) - 16, 4)], np.float32))  # windows with a step of 4 frames (~0.3 s)
VAL_HOURS = 11.3


class Net(torch.nn.Module):
    def __init__(self):
        super().__init__()
        # features spread ~16, so divide by 16: otherwise the net saturates at the start and stops learning
        self.net = torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(16 * 96, 256), torch.nn.ReLU(), torch.nn.Linear(256, 64), torch.nn.ReLU(), torch.nn.Linear(64, 1))

    def forward(self, x):
        return self.net(x / 16.0)   # logit

    def prob(self, x):
        return torch.sigmoid(self(x))


torch.manual_seed(0)
net = Net(); opt = torch.optim.Adam(net.parameters(), 1e-3, weight_decay=1e-5)
P, N = torch.from_numpy(pos), torch.from_numpy(neg)
Pt, Nt = torch.from_numpy(pos_t), torch.from_numpy(neg_t)
rng = np.random.default_rng(0)
best, best_state, best_thr = None, None, 0.5
t0 = time.time()
for step in range(1, STEPS + 1):
    idx = np.sort(rng.choice(acav.shape[0], 1024, replace=False))   # proportions as in the stock recipe: 1024 of other speech per 50+50
    xa = torch.from_numpy(np.asarray(acav[idx], np.float32))
    xp = P[torch.randint(0, len(P), (50,))]; xn = N[torch.randint(0, len(N), (50,))]
    x = torch.cat([xp, xn, xa]); y = torch.cat([torch.ones(50), torch.zeros(50 + 1024)])
    neg_w = 1.0 + 59.0 * step / STEPS   # the false-activation penalty grows to 60: at 300 the net learned to just stay silent
    w = torch.cat([torch.ones(50), torch.full((50,), neg_w), torch.full((1024,), neg_w)])
    loss = torch.nn.functional.binary_cross_entropy_with_logits(net(x).squeeze(1), y, weight=w)
    opt.zero_grad(); loss.backward(); opt.step()
    if step % 1000 == 0:
        with torch.no_grad():
            pp, pn, pv = net.prob(Pt).squeeze(1), net.prob(Nt).squeeze(1), net.prob(val).squeeze(1)
        # the best model and threshold: the highest recall among thresholds with at most 1 false activation per hour
        cands = [((pp >= t).float().mean().item(), t, (pv >= t).sum().item() / VAL_HOURS, (pn >= t).float().mean().item()) for t in (0.3, 0.5, 0.7, 0.8, 0.9, 0.95)]
        ok = [c for c in cands if c[2] <= 1.0]
        rp, thr, fph, fn = max(ok) if ok else min(cands, key=lambda c: c[2])
        logging.info(f"шаг {step}: loss={loss.item():.3f} порог={thr} полнота={rp:.3f} ложных на похожих={fn:.3f} ложных/час={fph:.1f}  {time.time()-t0:.0f} с")
        score = rp if ok else -fph
        if best is None or score > best:
            best, best_state, best_thr = score, {k: v.clone() for k, v in net.state_dict().items()}, thr

net.load_state_dict(best_state)
with torch.no_grad():
    pt, nt_, pv = net.prob(Pt).squeeze(1).numpy(), net.prob(Nt).squeeze(1).numpy(), net.prob(val).squeeze(1).numpy()
logging.info(f"ЛУЧШИЙ ПОРОГ: {best_thr}")
for thr in (0.3, 0.5, 0.7, 0.8, 0.9, 0.95):
    logging.info(f"порог {thr}: полнота={(pt >= thr).mean():.3f} ложных на похожих={(nt_ >= thr).mean():.3f} ложных/час={(pv >= thr).sum() / VAL_HOURS:.2f}")
out = os.path.join(W, "sena.onnx")
class Export(torch.nn.Module):
    def __init__(self, n): super().__init__(); self.n = n
    def forward(self, x): return torch.sigmoid(self.n(x))
torch.onnx.export(Export(net.eval()), torch.rand(1, 16, 96), out, input_names=["input"], output_names=["sena"], opset_version=13, dynamo=False)
logging.info("МОДЕЛЬ СОХРАНЕНА: " + out)
