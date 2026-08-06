import time
import requests

SYMBOL = "BTCUSDT"
EMA_FAST, EMA_SLOW, EMA_TREND = 10, 30, 200
BB_LENGTH, BB_STD = 20, 2
CROSS_BREAKOUT_WINDOW_CANDLES = 8
STOP_LOSS_PCT = 0.004
REWARD_RISK = 4
POSITION_SIZE_PCT = 0.40
LOOKBACK_DAYS = 180
BASE_URLS = ["https://data-api.binance.vision", "https://api.binance.com"]

START_BALANCE = 5000.0
PROFIT_TARGET_PCT = 0.12
MAX_OVERALL_DD_PCT = 0.06
MAX_DAILY_DD_PCT = 0.03


def fetch_all_klines(interval, days):
    ms_per_day = 86400000
    end = int(time.time() * 1000)
    start = end - days * ms_per_day
    out = []
    cur = start
    while cur < end:
        got = None
        for base in BASE_URLS:
            try:
                r = requests.get(
                    f"{base}/api/v3/klines",
                    params={"symbol": SYMBOL, "interval": interval, "startTime": cur, "limit": 1000},
                    timeout=15,
                )
                r.raise_for_status()
                got = r.json()
                break
            except Exception:
                continue
        if not got:
            break
        for row in got:
            out.append({"open_time": row[0], "close": float(row[4]), "high": float(row[2]), "low": float(row[3]), "close_time": row[6]})
        if len(got) < 1000:
            break
        cur = got[-1][6] + 1
    return out


