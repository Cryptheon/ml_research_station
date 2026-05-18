"""ResearchStation CLI — paper ingestion and database management commands."""

from __future__ import annotations

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

app = typer.Typer(
    name="rs",
    help="ResearchStation paper ingestion and management CLI.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # Silence noisy third-party loggers
    for name in ("httpx", "httpcore", "openreview", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ── ingest command ────────────────────────────────────────────────────────────


@app.command()
def ingest(
    sources: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help="arxiv | biorxiv | openreview  (repeat for multiple)"),
    ] = None,
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Calendar days to look back"),
    ] = 7,
    max_results: Annotated[
        int,
        typer.Option("--max-results", "-m", help="Max papers per source"),
    ] = 100,
    download_pdfs: Annotated[
        bool,
        typer.Option("--download-pdfs", help="Download PDFs to data/papers/"),
    ] = False,
    no_enrich: Annotated[
        bool,
        typer.Option("--no-enrich", help="Skip Semantic Scholar enrichment"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch and display without writing to DB"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v"),
    ] = False,
) -> None:
    """[bold]Fetch recent papers[/bold] and store them in the local database."""
    _setup_logging(verbose)

    from .config.settings import get_settings
    from .ingestion.pipeline import IngestionPipeline

    settings = get_settings()
    if max_results != 100:
        settings.preferences.max_results_per_query = max_results

    console.rule("[bold cyan]ResearchStation — Ingestion")
    console.print(f"Sources: {sources or 'all'}  |  Days back: {days}  |  Dry run: {dry_run}")

    pipeline = IngestionPipeline(settings)
    result = pipeline.run(
        days_lookback=days,
        sources=sources,
        download_pdfs=download_pdfs,
        enrich=not no_enrich,
        dry_run=dry_run,
    )

    if result.errors:
        console.print(f"\n[red]Errors ({len(result.errors)}):")
        for err in result.errors[:10]:
            console.print(f"  [red dim]• {err}")


# ── stats command ─────────────────────────────────────────────────────────────


@app.command()
def stats() -> None:
    """Show [bold]database statistics[/bold]."""
    _setup_logging(False)

    from .config.settings import get_settings
    from .database.engine import build_engine, build_session_factory, get_session
    from .database.repository import CitationRepository, PaperRepository

    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    session_factory = build_session_factory(engine)

    with get_session(session_factory) as session:
        paper_repo = PaperRepository(session)
        citation_repo = CitationRepository(session)

        total_papers = paper_repo.count()
        by_source = paper_repo.count_by_source()
        total_citations = citation_repo.count()

    table = Table(title="ResearchStation — Database Stats", show_header=True)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right")

    table.add_row("Total papers", str(total_papers))
    table.add_row("Citation edges", str(total_citations))
    for source, count in sorted(by_source.items()):
        table.add_row(f"  ↳ {source}", str(count))

    db_path = settings.database.sqlite_path
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024**2)
        table.add_row("DB size", f"{size_mb:.2f} MB")

    console.print(table)


# ── search command ────────────────────────────────────────────────────────────


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search term (title + abstract)")],
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    source: Annotated[str | None, typer.Option("--source", "-s")] = None,
) -> None:
    """[bold]Search[/bold] the local paper database."""
    _setup_logging(False)

    from .config.settings import get_settings
    from .database.engine import build_engine, build_session_factory, get_session
    from .database.repository import PaperRepository

    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    session_factory = build_session_factory(engine)

    with get_session(session_factory) as session:
        results = PaperRepository(session).search(
            query=query,
            sources=[source] if source else None,
            limit=limit,
        )

    if not results:
        console.print("[yellow]No results found.")
        return

    table = Table(title=f"Search results for '{query}'", show_header=True)
    table.add_column("ID", style="dim", width=20)
    table.add_column("Title", max_width=60)
    table.add_column("Venue", width=12)
    table.add_column("Date", width=11)
    table.add_column("Cites", justify="right", width=6)

    for paper in results:
        table.add_row(
            paper.id,
            paper.title[:80],
            paper.venue or paper.source.value,
            paper.published_date.strftime("%Y-%m-%d"),
            str(paper.citation_count or "—"),
        )

    console.print(table)


# ── summarize command ─────────────────────────────────────────────────────────


