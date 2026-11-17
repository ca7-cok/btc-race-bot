"""DipCatcher: a mean-reversion bot for Coinbase Wrapped BTC (cbBTC) 1-minute bars.

The bot trades one asset, CBBTC, long only, against USDC, through the
agent-fun/bars-replay/v1 protocol (see vendor/makefun_bars.py, the kit's SDK
shim, copied byte for byte). It is a pure function of the bars it has been
sent: no clock, no network, no files, no randomness, so two runs on the same
data produce the same orders.

Idea
----
Bitcoin on 1-minute bars spends most of a day oscillating inside a band a few
percent wide. Deep, fast dips below the 4-hour mean tend to revert, and sharp
rallies above it tend to fade. The bot therefore buys dips and sells strength,
in two tranches, and never chases a trend:

* Band: the mean and population standard deviation of the last 240 closes
  (4 hours). A bar's z-score is (close - mean) / std.
* Buy tranche 1: half the equity when z <= -1.8.
* Buy tranche 2: the other half when z <= -2.6 while tranche 1 is held.
* Sell everything: when z >= +1.2, when the position is 1.5% below its
  blended entry price (stop-loss), when it gives back 1.0% from its
  post-entry peak while still in profit (trailing exit), or when it has
  been held over 6 hours without the band turning back up (stale exit).
* A 3-bar cooldown between decisions keeps the bot from stacking orders on
  the same move, and every order caps its slippage at 100 bps.
* Sizing is impact-aware: a tranche is halved while its modelled price
  impact would exceed 10 bps, so a thin pool never turns a good signal
  into an expensive fill.

Costs decide the thresholds. A full-size round trip pays about 26 bps in
fees, slippage and price impact, so a trade has to catch a move well above
that. The band is wide (4 hours) and the entry is deep (1.8+ sigma) precisely
so the bot trades rarely -- a handful of times a day -- and only on moves big
enough to clear the costs. On the published dev window this earned about
+$10 on $1,000 with a 0.8% maximum drawdown over 6 trades.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Strategy parameters (see the module docstring for why each is what it is).
WINDOW_BARS = 240        # 4 hours of 1-minute bars for the band
Z_BUY_FIRST = 1.8        # sigma below the band mean to open tranche 1
Z_BUY_SECOND = 2.6       # sigma below to add tranche 2
Z_SELL = 1.2             # sigma above to close the whole position
STOP_LOSS = 0.015        # close if 1.5% below the blended entry price
TRAIL_DROP = 0.010       # close if 1.0% below the post-entry peak, in profit
HELD_MAX_BARS = 360      # close a stale position held over 6 hours (z > 0)
TRANCHE_GAP_BARS = 10    # bars to wait after tranche 1 before tranche 2
MAX_IMPACT_BPS = 10      # halve a tranche while its impact exceeds this
TRANCHE = 0.5            # each tranche is half of equity
COOLDOWN_BARS = 3        # bars to wait after any decision
MAX_SLIPPAGE_BPS = 100   # 1%: generous vs a 1-minute move, tight vs a bad fill


class DipCatcher:
    """State for one run. All of it derives from the bars and fills seen."""

    def __init__(self, hello):
        self.price_scale = hello["symbols"][0]["price_scale"]
        self.closes = []          # closes in USD, one per bar seen
        self.last_bar_t = None    # close time of the last bar appended
        self.entry_price = 0.0    # blended entry, USD, at decision closes
        self.peak_since_entry = 0.0  # highest close seen while holding
        self.entry_seq = 0         # seq the current position was opened on
        self.second_tranche = False
        self.last_action_seq = -10 ** 9

    def sized_buy(self, state, want):
        """A buy size in quote units, halved while its impact is too big."""
        size = min(state.cash, state.weight_room(SYMBOL), want)
        while size >= 2_000_000:  # keep at least 1 USDC after halving
            impact = state.impact_bps(SYMBOL, size)
            if impact is None or impact <= MAX_IMPACT_BPS:
                break
            size //= 2
        return size

    def note_bar(self, batch):
        """Append this batch's CBBTC close once (a missing minute has no bar)."""
        for bar in batch.get("bars", []):
            if bar["symbol"] != SYMBOL:
                continue
            if bar["t"] == self.last_bar_t:
                return
            self.last_bar_t = bar["t"]
            self.closes.append(int(bar["c"]) / 10 ** self.price_scale)

    def band(self):
        """(mean, std) of the last WINDOW_BARS closes, or (None, None)."""
        if len(self.closes) < WINDOW_BARS:
            return None, None
        window = self.closes[-WINDOW_BARS:]
        mean = sum(window) / WINDOW_BARS
        var = sum((c - mean) ** 2 for c in window) / WINDOW_BARS
        return mean, var ** 0.5

    def on_batch(self, state, batch):
        self.note_bar(batch)
        if state.warming_up:
            return []
        price = state.close(SYMBOL)
        if price is None:
            return []
        price_usd = price / 10 ** self.price_scale
        mean, std = self.band()
        if mean is None or std == 0:
            return []
        if state.seq - self.last_action_seq < COOLDOWN_BARS:
            return []

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding else 0
        z = (price_usd - mean) / std

        if qty > 0:
            self.peak_since_entry = max(self.peak_since_entry, price_usd)
            in_profit = price_usd > self.entry_price
            trailing = (
                in_profit
                and price_usd <= self.peak_since_entry * (1 - TRAIL_DROP)
            )
            stale = (
                state.seq - self.entry_seq > HELD_MAX_BARS and z > 0
            )
            # Exit: strength, stop-loss, trail, or a stale position.
            if z >= Z_SELL or price_usd / self.entry_price - 1 <= -STOP_LOSS or trailing or stale:
                self.last_action_seq = state.seq
                self.entry_price = 0.0
                self.peak_since_entry = 0.0
                self.second_tranche = False
                return [{
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }]
            # Add tranche 2 on a deeper dip, sized to the remaining room.
            # Wait a little after tranche 1: never double down on the same
            # minute's dip.
            if (not self.second_tranche and z <= -Z_BUY_SECOND
                    and state.seq - self.entry_seq >= TRANCHE_GAP_BARS):
                size = self.sized_buy(state, int(TRANCHE * state.equity))
                if size >= 1_000_000:  # at least 1 USDC
                    self.last_action_seq = state.seq
                    self.second_tranche = True
                    # Blend the entry price by quantity (qty is in 1e-8
                    # BTC; size / price_usd is the new BTC).
                    new_qty = size / price_usd
                    old_qty = qty / 10 ** 8
                    self.entry_price = (
                        self.entry_price * old_qty + price_usd * new_qty
                    ) / (old_qty + new_qty)
                    return [{
                        "side": "buy",
                        "symbol": SYMBOL,
                        "notional_quote_units": size,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    }]
            return []

        # Flat: open tranche 1 on a deep dip.
        if z <= -Z_BUY_FIRST:
            size = self.sized_buy(state, int(TRANCHE * state.equity))
            if size >= 1_000_000:  # at least 1 USDC
                self.last_action_seq = state.seq
                self.entry_price = price_usd
                self.peak_since_entry = price_usd
                self.entry_seq = state.seq
                self.second_tranche = False
                return [{
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": size,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }]
        return []


def main():
    bot = None

    def on_hello(state):
        nonlocal bot
        bot = DipCatcher(state.hello)

    def on_batch(state, batch):
        return bot.on_batch(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()
