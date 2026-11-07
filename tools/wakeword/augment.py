import numpy as np, wave, random, scipy.signal
# Gentle noise mixing: speech stays intelligible. Background 5..25 dB below the speech, room echo with probability 0.4, a random place in the window
def read(path):
    w = wave.open(path); return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
def augment(files, bgs, rirs, total=32000, rounds=1, seed=0, snr=(5, 25), p_bg=0.8, p_rir=0.4):
    rng = random.Random(seed); out = []
    for r in range(rounds):
        for f in files:
            x = read(f)[:total]
            if rng.random() < p_rir:
                h = read(rng.choice(rirs)); h = h / (np.abs(h).max() + 1e-6)
                x = scipy.signal.fftconvolve(x, h)[:len(x)] * (np.abs(read(f)).max() / (np.abs(x).max() + 1e-6)) if len(h) else x
            pad = total - len(x); start = rng.randint(min(pad, 1600), pad) if pad > 0 else 0   # the word near the end of the window, as in a live stream
            y = np.zeros(total, np.float32); y[start:start + len(x)] = x[:total - start]
            if rng.random() < p_bg:
                b = read(rng.choice(bgs)); off = rng.randint(0, max(0, len(b) - total)); b = b[off:off + total]
                if len(b) < total: b = np.pad(b, (0, total - len(b)))
                s = 10 ** (rng.uniform(*snr) / 20); b = b * (np.sqrt((x ** 2).mean() + 1e-6) / (np.sqrt((b ** 2).mean() + 1e-6) * s))
                y = y + b
            y = y * rng.uniform(0.3, 1.0) * (32000 / max(np.abs(y).max(), 1))   # random loudness, without clipping
            out.append(np.clip(y, -32768, 32767).astype(np.int16))
    return out
