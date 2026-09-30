import hashlib

import numpy as np
import pytest

from app.config import Config
from app.markdown import search_tokens
from app.service import Service


class FakeEmbedder:
    """Deterministic test vectors, never used to claim semantic-search quality."""

    ready = True
    key = "test-vector-v1"
    calls = 0

    def embed(self, texts, query=False):
        self.calls += len(texts)
        vectors = []
        for text in texts:
            vector = np.zeros(384, dtype=np.float32)
            for term in search_tokens(text).split():
                index = int(hashlib.sha256(term.encode()).hexdigest()[:8], 16) % 384
                vector[index] += 1
            vector /= max(float(np.linalg.norm(vector)), 1)
            vectors.append(vector.tobytes())
        return vectors


@pytest.fixture
def service(tmp_path):
    root = (tmp_path / "vault").resolve()
    root.mkdir()
    svc = Service(Config(data_dir=tmp_path / "data", debounce=0.1), FakeEmbedder())
    svc.store.put("vault", str(root))
    svc.store.put("excludes", ["Private"])
    yield svc
    svc.close()


def write(service, path, text):
    file = service.indexer.root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    return file
