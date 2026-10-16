"""Entry point for ZenML pipelines.

python run.py data                      # run with configs/data_pipeline.yaml
python run.py data --keep-code          # override a parameter
"""

from __future__ import annotations

from pathlib import Path

import typer
from loguru import logger

app = typer.Typer(help="Run ZenML pipelines.", no_args_is_help=True)
CONFIGS = Path(__file__).parent / "configs"


@app.callback()
def main() -> None:
    """Keep subcommand dispatch while only one pipeline exists."""


@app.command()
def data(
    keep_code: bool = typer.Option(False, "--keep-code", help="Keep code blocks in chunks."),
    config: bool = typer.Option(True, help="Apply configs/data_pipeline.yaml."),
) -> None:
    """ingest -> chunk -> embed."""
    from pipelines.data import data_pipeline

    pipeline = data_pipeline
    if config:
        cfg = CONFIGS / "data_pipeline.yaml"
        if cfg.exists():
            pipeline = pipeline.with_options(config_path=str(cfg))
            logger.info(f"using {cfg}")

    run = pipeline(keep_code=keep_code)
    # The response object is enormous; print only what a human needs.
    logger.info(f"run {run.name} -> {run.status.value}")
    for name, step_run in run.steps.items():
        logger.info(f"  {name}: {step_run.status.value}")


if __name__ == "__main__":
    app()
