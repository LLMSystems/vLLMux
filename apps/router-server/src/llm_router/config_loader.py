import yaml


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)
    
def get_model_route_table(path: str) -> dict:
    config = load_config(path)
    engines = config.get("LLM_engines", {})
    route_table = {}
    for name, settings in engines.items():
        port = settings.get("port")
        if port:
            route_table[name] = f"http://localhost:{port}/v1"
    return route_table