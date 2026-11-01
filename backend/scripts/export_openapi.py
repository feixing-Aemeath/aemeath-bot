import json
import tempfile
from pathlib import Path

from core.api import create_app
from core.config import Settings


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(
            Settings(
                token="schema-export-only-000000000000000000",
                data_dir=Path(directory),
                scheduler_enabled=False,
                _env_file=None,
            )
        )
        destination = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
        destination.write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(destination)


if __name__ == "__main__":
    main()
