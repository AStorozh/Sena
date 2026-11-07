"""Semantic fingerprints of text (embeddings) and similarity. Computed in batches by whoever the "embed" role names
(core/roles.py: the home model by default, the cloud one if moved); where to store them and how to search is the memory
index's job (memory/index.py)."""
import numpy as np

from sena.core import roles

_BATCH = 100   # texts per request


def embed(texts, query=False):
    """Vectors for the list of texts, in the same order; identical texts are computed only once.
    query — these are questions, not records: some models fingerprint the two differently."""
    unique = list(dict.fromkeys(t[:2000] for t in texts))
    found = {}
    for i in range(0, len(unique), _BATCH):
        chunk = unique[i:i + _BATCH]
        for text, vec in zip(chunk, roles.embed(chunk, query=query)):
            found[text] = np.asarray(vec, dtype=np.float32)
    return [found[t[:2000]] for t in texts]


def stamp():
    """Which model and width the fingerprints come from now: the registry stamps every vector with it."""
    return roles.embed_stamp()


def cosine(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / norm) if norm else 0.0
