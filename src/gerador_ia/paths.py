from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
JSON_INPUT_DIR = INPUTS_DIR / "json"
OUTPUTS_DIR = DATA_DIR / "outputs"
RAW_OUTPUT_DIR = OUTPUTS_DIR / "raw"
ORGANIZED_OUTPUT_DIR = OUTPUTS_DIR / "organized"


def resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

