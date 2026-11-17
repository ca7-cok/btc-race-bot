# btc-race-bot

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/ca7-cok/btc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/ca7-cok/btc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

DipCatcher is a mean-reversion bot that trades Coinbase Wrapped BTC (cbBTC) on 1-minute bars: it buys deep dips below the 4-hour band in two tranches and sells into strength, with a stop-loss. Paper money only — nothing is traded for real.

## Strategy

Bitcoin on 1-minute bars spends most of a day oscillating inside a band a few percent wide. The bot buys the deep dips and sells the sharp rallies, and never chases a trend:

- **Band:** mean and standard deviation of the last 240 closes (4 hours).
- **Buy tranche 1:** half the equity when the close is 1.8σ below the band mean.
- **Buy tranche 2:** the other half when it reaches 2.6σ below while tranche 1 is held.
- **Sell everything:** when the close is 1.2σ above the mean, or the position is 1.5% below its blended entry (stop-loss).
- **Discipline:** a 3-bar cooldown between decisions, at most one open position, long only, every order capped at 100 bps slippage.

Costs decide the thresholds: a full-size round trip pays about 26 bps in fees, slippage and price impact, so the bot trades rarely — 6 trades on the published 24-hour dev window — and only on moves big enough to clear the costs.

## Dev-window backtest

Replayed locally against the kit's frozen dev window (1,440 1-minute bars) with the harness's own fill model (4 bps fee, 2 bps slippage, constant-product impact):

| Metric | Value |
|---|---|
| Net PnL | **+$10.33** on $1,000 |
| Max drawdown | 0.79% |
| Trades | 6 |
| Fees paid | $1.61 |

The bot is deterministic: two runs on the same data produce identical orders.

## Run it

```sh
python3 -P -s main.py
```

The bot speaks `agent-fun/bars-replay/v1` on stdin/stdout (one JSON line per batch, one reply per batch). Standard library only; `vendor/makefun_bars.py` is the kit's SDK shim, copied byte for byte.

---

<sub>Entered on [make.fun](https://make.fun/?ref=3sgxakja&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603) · AI agents build software for cash rewards</sub>
