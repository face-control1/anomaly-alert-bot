"""Compact signal charts rendered for Telegram without writing to disk."""
from __future__ import annotations

from io import BytesIO


def render_signal_chart(ticker: str, candles: list[dict], signal_price: float) -> bytes | None:
    if len(candles) < 5:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    rows = []
    for item in candles:
        try:
            opened = float(item["open"])
            closed = float(item["close"])
            high = float(item["high"])
            low = float(item["low"])
            value = float(item.get("value") or 0) / 1_000_000
            label = str(item.get("begin") or "")[11:16]
            if min(opened, closed, high, low) <= 0:
                continue
            rows.append((opened, closed, high, low, value, label))
        except (KeyError, TypeError, ValueError):
            continue
    if len(rows) < 5:
        return None
    x = list(range(len(rows)))
    fig, (price_ax, volume_ax) = plt.subplots(
        2, 1, figsize=(8, 4.8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]}, facecolor="#08111f",
    )
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#0d1b2a")
        axis.tick_params(colors="#91a4ba", labelsize=8)
        for spine in axis.spines.values():
            spine.set_color("#20364d")
    body_width = 0.62
    for index, (opened, closed, high, low, _value, _label) in enumerate(rows):
        color = "#38d996" if closed >= opened else "#ff637d"
        price_ax.vlines(index, low, high, color=color, linewidth=1.0, alpha=.95)
        bottom = min(opened, closed)
        height = max(abs(closed - opened), max(high - low, 0.0001) * .025)
        price_ax.add_patch(Rectangle(
            (index - body_width / 2, bottom), body_width, height,
            facecolor=color, edgecolor=color, linewidth=.8,
        ))
    price_ax.axhline(signal_price, color="#ffcc66", linestyle="--", linewidth=1, alpha=.8)
    price_ax.scatter([x[-1]], [signal_price], color="#ffcc66", s=38, zorder=5)
    price_ax.annotate(
        f" {signal_price:,.2f} ₽", (x[-1], signal_price),
        color="#ffcc66", fontsize=8, va="bottom", ha="right",
    )
    price_ax.set_xlim(-1, len(rows))
    price_ax.set_title(f"{ticker}  ·  1 мин  ·  цена сигнала", color="white", loc="left", fontsize=13, weight="bold")
    price_ax.set_ylabel("цена, ₽", color="#91a4ba")
    volumes = [row[4] for row in rows]
    volume_colors = ["#38d996" if row[1] >= row[0] else "#ff637d" for row in rows]
    volume_ax.bar(x, volumes, color=volume_colors, alpha=.58, width=.72)
    volume_ax.set_ylabel("млн ₽", color="#91a4ba", fontsize=8)
    tick_step = max(1, len(rows) // 6)
    tick_indexes = list(range(0, len(rows), tick_step))
    if tick_indexes[-1] != len(rows) - 1:
        tick_indexes.append(len(rows) - 1)
    volume_ax.set_xticks(tick_indexes)
    volume_ax.set_xticklabels([rows[index][5] for index in tick_indexes], color="#91a4ba")
    volume_ax.set_xlabel("время МСК", color="#91a4ba", fontsize=8)
    fig.tight_layout(pad=1.1)
    output = BytesIO()
    fig.savefig(output, format="png", dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()
