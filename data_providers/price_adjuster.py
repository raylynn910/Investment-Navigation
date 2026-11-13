import duckdb
import pandas as pd
import logging

logger = logging.getLogger(__name__)

def auto_adjust_historical_prices(db_path: str = "data/market_data.duckdb"):
    """
    Detects abnormal price gaps (> 11% or < -11%) between consecutive trading days,
    which usually indicate Ex-Dividend (除權息) or Capital Reduction (減資).
    Automatically back-adjusts historical prices in DuckDB to maintain accurate momentum.
    """
    try:
        con = duckdb.connect(db_path)
        from tools.tw_price_limit import get_market_trading_dates
        market_dates = get_market_trading_dates(con)
        if len(market_dates) < 2:
            logger.info("Not enough market trading dates for auto-adjust check.")
            return
        latest_market_date, prev_market_date = market_dates[0], market_dates[1]

        # Get the latest 2 trading days for each stock
        query = """
        WITH RankedData AS (
            SELECT 
                stock_id,
                date,
                open,
                close,
                volume,
                ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) as rn
            FROM klines
        )
        SELECT 
            t1.stock_id,
            t1.date as current_date,
            t1.open as current_open,
            t2.date as prev_date,
            t2.close as prev_close
        FROM RankedData t1
        JOIN RankedData t2 ON t1.stock_id = t2.stock_id AND t1.rn = 1 AND t2.rn = 2
        WHERE t2.close > 0
        """
        
        df = con.execute(query).fetchdf()
        adjustments_made = 0
        
        for idx, row in df.iterrows():
            stock_id = row['stock_id']
            curr_open = row['current_open']
            prev_close = row['prev_close']
            curr_date = str(row['current_date'])[:10]
            prev_date = str(row['prev_date'])[:10]

            # 嚴禁在「前一交易日資料缺漏 (非全市場相鄰兩個有效交易日)」時誤判為除權息！
            if curr_date != latest_market_date or prev_date != prev_market_date:
                continue
            
            if prev_close <= 0 or curr_open <= 0:
                continue
                
            gap_pct = (curr_open / prev_close) - 1.0
            
            # Taiwan's daily limit is 10%. Anything beyond 11% is definitely an adjustment.
            if gap_pct > 0.11 or gap_pct < -0.11:
                ratio = curr_open / prev_close
                logger.info(f"Anomaly Detected for {stock_id} on {curr_date}: Gap {gap_pct*100:.2f}%. Applying Ratio: {ratio:.4f}")
                
                # Apply back-adjustment
                adjust_query = f"""
                UPDATE klines 
                SET 
                    open = open * {ratio},
                    high = high * {ratio},
                    low = low * {ratio},
                    close = close * {ratio},
                    volume = volume / {ratio}
                WHERE stock_id='{stock_id}' AND date < '{curr_date}'
                """
                con.execute(adjust_query)
                adjustments_made += 1
                
        con.commit()
        if adjustments_made > 0:
            logger.info(f"Successfully auto-adjusted {adjustments_made} stocks.")
            print(f"Successfully auto-adjusted {adjustments_made} stocks.")
            
    except Exception as e:
        logger.error(f"Error in auto_adjust_historical_prices: {e}")
        print(f"Error in auto_adjust_historical_prices: {e}")
    finally:
        if 'con' in locals():
            con.close()

if __name__ == "__main__":
    auto_adjust_historical_prices()
