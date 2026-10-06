"""Public model recipients and input scope, without credentials or URL paths."""

from urllib.parse import urlsplit

from app.models import validate_endpoint


def _destination(url, allowed):
    if not url:
        return None
    try:
        validate_endpoint(url, allowed)
        parsed = urlsplit(url)
        host = parsed.hostname
        external = host not in {"localhost", "127.0.0.1", "::1"}
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return {"recipient": f"{parsed.scheme}://{host}{port}", "external": external}
    except ValueError:
        return None


def transmission_status(config, generation_enabled):
    embedding = (
        _destination(config.embedding_url, config.external_embedding)
        if config.embedding_provider == "external"
        else None
    )
    generation = _destination(config.generation_url, config.external_generation)
    return {
        "embedding": {
            "enabled": config.embedding_provider == "local" or embedding is not None,
            **(
                embedding
                or {
                    "recipient": "This computer"
                    if config.embedding_provider == "local"
                    else "Not configured",
                    "external": False,
                }
            ),
            "scope": (
                "Note titles, headings and passages requiring new embeddings; search questions."
            ),
        },
        "generation": {
            "enabled": generation_enabled and generation is not None,
            **(generation or {"recipient": "Not configured", "external": False}),
            "scope": (
                "Your question, selected source passages, dates and headings, clarifications, "
                "and up to 20 recent conversation turns. Relationship requests include "
                "the selected source passages."
            ),
        },
        "suggestions": {
            "enabled": generation_enabled
            and generation is not None
            and (not config.external_generation or config.external_suggestions),
            **(generation or {"recipient": "Not configured", "external": False}),
            "scope": (
                "Titles, dates and excerpts of up to 600 characters from up to 24 "
                "non-excluded notes when vault settings change."
            ),
        },
    }
