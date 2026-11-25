"""Compact signal charts rendered for Telegram without writing to disk."""
from __future__ import annotations

from io import BytesIO


def render_signal_chart(ticker: str, candles: list[dict], signal_price: float) -> bytes | None:
    if len(candles) < 5:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    closes = [float(item.get("close") or 0) for item in candles if item.get("close")]
    volumes = [float(item.get("value") or 0) / 1_000_000 for item in candles if item.get("close")]
    if len(closes) < 5:
        return None
    x = list(range(len(closes)))
    fig, (price_ax, volume_ax) = plt.subplots(
        2, 1, figsize=(8, 4.8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]}, facecolor="#08111f",
    )
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#0d1b2a")
        axis.tick_params(colors="#91a4ba", labelsize=8)
        for spine in axis.spines.values():
            spine.set_color("#20364d")
    color = "#38d996" if closes[-1] >= closes[0] else "#ff637d"
    price_ax.plot(x, closes, color=color, linewidth=2.2)
    price_ax.fill_between(x, closes, min(closes), color=color, alpha=0.10)
    price_ax.axhline(signal_price, color="#ffcc66", linestyle="--", linewidth=1, alpha=.8)
    price_ax.scatter([x[-1]], [closes[-1]], color="#ffcc66", s=35, zorder=5)
    price_ax.set_title(f"{ticker}  ·  сигнал {signal_price:,.2f} ₽", color="white", loc="left", fontsize=13, weight="bold")
    price_ax.set_ylabel("цена, ₽", color="#91a4ba")
    volume_ax.bar(x, volumes, color="#3f8cff", alpha=.65, width=.75)
    volume_ax.set_ylabel("млн ₽", color="#91a4ba", fontsize=8)
    fig.tight_layout(pad=1.1)
    output = BytesIO()
    fig.savefig(output, format="png", dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()
