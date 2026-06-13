"""Service layer over the launchers.

Keeps the route handlers free of any direct launcher / subprocess imports — they
talk to this module, this module owns the model lifecycle and delegates the
actual process spawning to app.launcher.*.
"""
from app.launcher.embedding_launcher import (launch_embedding_server,
                                             stop_embedding_server)
from app.launcher.llm_launcher import (launch_single_llm_model,
                                       stop_single_llm_model)

EMBEDDING_KEY = "Embedding & reranking Server"


def start_llm(app, model_name: str, config_path: str) -> None:
    launch_single_llm_model(app, model_name, config_path)


def stop_llm(app, model_name: str) -> None:
    stop_single_llm_model(app, model_name)


def start_embedding(app, config_path: str) -> None:
    """Start the embedding/reranking server, tracking it in `starting_models`."""
    app.state.starting_models.add(EMBEDDING_KEY)
    try:
        launch_embedding_server(config_path)
    finally:
        app.state.starting_models.discard(EMBEDDING_KEY)


def stop_embedding() -> None:
    stop_embedding_server()
