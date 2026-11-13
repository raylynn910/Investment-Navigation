# -*- coding: utf-8 -*-
"""
tools/tw_price_limit.py
=======================
台股「今日收盤漲跌幅」與「漲跌停亮燈」唯一判定來源 (Single Source of Truth)

設計原則（2026-11-14 重構，修正舊版百分比門檻誤判）：
1. 漲跌停判定一律使用「官方漲跌停價」：
   漲停價 = 參考價 × 1.10 後「無條件捨去」至該價位升降單位
   跌停價 = 參考價 × 0.90 後「無條件進位」至該價位升降單位
   收盤價 == 漲停價 → 漲停亮紅燈；收盤價 == 跌停價 → 跌停亮綠燈。
   嚴禁再用 9.5% / 9.0% 等百分比門檻（低價股、跨升降單位時會誤判）。
2. 前一交易日必須是「全市場的前一個交易日」：
   若某檔股票在全市場前一交易日沒有資料（例如上櫃整日同步失敗），
   嚴禁拿更早的資料硬算（那是 2 日漲跌幅），一律標記 data_ok=False、不亮燈。
3. 漲跌幅超過 ±10.5%（正常交易日不可能發生，代表除權息/減資/新上市參考價異動）
   一律標記 data_ok=False，不得以開盤價等方式「猜」參考價。
"""
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

LIMIT_RATIO = Decimal("0.10")
MAX_VALID_ABS_PCT = 10.5  # 超過即視為參考價異動，不判定


def _is_etf(stock_id: str) -> bool:
    return str(stock_id).startswith("00")


def tick_size(price: Decimal, stock_id: str = "") -> Decimal:
    """台股升降單位 (股票 / ETF)"""
    p = Decimal(price)
    if _is_etf(stock_id):
        return Decimal("0.01") if p < 50 else Decimal("0.05")
    if p < 10:
        return Decimal("0.01")
    if p < 50:
        return Decimal("0.05")
    if p < 100:
        return Decimal("0.1")
    if p < 500:
        return Decimal("0.5")
    if p < 1000:
        return Decimal("1")
    return Decimal("5")


def _to_dec(x: float) -> Decimal:
    return Decimal(str(round(float(x), 4)))


def calc_limit_prices(ref_price: float, stock_id: str = ""):
    """回傳 (漲停價, 跌停價)，依官方升降單位規則計算"""
    ref = _to_dec(ref_price)
    if ref <= 0:
        return None, None
    raw_up = ref * (Decimal(1) + LIMIT_RATIO)
    t_up = tick_size(raw_up, stock_id)
    limit_up = (raw_up / t_up).to_integral_value(rounding=ROUND_FLOOR) * t_up

    raw_dn = ref * (Decimal(1) - LIMIT_RATIO)
    t_dn = tick_size(raw_dn, stock_id)
    limit_dn = (raw_dn / t_dn).to_integral_value(rounding=ROUND_CEILING) * t_dn
    return float(limit_up), float(limit_dn)


def classify_daily_change(close: float, prev_close: float, stock_id: str = "") -> dict:
    """依收盤價與前一交易日收盤價（參考價）判定漲跌幅與漲跌停"""
    res = {
        "daily_change_pct": None,
        "daily_change_val": None,
        "is_limit_up": False,
        "is_limit_down": False,
        "limit_up_price": None,
        "limit_down_price": None,
        "prev_close": prev_close,
        "change_data_ok": False,
        "change_note": "",
    }
    try:
        c = float(close)
        p = float(prev_close)
    except (TypeError, ValueError):
        res["change_note"] = "無報價"
        return res
    if c <= 0 or p <= 0:
        res["change_note"] = "無報價"
        return res

    pct = (c / p - 1.0) * 100.0
    if abs(pct) > MAX_VALID_ABS_PCT:
        res["change_note"] = "參考價異動(除權息/減資/新上市)"
        return res

    lu, ld = calc_limit_prices(p, stock_id)
    t_c = float(tick_size(_to_dec(c), stock_id))
    eps = t_c / 2.0
    res.update({
        "daily_change_pct": round(pct, 2),
        "daily_change_val": round(c - p, 2),
        "limit_up_price": lu,
        "limit_down_price": ld,
        "is_limit_up": bool(lu is not None and c >= lu - eps),
        "is_limit_down": bool(ld is not None and c <= ld + eps),
        "change_data_ok": True,
    })
    return res


def get_market_trading_dates(conn, lookback: int = 15, coverage_ratio: float = 0.5) -> list:
    """
    取得「全市場有效交易日」(新到舊)。
    只計數字開頭的證券代號 (排除 TAIEX / TXF 等指數列)，
    當日檔數 >= 近期最大檔數 × coverage_ratio 才算有效交易日（排除僅有指數的假日列）。
    """
    rows = conn.execute(f"""
        SELECT date, COUNT(*) AS n FROM klines
        WHERE close > 0 AND regexp_matches(stock_id, '^[0-9]')
        GROUP BY date ORDER BY date DESC LIMIT {int(lookback)}
    """).fetchall()
    if not rows:
        return []
    max_n = max(r[1] for r in rows)
    return [str(r[0])[:10] for r in rows if r[1] >= max_n * coverage_ratio]


def load_daily_change_map(conn) -> tuple:
    """
    回傳 (change_map, meta)
    change_map[stock_id] = classify_daily_change(...) 結果 + close/date
    meta = {date_t, date_t1, ok_count, missing_prev_count, stale_count, anomaly_count}
    """
    dates = get_market_trading_dates(conn)
    meta = {"date_t": None, "date_t1": None, "ok_count": 0,
            "missing_prev_count": 0, "stale_count": 0, "anomaly_count": 0}
    if len(dates) < 2:
        return {}, meta
    date_t, date_t1 = dates[0], dates[1]
    meta["date_t"], meta["date_t1"] = date_t, date_t1

    df = conn.execute("""
        WITH latest AS (
            SELECT stock_id, date, close,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
            FROM klines WHERE close > 0 AND regexp_matches(stock_id, '^[0-9]')
        )
        SELECT l.stock_id, l.date AS last_date, l.close AS close_t, p.close AS close_t1
        FROM latest l
        LEFT JOIN klines p ON p.stock_id = l.stock_id AND p.date = ? AND p.close > 0
        WHERE l.rn = 1
    """, [date_t1]).fetchall()

    change_map = {}
    for sid, last_date, close_t, close_t1 in df:
        sid = str(sid)
        last_date = str(last_date)[:10]
        if last_date != date_t:
            info = classify_daily_change(None, None, sid)
            info["change_note"] = f"今日({date_t})無成交或資料缺漏"
            meta["stale_count"] += 1
        elif close_t1 is None:
            info = classify_daily_change(None, None, sid)
            info["change_note"] = f"前一交易日({date_t1})資料缺漏"
            meta["missing_prev_count"] += 1
        else:
            info = classify_daily_change(close_t, close_t1, sid)
            if info["change_data_ok"]:
                meta["ok_count"] += 1
            else:
                meta["anomaly_count"] += 1
        info["close"] = round(float(close_t), 2) if close_t else None
        info["date"] = last_date
        change_map[sid] = info
    return change_map, meta
