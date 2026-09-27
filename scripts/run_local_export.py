"""Explicit finite mock-mode export worker, for one already-created request."""
import argparse
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
from idp_app.core.config import IdpMode, Settings
from idp_app.services.export_jobs import build_exports, run_export


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_id", type=UUID)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(mode=IdpMode.MOCK, local_data_dir=args.data_dir)
    requests, sources, artifacts = build_exports(settings)
    run_export(requests, sources, artifacts, str(args.export_id))


if __name__ == "__main__":
    main()