@app.command()
def summarize(
    paper_id: Annotated[str, typer.Argument(help="Canonical paper ID, e.g. 'arxiv:2301.00001'")],
    think: Annotated[
        bool,
        typer.Option("--think", help="Enable extended thinking (Anthropic / Ollama models only)"),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """[bold]Summarise[/bold] a single paper using the configured LLM."""
    _setup_logging(verbose)

    from .config.settings import get_settings
    from .database.engine import build_engine, build_session_factory, get_session
    from .database.repository import PaperRepository, SummaryRepository
    from .processing import PaperSummarizer, create_llm_client

    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    session_factory = build_session_factory(engine)

    with get_session(session_factory) as session:
        paper = PaperRepository(session).get(paper_id)

    if paper is None:
        console.print(f"[red]Paper not found: {paper_id}")
        raise typer.Exit(1)

    console.print(f"[cyan]Summarising:[/cyan] {paper.title[:80]}")
    console.print(f"[dim]Provider: {settings.llm.provider} / {settings.llm.model_name}[/dim]")

    llm = create_llm_client(settings)
    summarizer = PaperSummarizer(llm, enable_thinking=think)
    summary = summarizer.summarize_sync(paper)

    with get_session(session_factory) as session:
        SummaryRepository(session).save(summary)

    # ── display ───────────────────────────────────────────────────────────
    from rich.panel import Panel

    console.print(Panel(summary.tldr, title="[bold]TLDR", border_style="cyan"))

    if summary.contributions:
        console.print("\n[bold yellow]Contributions")
        for item in summary.contributions:
            console.print(f"  • {item}")

    if summary.key_results:
        console.print("\n[bold green]Key Results")
        for item in summary.key_results:
            console.print(f"  • {item}")

    if summary.limitations:
        console.print("\n[bold red]Limitations")
        for item in summary.limitations:
            console.print(f"  • {item}")

    if summary.thinking_trace and verbose:
        console.print(
            Panel(
                summary.thinking_trace[:2000],
                title="[dim]Reasoning Trace (truncated)",
                border_style="dim",
            )
        )

    console.print(
        f"\n[dim]Generated in {summary.generation_time_seconds:.1f}s  |  "
        f"tokens: {summary.prompt_tokens}→{summary.completion_tokens}[/dim]"
    )


# ── process command ───────────────────────────────────────────────────────────


@app.command()
def process(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max papers to summarise per run"),
    ] = 20,
    think: Annotated[bool, typer.Option("--think")] = False,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing/--no-skip-existing", help="Skip papers that already have a summary"
        ),
    ] = True,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """[bold]Batch-summarise[/bold] all unsummarised papers in the database."""
    _setup_logging(verbose)
    import asyncio

    from .config.settings import get_settings
    from .database.engine import build_engine, build_session_factory, get_session
    from .database.repository import PaperRepository, SummaryRepository
    from .processing import PaperSummarizer, create_llm_client

    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    session_factory = build_session_factory(engine)

    with get_session(session_factory) as session:
        paper_ids = SummaryRepository(session).list_unsummarised_paper_ids(limit=limit)
        papers = [PaperRepository(session).get(pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]

    if not papers:
        console.print("[yellow]No unsummarised papers found.")
        return

    console.print(
        f"[cyan]Processing {len(papers)} papers with {settings.llm.provider}/{settings.llm.model_name}"
    )

    llm = create_llm_client(settings)
    summarizer = PaperSummarizer(llm, enable_thinking=think)

    async def _run() -> None:
        for i, paper in enumerate(papers, 1):
            console.print(f"  [{i}/{len(papers)}] {paper.title[:70]}")
            summary = await summarizer.summarize(paper)
            with get_session(session_factory) as session:
                SummaryRepository(session).save(summary)

    asyncio.run(_run())
    console.print(f"[bold green]Done — summarised {len(papers)} papers.")


# ── serve command ─────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p")] = 8080,
    reload: Annotated[bool, typer.Option("--reload")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """[bold]Start the API server[/bold] (requires [api] extras)."""
    _setup_logging(verbose)
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: uv pip install research-station[api]")
        raise typer.Exit(1)

    console.rule("[bold cyan]ResearchStation — API Server")
    console.print(f"Listening on [cyan]http://{host}:{port}[/cyan]  |  reload: {reload}")
    uvicorn.run(
        "research_station.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
