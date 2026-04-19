"""
CLI entry point.

  python main.py once             # run one cycle and exit
  python main.py loop             # run forever, every LOOP_SECONDS
  python main.py dashboard        # serve the FastAPI dashboard
  python main.py status           # print last cycle summary
  python main.py demo             # run 3 cycles back-to-back (5s apart)
"""
from __future__ import annotations

import time

import typer
from rich.panel import Panel
from rich.table import Table

from agents.orchestrator import Orchestrator
from config import settings
from core.logger import console
from core.memory import (
    hit_rate,
    load_recent_cycles,
    load_recent_feedback,
    total_pnl,
)

app = typer.Typer(add_completion=False, help="CryptoAdsAgents CLI")


def _banner() -> None:
    body = (
        f"[bold]Model[/bold]   {settings.openrouter_model}\n"
        f"[bold]Assets[/bold]  {', '.join(settings.assets)}\n"
        f"[bold]Bankroll[/bold] ${settings.bankroll_usd:,.2f}   "
        f"[bold]MaxKelly[/bold] {settings.max_kelly_fraction:.0%}   "
        f"[bold]MinEdge[/bold] {settings.min_edge:.1%}\n"
        f"[bold]LLM[/bold]     {'ON' if settings.has_llm else 'OFF (demo narration)'}    "
        f"[bold]Apify[/bold]   {'ON' if settings.has_apify else 'OFF (Binance fallback)'}"
    )
    console.print(
        Panel.fit(
            body,
            title="[bold #f5b400]CRYPTO ADS · AGENTS[/bold #f5b400]",
            border_style="#f5b400",
            padding=(1, 2),
        )
    )


def _summary_table(cycle) -> Table:
    table = Table(
        title="Decisions",
        title_style="bold",
        header_style="bold #f5b400",
        border_style="#444444",
        expand=True,
    )
    table.add_column("Venue")
    table.add_column("Asset")
    table.add_column("Side")
    table.add_column("Edge", justify="right")
    table.add_column("Stake $", justify="right")
    table.add_column("EV $", justify="right")
    table.add_column("Notes", overflow="fold")
    for d in cycle.decisions:
        side_color = (
            "ok" if d.side == "YES" else "err" if d.side == "NO" else "warn"
        )
        table.add_row(
            d.market.venue,
            d.market.asset,
            f"[{side_color}]{d.side}[/{side_color}]",
            f"{d.edge:+.3f}",
            f"{d.stake_usd:.2f}",
            f"{d.expected_value_usd:+.2f}",
            d.notes,
        )
    return table


@app.command()
def once() -> None:
    """Run a single agent cycle."""
    _banner()
    cycle = Orchestrator().run_once()
    console.print(_summary_table(cycle))
    if cycle.feedback:
        console.print(
            Panel(cycle.feedback, title="Hermes feedback", border_style="#66bb6a")
        )


@app.command()
def loop() -> None:
    """Run continuously until Ctrl-C."""
    _banner()
    Orchestrator().loop_forever()


@app.command()
def demo(n: int = 3, sleep: int = 5) -> None:
    """Run N cycles back-to-back (great for first-time smoke testing)."""
    _banner()
    orch = Orchestrator()
    for i in range(n):
        cycle = orch.run_once()
        console.print(_summary_table(cycle))
        if cycle.feedback:
            console.print(
                Panel(cycle.feedback, title="Hermes feedback", border_style="#66bb6a")
            )
        if i < n - 1:
            console.print(f"[dim]Sleeping {sleep}s before next cycle…[/dim]")
            time.sleep(sleep)


@app.command()
def status() -> None:
    """Show recent stats."""
    _banner()
    fb = load_recent_feedback(200)
    cy = load_recent_cycles(10)
    console.print(
        Panel(
            f"[bold]Recent feedback[/bold] n={len(fb)}\n"
            f"  hit rate : [ok]{hit_rate(fb):.1%}[/ok]\n"
            f"  total PnL: [ok]${total_pnl(fb):+,.2f}[/ok]\n"
            f"[bold]Cycles stored[/bold] {len(cy)}",
            title="Status",
            border_style="#4fc3f7",
        )
    )


@app.command()
def dashboard() -> None:
    """Start the FastAPI dashboard."""
    import uvicorn

    _banner()
    console.print(
        f"[ok]Dashboard:[/ok] http://{settings.dashboard_host}:{settings.dashboard_port}"
    )
    uvicorn.run(
        "api:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level="info",
    )


if __name__ == "__main__":
    app()
