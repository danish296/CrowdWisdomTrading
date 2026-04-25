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


@app.command()
def backtest(
    asset: str = typer.Option("BTC", help="Asset symbol (BTC, ETH, ...)"),
    interval: str = typer.Option("5m", help="Bar size: 1m, 5m, 15m, 1h, 1d, ..."),
    horizon_minutes: int = typer.Option(5, help="Forecast horizon in minutes"),
    days: int = typer.Option(14, help="How many days of history to fetch"),
    warmup: int = typer.Option(200, help="Bars used as input window per prediction"),
    step: int = typer.Option(1, help="Bar spacing between successive anchors"),
    models: str = typer.Option(
        "statistical",
        help=(
            "Comma-separated list. Choices: statistical, kronos-mini, "
            "kronos-small, kronos-base. Example: statistical,kronos-small"
        ),
    ),
    source: str = typer.Option("auto", help="auto | bitstamp | binance"),
    label: str = typer.Option("", help="Free-text label saved with the run"),
) -> None:
    """Run a walk-forward backtest on REAL exchange data and print a side-by-side report."""
    from backtest import BacktestSpec, run_backtest, save_backtest
    from backtest.engine import result_to_payload
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    _banner()
    spec = BacktestSpec(
        asset=asset.upper(),
        interval=interval,
        horizon_minutes=horizon_minutes,
        days=days,
        warmup_bars=warmup,
        step_bars=step,
        models=[m.strip() for m in models.split(",") if m.strip()],
        bankroll_usd=settings.bankroll_usd,
        max_kelly_fraction=settings.max_kelly_fraction,
        min_edge=settings.min_edge,
        source=source,
        label=label,
    )

    with Progress(
        TextColumn("[bold #f5b400]backtest[/bold #f5b400]"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("{task.description}"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("starting...", total=100)

        def cb(pct: float, msg: str) -> None:
            progress.update(task, completed=int(pct * 100), description=msg)

        result = run_backtest(spec, progress=cb)

    payload = result_to_payload(result)
    save_backtest(payload)

    table = Table(
        title=f"Backtest {result.id} -- {asset.upper()} {interval} (horizon {horizon_minutes}m)",
        title_style="bold",
        header_style="bold #f5b400",
        border_style="#444444",
        expand=True,
    )
    table.add_column("Model")
    table.add_column("N", justify="right")
    table.add_column("Hit", justify="right")
    table.add_column("Conf-Hit (n)", justify="right")
    table.add_column("Brier", justify="right")
    table.add_column("LogLoss", justify="right")
    table.add_column("PnL $", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("MaxDD %", justify="right")
    for model_name, s in result.summary_per_model.items():
        table.add_row(
            model_name,
            str(s.get("n_predictions", 0)),
            f"{s.get('hit_rate', float('nan')):.3f}",
            f"{s.get('confident_hit_rate', float('nan')):.3f} ({s.get('confident_n', 0)})",
            f"{s.get('brier', float('nan')):.4f}",
            f"{s.get('log_loss', float('nan')):.4f}",
            f"{s.get('total_pnl_usd', 0):+.2f}",
            f"{s.get('sharpe_annualised', float('nan')):.2f}",
            f"{s.get('max_drawdown_pct', 0):+.2f}",
        )
    console.print(table)

    if result.pairwise:
        body = "\n".join(p["interpretation"] for p in result.pairwise)
        console.print(
            Panel(body, title="Diebold-Mariano (Brier loss)", border_style="#4fc3f7")
        )

    console.print(
        f"[dim]Saved to data/backtests/{result.id}.json -- view in the dashboard at /backtest[/dim]"
    )


if __name__ == "__main__":
    app()
