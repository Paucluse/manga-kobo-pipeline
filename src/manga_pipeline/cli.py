"""CLI commands using Typer.

Commands:
    doctor   - Check environment configuration
    scan     - Scan inbox for new files
    process  - Process pending files
    run      - Watch inbox and process continuously
    status   - Show processing statistics
    retry    - Retry a failed task
    dry-run  - Preview processing without execution
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from manga_pipeline.config import PipelineConfig, get_config_path, load_config
from manga_pipeline.logging_config import get_logger, setup_logging

app = typer.Typer(
    name="manga-pipeline",
    help="Manga pipeline: scan -> parse -> convert (KCC) -> import (Komga) for Kobo Sage",
    no_args_is_help=True,
)

console = Console()
logger = get_logger(__name__)


def _load_and_init(
    config_file: Path | None = None,
) -> PipelineConfig:
    """Load config and initialize logging. Shared by all commands."""
    cfg = load_config(config_file)
    setup_logging(
        level=cfg.logging.level,
        log_format=cfg.logging.format,
        log_dir=cfg.paths.logs,
    )
    return cfg


@app.command()
def doctor(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Check environment: config, directories, KCC, Komga."""
    console.print("\n[bold]manga-pipeline doctor[/bold]\n")

    cfg = _load_and_init(config_file)
    all_ok = True

    # --- Config file ---
    cfg_path = get_config_path() if config_file is None else config_file
    if cfg_path and cfg_path.is_file():
        console.print(f"  [green]\u2713[/green] Config file: {cfg_path}")
    else:
        console.print("  [yellow]![/yellow] No config.yaml found, using defaults")

    # --- Directories ---
    console.print("\n[bold]Directories:[/bold]")
    dir_table = Table(show_header=True, header_style="bold")
    dir_table.add_column("Directory", style="cyan")
    dir_table.add_column("Path")
    dir_table.add_column("Status")

    dir_checks = [
        ("inbox", cfg.paths.inbox),
        ("processing", cfg.paths.processing),
        ("archive_cbz", cfg.paths.archive_cbz),
        ("kepub_ready", cfg.paths.kepub_ready),
        ("komga_library", cfg.paths.komga_library),
        ("state", cfg.paths.state),
        ("manual_review", cfg.paths.manual_review),
        ("logs", cfg.paths.logs),
    ]

    for name, path in dir_checks:
        if path.is_dir():
            writable = _check_writable(path)
            if writable:
                dir_table.add_row(name, str(path), "[green]\u2713 OK[/green]")
            else:
                dir_table.add_row(name, str(path), "[red]\u2717 Not writable[/red]")
                all_ok = False
        else:
            dir_table.add_row(name, str(path), "[red]\u2717 Missing[/red]")
            all_ok = False

    console.print(dir_table)

    # --- External commands ---
    console.print("\n[bold]External commands:[/bold]")
    cmd_table = Table(show_header=True, header_style="bold")
    cmd_table.add_column("Command", style="cyan")
    cmd_table.add_column("Path")
    cmd_table.add_column("Status")

    for cmd_name, cmd_path in [
        ("kcc", cfg.commands.kcc),
    ]:
        found = shutil.which(cmd_path)
        if found:
            cmd_table.add_row(cmd_name, found, "[green]\u2713 Found[/green]")
        else:
            cmd_table.add_row(cmd_name, cmd_path, "[red]\u2717 Not found[/red]")
            all_ok = False

    console.print(cmd_table)

    # --- Komga connection ---
    console.print("\n[bold]Komga server:[/bold]")
    try:
        import requests
        from requests.auth import HTTPBasicAuth

        resp = requests.get(
            f"{cfg.komga.base_uri}/api/v1/libraries",
            auth=HTTPBasicAuth(cfg.komga.user, cfg.komga.password),
            timeout=5,
        )
        if resp.status_code == 200:
            libs = resp.json()
            console.print(f"  [green]\u2713[/green] Connected to Komga at {cfg.komga.base_uri}")
            console.print(f"  [green]\u2713[/green] Found {len(libs)} library(ies)")
        else:
            console.print(f"  [red]\u2717[/red] Komga returned HTTP {resp.status_code}")
            all_ok = False
    except Exception as e:
        console.print(f"  [red]\u2717[/red] Cannot connect to Komga: {e}")
        all_ok = False

    # --- Komga library directory ---
    console.print("\n[bold]Komga library directory:[/bold]")
    if cfg.paths.komga_library.is_dir():
        console.print(f"  [green]\u2713[/green] Library directory exists: {cfg.paths.komga_library}")
    else:
        console.print(f"  [red]\u2717[/red] Library directory missing: {cfg.paths.komga_library}")
        all_ok = False

    # --- Summary ---
    console.print()
    if all_ok:
        console.print("[bold green]All checks passed![/bold green]\n")
    else:
        console.print(
            "[bold yellow]Some checks failed. See details above.[/bold yellow]\n"
        )

    raise typer.Exit(code=0 if all_ok else 1)


def _check_writable(path: Path) -> bool:
    """Check if a directory is writable by creating a temporary file."""
    try:
        test_file = path / ".pipeline_write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except OSError:
        return False


