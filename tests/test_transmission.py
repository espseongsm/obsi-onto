from app.config import Config
from app.transmission import transmission_status


def test_recipient_hides_path_query_credentials_and_independent_permissions(tmp_path):
    config = Config(
        data_dir=tmp_path,
        generation_url="https://models.example/private-account/v1?secret=private-value",
        generation_model="test",
        external_generation=True,
        external_suggestions=False,
        embedding_provider="external",
        embedding_url="https://embedding.example/v1",
        external_embedding=True,
    )
    status = transmission_status(config, True)
    assert status["generation"]["recipient"] == "https://models.example"
    assert status["embedding"]["recipient"] == "https://embedding.example"
    assert status["generation"]["external"] is True
    assert status["generation"]["enabled"] is True
    assert status["suggestions"]["enabled"] is False
    assert "private" not in str(status)
    config.external_suggestions = True
    assert transmission_status(config, True)["suggestions"]["enabled"] is True


def test_local_and_invalid_endpoints_are_not_reported_as_external_sending(tmp_path):
    config = Config(data_dir=tmp_path, generation_url="http://[::1]:8080/v1")
    status = transmission_status(config, True)
    assert status["embedding"]["recipient"] == "This computer"
    assert status["generation"]["recipient"] == "http://[::1]:8080"
    assert status["generation"]["external"] is False
    config.generation_url = "https://user:secret@models.example"
    status = transmission_status(config, True)
    assert status["generation"]["enabled"] is False
    assert "secret" not in str(status)
    assert "models.example" not in str(status)
