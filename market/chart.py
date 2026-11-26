"""TradingView-inspired candlestick charts rendered in memory for Telegram."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from statistics import fmean

BG, GRID, TEXT = "#131722", "#2a2e39", "#b2b5be"
UP, DOWN, ACCENT = "#089981", "#f23645", "#2962ff"


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _time_label(raw: object) -> str:
    try:
        return datetime.fromisoformat(str(raw)).strftime("%H:%M")
    except (TypeError, ValueError):
        text = str(raw or "")
        return text[11:16] if len(text) >= 16 else text[-5:]


def _price_text(value: float) -> str:
    if value >= 1000:
        return f"{value:,.1f}".replace(",", " ")
    return f"{value:.2f}" if value >= 10 else f"{value:.4f}"


def render_signal_chart(ticker: str, candles: list[dict], signal_price: float) -> bytes | None:
    """Return a crisp 16:9 PNG with candles, EMA20, volume and price scale."""
    rows: list[dict] = []
    for item in candles[-100:]:
        opened, closed = _number(item.get("open")), _number(item.get("close"))
        high, low = _number(item.get("high")), _number(item.get("low"))
        if min(opened, closed, high, low) <= 0 or high < low:
            continue
        rows.append({"open": opened, "close": closed, "high": high, "low": low,
                     "value": _number(item.get("value")), "time": _time_label(item.get("begin"))})
    if len(rows) < 5:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    x = list(range(len(rows)))
    closes = [row["close"] for row in rows]
    first_close, last_close = closes[0], closes[-1]
    change_pct = (last_close / first_close - 1) * 100 if first_close else 0
    direction_color = UP if change_pct >= 0 else DOWN
    alpha, ema = 2 / 21, []
    for close in closes:
        ema.append(close if not ema else alpha * close + (1 - alpha) * ema[-1])

    fig = plt.figure(figsize=(12.8, 7.2), dpi=120, facecolor=BG)
    grid = fig.add_gridspec(5, 1, hspace=.02, left=.055, right=.93, top=.89, bottom=.10)
    price_ax = fig.add_subplot(grid[:4, 0])
    volume_ax = fig.add_subplot(grid[4, 0], sharex=price_ax)
    for axis in (price_ax, volume_ax):
        axis.set_facecolor(BG)
        axis.grid(True, color=GRID, linewidth=.65, alpha=.72)
        axis.tick_params(colors=TEXT, labelsize=9, length=0, pad=8)
        axis.yaxis.tick_right()
        for spine in axis.spines.values():
            spine.set_visible(False)

    volume_values, candle_colors = [], []
    for index, row in enumerate(rows):
        color = UP if row["close"] >= row["open"] else DOWN
        candle_colors.append(color)
        price_ax.vlines(index, row["low"], row["high"], color=color, linewidth=1.15, zorder=2)
        bottom, height = min(row["open"], row["close"]), abs(row["close"] - row["open"])
        height = height or max((row["high"] - row["low"]) * .018, last_close * .00005)
        price_ax.add_patch(Rectangle((index - .33, bottom), .66, height,
                                     facecolor=color, edgecolor=color, linewidth=.75, zorder=3))
        volume_values.append(row["value"] / 1_000_000)

    price_ax.plot(x, ema, color="#f7a600", linewidth=1.15, alpha=.95, zorder=4)
    price_ax.axhline(signal_price, color=ACCENT, linestyle=(0, (4, 3)), linewidth=1, alpha=.9)
    price_ax.set_xlim(-1.3, len(rows) + 1.7)
    price_ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
    price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _price_text(value)))
    price_ax.tick_params(axis="x", labelbottom=False)
    price_ax.annotate(_price_text(signal_price), xy=(len(rows) - .05, signal_price), xycoords="data",
                      xytext=(10, 0), textcoords="offset points", va="center", ha="left",
                      color="white", fontsize=9, fontweight="bold",
                      bbox={"boxstyle": "round,pad=.32,rounding_size=.1", "fc": ACCENT, "ec": ACCENT},
                      clip_on=False, zorder=8)

    volume_ax.bar(x, volume_values, width=.72, color=candle_colors, alpha=.72)
    volume_ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
    volume_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:g}M" if value else ""))
    tick_step = max(1, len(rows) // 7)
    ticks = list(range(0, len(rows), tick_step))
    if ticks[-1] != len(rows) - 1:
        ticks.append(len(rows) - 1)
    volume_ax.set_xticks(ticks)
    volume_ax.set_xticklabels([rows[i]["time"] for i in ticks], color=TEXT)

    average_volume = fmean(volume_values) if volume_values else 0
    fig.text(.057, .946, ticker, color="white", fontsize=18, weight="bold", ha="left", va="center")
    fig.text(.132, .946, "MOEX · 1 мин", color=TEXT, fontsize=10, ha="left", va="center")
    fig.text(.057, .912, f"O {_price_text(rows[-1]['open'])}   H {_price_text(rows[-1]['high'])}   L {_price_text(rows[-1]['low'])}   C {_price_text(last_close)}", color=TEXT, fontsize=9, ha="left")
    fig.text(.50, .946, f"{change_pct:+.2f}%", color=direction_color, fontsize=13, weight="bold", ha="center", va="center")
    fig.text(.057, .073, f"Объём, млн ₽   {volume_values[-1]:.2f}   ·   средний {average_volume:.2f}", color=TEXT, fontsize=8.5, ha="left")
    fig.text(.925, .025, "MOEX HELP", color="#6f7480", fontsize=8, weight="bold", ha="right")

    output = BytesIO()
    fig.savefig(output, format="png", facecolor=BG, edgecolor="none")
    plt.close(fig)
    return output.getvalue()