@app.command()
def scan(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Scan inbox directory for new manga files."""
    from manga_pipeline.database import Database
    from manga_pipeline.scanner import scan_inbox

    cfg = _load_and_init(config_file)
    db = Database(cfg.paths.state / "pipeline.db")

    try:
        discovered = scan_inbox(cfg.paths.inbox, db)
        if discovered:
            console.print(
                f"\n[green]Discovered {len(discovered)} new file(s)[/green]"
            )
            for rec in discovered:
                console.print(f"  - {rec.file_name}")
        else:
            console.print("\n[dim]No new files found.[/dim]")
    finally:
        db.close()


@app.command()
def process(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Process pending manga files through the pipeline."""
    from manga_pipeline.database import Database
    from manga_pipeline.pipeline import process_all_pending
    from manga_pipeline.scanner import scan_inbox

    cfg = _load_and_init(config_file)
    db = Database(cfg.paths.state / "pipeline.db")

    try:
        # First scan for new files
        discovered = scan_inbox(cfg.paths.inbox, db)
        if discovered:
            console.print(
                f"[green]Discovered {len(discovered)} new file(s)[/green]"
            )

        # Process all pending
        completed = process_all_pending(cfg, db)
        console.print(
            f"\n[bold]Processing complete: {completed} file(s) done[/bold]"
        )

        # Show status summary
        counts = db.get_status_counts()
        if counts:
            for s, count in sorted(counts.items()):
                console.print(f"  {s}: {count}")
    finally:
        db.close()


@app.command()
def run(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Watch inbox and process files continuously."""
    from manga_pipeline.database import Database
    from manga_pipeline.watcher import watch_inbox

    cfg = _load_and_init(config_file)
    db = Database(cfg.paths.state / "pipeline.db")

    try:
        watch_inbox(cfg, db)
    finally:
        db.close()


@app.command()
def status(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Show current processing status statistics."""
    from manga_pipeline.database import Database

    cfg = _load_and_init(config_file)
    db_path = cfg.paths.state / "pipeline.db"

    if not db_path.is_file():
        console.print(
            "[dim]No database found. Run 'scan' first.[/dim]"
        )
        return

    db = Database(db_path)
    try:
        counts = db.get_status_counts()
        if not counts:
            console.print("[dim]No records in database.[/dim]")
            return

        console.print("\n[bold]Processing Status:[/bold]")
        status_table = Table(show_header=True, header_style="bold")
        status_table.add_column("Status", style="cyan")
        status_table.add_column("Count", justify="right")

        total = 0
        for s, count in sorted(counts.items()):
            status_table.add_row(s, str(count))
            total += count

        status_table.add_row(
            "[bold]TOTAL[/bold]", f"[bold]{total}[/bold]"
        )
        console.print(status_table)
    finally:
        db.close()


@app.command()
def retry(
    task_id: int = typer.Option(
        ..., "--id", help="ID of the failed task to retry"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Retry a failed task by ID."""
    from manga_pipeline.database import Database
    from manga_pipeline.models import ProcessingStatus

    cfg = _load_and_init(config_file)
    db = Database(cfg.paths.state / "pipeline.db")

    try:
        record = db.get_record_by_id(task_id)
        if record is None:
            console.print(f"[red]Record {task_id} not found[/red]")
            raise typer.Exit(code=1)

        if record.current_status != ProcessingStatus.FAILED:
            console.print(
                f"[yellow]Record {task_id} is not failed "
                f"(status: {record.current_status})[/yellow]"
            )
            raise typer.Exit(code=1)

        db.update_status(
            task_id, ProcessingStatus.DISCOVERED, error_message=""
        )
        console.print(
            f"[green]Record {task_id} reset to discovered. "
            f"Run 'process' to retry.[/green]"
        )
    finally:
        db.close()


@app.command(name="dry-run")
def dry_run(
    file_path: str = typer.Argument(help="Path to manga file to preview"),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
) -> None:
    """Preview processing without execution."""
    from manga_pipeline.filename_parser import parse_filename
    from manga_pipeline.kcc import build_kcc_command

    cfg = _load_and_init(config_file)
    p = Path(file_path)

    console.print(f"\n[bold]Dry-run: {p.name}[/bold]\n")

    # Parse filename
    parsed = parse_filename(p.name)
    console.print("[bold]Parsed metadata:[/bold]")
    console.print(f"  Title:      {parsed.title or '(none)'}")
    console.print(f"  Author:     {parsed.author or '(none)'}")
    console.print(f"  Publisher:  {parsed.publisher or '(none)'}")
    console.print(f"  Series:     {parsed.series or '(none)'}")
    console.print(f"  Volume:     {parsed.volume or '(none)'}")
    console.print(f"  Confidence: {parsed.confidence:.2f}")

    threshold = cfg.metadata.confidence_auto_accept
    if parsed.confidence < threshold:
        console.print(
            f"\n  [yellow]! Confidence below threshold "
            f"({threshold}), would enter manual review[/yellow]"
        )

    # KCC command
    kcc_cmd = build_kcc_command(
        input_path=p,
        output_dir=cfg.paths.kepub_ready,
        kcc_cmd=cfg.commands.kcc,
        kobo_config=cfg.kobo,
    )
    console.print("\n[bold]KCC command:[/bold]")
    console.print(f"  {' '.join(kcc_cmd)}")

    # Komga destination
    series_name = parsed.series or parsed.title or p.stem
    dest = cfg.paths.komga_library / series_name / f"{p.stem}.kepub.epub"
    console.print("\n[bold]Komga destination:[/bold]")
    console.print(f"  {dest}")
    console.print()