def ema(values, length):
    k = 2 / (length + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def bollinger(values, length, num_std):
    upper, lower = [None] * len(values), [None] * len(values)
    for i in range(length - 1, len(values)):
        w = values[i - length + 1: i + 1]
        m = sum(w) / length
        var = sum((x - m) ** 2 for x in w) / length
        s = var ** 0.5
        upper[i] = m + num_std * s
        lower[i] = m - num_std * s
    return upper, lower


print("Fetching ~6 months of historical data...")
c5 = fetch_all_klines("5m", LOOKBACK_DAYS)
c15 = fetch_all_klines("15m", LOOKBACK_DAYS)
print(f"5m candles: {len(c5)}, 15m candles: {len(c15)}")

closes5 = [c["close"] for c in c5]
highs5 = [c["high"] for c in c5]
lows5 = [c["low"] for c in c5]
times5 = [c["close_time"] for c in c5]
e10_5 = ema(closes5, EMA_FAST)
e30_5 = ema(closes5, EMA_SLOW)
ub, lb = bollinger(closes5, BB_LENGTH, BB_STD)

closes15 = [c["close"] for c in c15]
e10_15 = ema(closes15, EMA_FAST)
e30_15 = ema(closes15, EMA_SLOW)
e200_15 = ema(closes15, EMA_TREND)
c15_times = [c["close_time"] for c in c15]


def find_15m_idx(t):
    lo, hi, ans = 0, len(c15_times) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if c15_times[mid] <= t:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


map15 = [find_15m_idx(t) for t in times5]

# ---------- PASS 1: identify every signal + its resolution (price-only, no $) ----------
trades = []
i = 250
open_trade = None

while i < len(closes5):
    if open_trade is not None:
        direction, entry, sl, tp, entry_i = open_trade
        hit, res_i = None, None
        j = entry_i + 1
        while j < len(closes5):
            if direction == "LONG":
                if lows5[j] <= sl:
                    hit, res_i = "LOSS", j
                    break
                if highs5[j] >= tp:
                    hit, res_i = "WIN", j
                    break
            else:
                if highs5[j] >= sl:
                    hit, res_i = "LOSS", j
                    break
                if lows5[j] <= tp:
                    hit, res_i = "WIN", j
                    break
            j += 1
        if hit is None:
            trades.append({"direction": direction, "entry": entry, "sl": sl, "tp": tp, "entry_i": entry_i, "resolve_i": None, "outcome": "OPEN"})
            break
        trades.append({"direction": direction, "entry": entry, "sl": sl, "tp": tp, "entry_i": entry_i, "resolve_i": res_i, "outcome": hit})
        open_trade = None
        i = res_i + 1
        continue

    found = None
    lookback_start = max(1, i - 2 * CROSS_BREAKOUT_WINDOW_CANDLES)
    for cross_idx in range(i, lookback_start - 1, -1):
        if e10_5[cross_idx - 1] is None:
            continue
        prev_diff = e10_5[cross_idx - 1] - e30_5[cross_idx - 1]
        cur_diff = e10_5[cross_idx] - e30_5[cross_idx]
        is_golden = prev_diff <= 0 and cur_diff > 0
        is_death = prev_diff >= 0 and cur_diff < 0
        window_end = min(i, cross_idx + CROSS_BREAKOUT_WINDOW_CANDLES)
        if is_golden:
            for j2 in range(cross_idx, window_end + 1):
                if ub[j2] is not None and closes5[j2] > ub[j2]:
                    found = ("LONG", cross_idx)
                    break
        if found:
            break
        if is_death:
            for j2 in range(cross_idx, window_end + 1):
                if lb[j2] is not None and closes5[j2] < lb[j2]:
                    found = ("SHORT", cross_idx)
                    break
        if found:
            break

    if found:
        direction, cross_idx = found
        idx15 = map15[i]
        if idx15 >= 0 and e10_15[idx15] is not None and e200_15[idx15] is not None:
            price = closes5[i]
            if direction == "LONG":
                confirms15 = e10_15[idx15] > e30_15[idx15]
                confirms200 = price > e200_15[idx15]
            else:
                confirms15 = e10_15[idx15] < e30_15[idx15]
                confirms200 = price < e200_15[idx15]
            if confirms15 and confirms200:
                entry = price
                if direction == "LONG":
                    sl = entry * (1 - STOP_LOSS_PCT)
                    tp = entry * (1 + STOP_LOSS_PCT * REWARD_RISK)
                else:
                    sl = entry * (1 + STOP_LOSS_PCT)
                    tp = entry * (1 - STOP_LOSS_PCT * REWARD_RISK)
                open_trade = (direction, entry, sl, tp, i)
    i += 1

wins = sum(1 for t in trades if t["outcome"] == "WIN")
losses = sum(1 for t in trades if t["outcome"] == "LOSS")
print(f"Total signals found: {len(trades)}  Wins: {wins}  Losses: {losses}")

# ---------- PASS 2: walk candle-by-candle, simulate $ equity with mark-to-market ----------
balance = START_BALANCE
peak = balance
max_overall_dd = 0.0
max_daily_dd = 0.0
day_key = None
day_start_balance = balance
breach = None
target_hit = None

trade_ptr = 0
current_trade = None

for i in range(250, len(closes5)):
    t = times5[i]
    day = t // 86400000
    if day != day_key:
        day_key = day
        day_start_balance = balance

    if current_trade is None and trade_ptr < len(trades) and trades[trade_ptr]["entry_i"] == i:
        tr = trades[trade_ptr]
        risk_amt = balance * POSITION_SIZE_PCT * STOP_LOSS_PCT
        reward_amt = risk_amt * REWARD_RISK
        current_trade = dict(tr)
        current_trade["risk_amt"] = risk_amt
        current_trade["reward_amt"] = reward_amt

    unrealized = 0.0
    if current_trade is not None and i > current_trade["entry_i"]:
        if current_trade["direction"] == "LONG":
            worst_price = lows5[i]
            r_mult = ((worst_price - current_trade["entry"]) / current_trade["entry"]) / STOP_LOSS_PCT
        else:
            worst_price = highs5[i]
            r_mult = ((current_trade["entry"] - worst_price) / current_trade["entry"]) / STOP_LOSS_PCT
        r_mult = max(-1.1, min(r_mult, REWARD_RISK * 1.1))
        unrealized = r_mult * current_trade["risk_amt"] if r_mult < 0 else min(r_mult, REWARD_RISK) * current_trade["risk_amt"]

    equity_now = balance + unrealized
    if equity_now > peak:
        peak = equity_now
    overall_dd = (peak - equity_now) / peak if peak > 0 else 0
    daily_dd = (day_start_balance - equity_now) / day_start_balance if day_start_balance > 0 else 0
    max_overall_dd = max(max_overall_dd, overall_dd)
    max_daily_dd = max(max_daily_dd, daily_dd)

    if breach is None and (overall_dd >= MAX_OVERALL_DD_PCT or daily_dd >= MAX_DAILY_DD_PCT):
        breach = {
            "type": "OVERALL" if overall_dd >= MAX_OVERALL_DD_PCT else "DAILY",
            "time": t, "equity": equity_now, "overall_dd": overall_dd, "daily_dd": daily_dd,
        }

    if target_hit is None and equity_now >= START_BALANCE * (1 + PROFIT_TARGET_PCT):
        target_hit = {"time": t, "equity": equity_now}

    if breach is not None or target_hit is not None:
        break

    if current_trade is not None and current_trade["resolve_i"] == i:
        if current_trade["outcome"] == "WIN":
            balance += current_trade["reward_amt"]
        elif current_trade["outcome"] == "LOSS":
            balance -= current_trade["risk_amt"]
        current_trade = None
        trade_ptr += 1

data_start_days = (times5[0]) / 86400000
end_i = i
elapsed_days = (times5[min(end_i, len(times5) - 1)] - times5[250]) / 86400000

print("=== CHALLENGE SIMULATION ($5,000 account, 12% target, 6% max DD, 3% daily DD) ===")
print(f"Data window: last {LOOKBACK_DAYS} days of real BTCUSDT 5m/15m data")
print(f"Position size 40% of balance, SL 0.4%, RR 1:4 (risk ~0.16% of equity per trade, compounding)")
print(f"Elapsed simulated days before stopping: {elapsed_days:.1f}")

if target_hit is not None:
    print("OUTCOME: PASSED -- profit target reached before any drawdown breach")
    print(f"Final equity: {target_hit['equity']:.2f} (+{(target_hit['equity']/START_BALANCE-1)*100:.2f}%)")
elif breach is not None:
    print(f"OUTCOME: FAILED -- breached {breach['type']} drawdown limit before reaching target")
    print(f"Equity at breach: {breach['equity']:.2f}  Overall DD: {breach['overall_dd']*100:.2f}%  Daily DD: {breach['daily_dd']*100:.2f}%")
else:
    print("OUTCOME: NEITHER -- ran out of data without hitting target or breaching limits")
    print(f"Final balance: {balance:.2f} ({(balance/START_BALANCE-1)*100:+.2f}%)")

print(f"Max overall drawdown observed: {max_overall_dd*100:.2f}%")
print(f"Max single-day drawdown observed: {max_daily_dd*100:.2f}%")
print(f"Trades taken before stop: {trade_ptr}")
