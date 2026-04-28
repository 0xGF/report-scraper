"""Top-level CLI.

Each sub-command is a thin wrapper that loads settings + configures logging
then delegates to the corresponding function in `report_scrape.application`.
Commands are intentionally independently runnable so you can re-run any one
stage in isolation during development.
"""

from __future__ import annotations

from typing import Annotated

import typer

from pipeline.config import get_settings
from pipeline.domain.companies import COMPANIES, get_all_companies
from pipeline.logging import bind_context, configure_logging

app = typer.Typer(
    help="Fiscal.ai take-home: scrape, parse, and consolidate European annual reports.",
    no_args_is_help=True,
    add_completion=False,
)

CompanyArg = Annotated[
    str,
    typer.Argument(help=f"Company slug — one of: {', '.join(sorted(COMPANIES))}"),
]


def _setup(company: str | None = None) -> None:
    s = get_settings()
    configure_logging(level=s.log_level, fmt=s.log_format)  # type: ignore[arg-type]
    s.ensure_dirs()
    if company is not None:
        bind_context(company=company)


@app.command()
def scrape(
    company: CompanyArg,
    discover_only: Annotated[
        bool,
        typer.Option("--discover-only", help="List PDFs without downloading"),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Cap how many PDFs to download (useful for smoke tests)"),
    ] = None,
) -> None:
    """Discover + download IR PDFs for a company."""
    _setup(company)
    from pipeline.application import scrape as _scrape

    _scrape.run(company, discover_only=discover_only, limit=limit)


@app.command()
def classify(company: CompanyArg) -> None:
    """Tag downloaded PDFs with (kind, fiscal_year)."""
    _setup(company)
    from pipeline.application import classify as _classify

    _classify.run(company)


@app.command()
def parse(company: CompanyArg) -> None:
    """Extract IS / BS / CF tables from annual reports."""
    _setup(company)
    from pipeline.application import parse as _parse

    _parse.run(company)


@app.command()
def confirm(company: CompanyArg) -> None:
    """LLM-validate extracted rows: drop noise, flag wrong-kind tables, attach hierarchy."""
    _setup(company)
    from pipeline.application import confirm as _confirm

    _confirm.run(company)


@app.command()
def normalize(company: CompanyArg) -> None:
    """Map raw line items to canonical names + infer hierarchy."""
    _setup(company)
    from pipeline.application import normalize as _normalize

    _normalize.run(company)


@app.command()
def build() -> None:
    """Consolidate all companies into the 'latest restated' view."""
    _setup()
    from pipeline.application import build as _build

    _build.run()


@app.command()
def export() -> None:
    """Emit JSON files under data/exports/ for the web viewer."""
    _setup()
    from pipeline.application import export as _export

    _export.run()


@app.command()
def market() -> None:
    """Fetch live price + sparkline + market cap into data/market.json."""
    _setup()
    from pipeline.application import market as _market

    _market.run()


@app.command()
def companies() -> None:
    """List configured companies."""
    _setup()
    for slug, c in sorted(get_all_companies().items()):
        typer.echo(f"  {slug:10s}  {c.name}  ({c.ticker} · {c.exchange})")


@app.command()
def discover(
    company_name: Annotated[str, typer.Argument(help="Free-text company name to research.")],
    write: Annotated[
        bool,
        typer.Option("--write/--dry-run", help="Append to companies.yaml when found."),
    ] = True,
) -> None:
    """Use the LLM to find a new company's IR config + metadata."""
    _setup()
    from pipeline.application import discover as _discover

    result = _discover.run(company_name, write=write)
    if not result.found:
        typer.echo(f"⚠ Low confidence: {result.notes or 'see logs'}")
        raise typer.Exit(code=1)
    typer.echo(f"✔ {result.slug}: {result.name} ({result.ticker} · {result.exchange})")
    typer.echo(f"  IR: {result.ir_url}")
    if write:
        typer.echo(f"  Run: just pipeline {result.slug}")


@app.command()
def add(
    company_name: Annotated[
        str, typer.Argument(help="Free-text company name (e.g. 'Adyen', 'Heineken').")
    ],
) -> None:
    """One-shot end-to-end onboarding: discover → scrape → ... → export → market."""
    _setup()
    from pipeline.application import add_company as _add_company

    _add_company.run(company_name)
    typer.echo(f"✔ Added '{company_name}' — refresh the viewer to see it.")


@app.command()
def research(company: CompanyArg) -> None:
    """Targeted gap-fill: find PDFs for years missing from `<slug>.extracted.json`.

    Runs after a normal `parse` pass. Reads which fiscal years are
    already covered, fires per-year searches + Wayback CDX for the rest,
    appends verified URLs to `companies.yaml`. Re-run scrape→classify→parse
    to actually pick the new docs up.
    """
    _setup()
    from pipeline.application import research as _research

    added = _research.fill_gaps(company)
    if added == 0:
        typer.echo("✔ No gaps to fill.")
    else:
        typer.echo(
            f"✔ Added {added} URL(s). Re-run: just pipeline {company} && just build && just export"
        )


if __name__ == "__main__":
    app()
