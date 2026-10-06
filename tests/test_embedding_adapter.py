"""Exercise the installed FastEmbed adapter without a model download or ONNX session."""

import hashlib
import json
from importlib.metadata import version

import numpy as np
from fastembed import TextEmbedding
from fastembed.text.onnx_embedding import OnnxTextEmbedding

from app.config import LOCAL_MODEL, Config
from app.models import Embedder


def test_installed_fastembed_weight_path_and_local_cache_contract(tmp_path, monkeypatch):
    description = next(
        model for model in TextEmbedding.list_supported_models() if model["model"] == LOCAL_MODEL
    )
    assert description["dim"] == 384
    model_root = tmp_path / "model-fixture"
    weights = model_root / description["model_file"]
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"offline model weight fixture")
    downloads, loaded, inferred = [], [], []

    def download(model_description, cache_dir, *, local_files_only, **options):
        assert model_description.model == LOCAL_MODEL
        downloads.append((cache_dir, local_files_only))
        return model_root

    def inference(model, documents, batch_size=256, **options):
        documents = list(documents)
        inferred.append((documents, batch_size))
        return iter([np.ones(384, dtype=np.float32) for _ in documents])

    monkeypatch.setattr(OnnxTextEmbedding, "download_model", staticmethod(download))
    monkeypatch.setattr(OnnxTextEmbedding, "load_onnx_model", lambda model: loaded.append(model))
    monkeypatch.setattr(OnnxTextEmbedding, "embed", inference)
    config = Config(data_dir=tmp_path / "data")
    embedder = Embedder(config)
    embedder.prepare()
    checksum = hashlib.sha256(weights.read_bytes()).hexdigest()
    assert embedder.fingerprint == f"{LOCAL_MODEL}:{checksum}:fastembed{version('fastembed')}"
    assert embedder.model.model._model_dir == model_root
    assert embedder.model.model.model_description.model_file == description["model_file"]
    assert embedder.model.model.providers == ["CPUExecutionProvider"]
    assert embedder.model.model.threads == 2
    assert len(embedder.embed(["source passage"])) == 1

    old_fingerprint = f"{LOCAL_MODEL}:{checksum}:fastembed0.7.4"
    embedder.manifest.write_text(json.dumps({"fingerprint": old_fingerprint, "extra": "preserve"}))
    restarted = Embedder(config)
    assert restarted.ready and restarted.key == embedder.key
    assert restarted.fingerprint != old_fingerprint
    assert restarted.key != f"{old_fingerprint}:dim384:input-v1"
    assert downloads == [(str(embedder.cache), False)]
    assert json.loads(restarted.manifest.read_text()) == {
        "fingerprint": embedder.fingerprint,
        "extra": "preserve",
    }
    assert len(restarted.embed(["search question"], query=True)) == 1
    assert downloads == [(str(embedder.cache), False), (str(embedder.cache), True)]
    assert len(loaded) == 2
    assert inferred == [(["source passage"], 16), (["search question"], 16)]
