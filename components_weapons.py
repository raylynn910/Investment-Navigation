import html
import datetime
import streamlit as st
import pandas as pd
import numpy as np
import api_client
import requests as req_lib
import plotly.graph_objects as go
import plotly.express as px


def render_html(html_str: str):
    """
    安全渲染 HTML，去除每行多餘縮排與空行，徹底防止 CommonMark 觸發 4-space 縮排程式碼區塊 (<pre><code>)
    """
    cleaned = " ".join(line.strip() for line in html_str.splitlines() if line.strip())
    st.markdown(cleaned, unsafe_allow_html=True)


# ============================================================
# 大盤天候與 N7 守護者 HUD
# ============================================================
def render_n7_guardian_hud(regime_data):
    """
    【N7 終極本金守護者 (Guardian V7) 實戰全景 HUD】
    頂部 4 個均勻分布的自適應卡片 (深色微霧，保證一頁全視，絕對無橫向捲軸)
    1. 🛡️ N7 建議持股水位
    2. 🌙 台指期夜盤 (TXF_NIGHT)
    3. 🛑 08:30 買單攔截器
    4. ⚡ 09:00 開盤防雙巴 (模組 C)
    """
    if not regime_data:
        st.warning("無大盤與期貨資訊 (API 暫無回應)")
        return

    n7 = regime_data.get("n7_guardian", {})
    eff_pct = n7.get("effective_equity_pct", regime_data.get("recommended_equity_pct", 50))
    spot_status = n7.get("spot_status", regime_data.get("name", "防守觀察"))
    spot_reason = n7.get("spot_reason", "")

    spot_status_clean = spot_status.split("(")[0].strip() if "(" in spot_status else spot_status
    if not spot_status_clean:
        spot_status_clean = "防守觀察"

    night_close = n7.get("night_close", 0.0)
    night_ret_pct = n7.get("night_ret_pct", 0.0)
    night_date = n7.get("night_date", "")
    night_date_clean = night_date[-5:] if night_date else "最新"
    txf_close = n7.get("txf_close", regime_data.get("close", 0.0))
    txf_ma5 = n7.get("txf_ma5", regime_data.get("ma5", 0.0))
    txf_ma20 = n7.get("txf_ma20", regime_data.get("ma20", 0.0))
    txf_ma60 = n7.get("txf_ma60", regime_data.get("ma60", 0.0))

    intercept_active = n7.get("night_intercept_active", False)
    intercept_reason = n7.get("night_intercept_reason", "")
    buy_cap = n7.get("night_buy_cap", eff_pct)

    mod_c_triggered = n7.get("mod_c_triggered", False)
    mod_c_reason = n7.get("mod_c_reason", "")
    night_k_val = n7.get("night_k_val", 50.0)

    spot_reason_escaped = html.escape(str(spot_reason), quote=True)
    intercept_reason_escaped = html.escape(str(intercept_reason), quote=True)
    mod_c_reason_escaped = html.escape(str(mod_c_reason), quote=True)

    # 顏色配置與行動大白話指示
    water_color = "#10b981" if eff_pct >= 80 else ("#f59e0b" if eff_pct >= 50 else "#f43f5e")
    water_bg = "rgba(16,185,129,0.08)" if eff_pct >= 80 else ("rgba(245,158,11,0.08)" if eff_pct >= 50 else "rgba(244,63,94,0.08)")
    water_action = "滿倉進攻" if eff_pct >= 80 else ("半倉防守" if eff_pct >= 50 else "空手避險")
    
    night_color = "#10b981" if night_ret_pct >= 0 else "#f43f5e"
    night_badge = f"{night_date_clean} 隔日夜盤" if night_date else "隔日夜盤"
    
    if intercept_active:
        if buy_cap == 0:
            intercept_color = "#f43f5e"
            intercept_bg = "rgba(244,63,94,0.08)"
            intercept_badge = "🚨 啟動攔截"
            intercept_title_action = "全面凍結"
            intercept_cap_text = "上限 0%"
            intercept_brief = "T+1夜盤摜破季線 ｜ 08:30 起全日禁止追買"
        else:
            intercept_color = "#f59e0b"
            intercept_bg = "rgba(245,158,11,0.08)"
            intercept_badge = "⚠️ 部位壓縮"
            intercept_title_action = "半倉上限"
            intercept_cap_text = f"上限 {buy_cap}%"
            intercept_brief = "T+1夜盤跌破月線 ｜ 今日買單上限壓至 50%"
    else:
        intercept_color = "#10b981"
        intercept_bg = "rgba(16,185,129,0.08)"
        intercept_badge = "🟢 正常放行"
        intercept_title_action = "正常放行"
        intercept_cap_text = f"上限 {buy_cap}%"
        if night_ret_pct >= 0:
            intercept_brief = f"T+1夜盤平穩 ({night_ret_pct:+.2f}%) ｜ 08:30 正常放行"
        else:
            intercept_brief = f"T+1夜盤未破線 ({night_ret_pct:+.2f}%) ｜ 08:30 正常放行"
    
    if mod_c_triggered:
        mod_c_color = "#f59e0b"
        mod_c_bg = "rgba(245,158,11,0.08)"
        mod_c_badge = "⚠️ 觸發防砍"
        mod_c_title_action = "分批延後"
        mod_c_action_text = "防砍阿呆谷"
        mod_c_brief = f"KD超賣({night_k_val:.1f}) ｜ 減碼單先砍半，日盤站回取消"
    else:
        mod_c_color = "#94a3b8"
        mod_c_bg = "rgba(255,255,255,0.03)"
        mod_c_badge = "⚪ 常規換股"
        mod_c_title_action = "09:00 執行"
        mod_c_action_text = "正常換股"
        mod_c_brief = "無極端超賣 ｜ 跌出30名開盤市價汰弱"

    hud_html = f"""
    <div style='display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; width: 100%; box-sizing: border-box;'>
        <div style='background: {water_bg}; border: 1px solid {water_color}; border-radius: 12px; padding: 12px 14px; min-width: 0; box-sizing: border-box; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.4);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;'>
                <span style='font-size: 12px; font-weight: 700; color: #94a3b8;'>🛡️ N7 建議水位</span>
                <span style='font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.08); color: {water_color}; font-weight: 800; white-space: nowrap;'>{spot_status_clean}</span>
            </div>
            <div style='display: flex; align-items: baseline; gap: 8px; margin: 4px 0;'>
                <span style='font-size: 26px; font-weight: 900; color: {water_color}; line-height: 1.1;'>{eff_pct}%</span>
                <span style='font-size: 13px; font-weight: 800; color: {water_color}; background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px;'>{water_action}</span>
            </div>
            <div style='font-size: 11px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' title='{spot_reason_escaped}'>
                T日收: {txf_close:,.0f} ｜ 季線: {txf_ma60:,.0f} ｜ 5MA: {txf_ma5:,.0f}
            </div>
        </div>

        <div style='background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; min-width: 0; box-sizing: border-box; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.4);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;'>
                <span style='font-size: 12px; font-weight: 700; color: #94a3b8;'>🌙 台指期夜盤</span>
                <span style='font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.08); color: #cbd5e1; white-space: nowrap;'>{night_badge}</span>
            </div>
            <div style='display: flex; align-items: baseline; gap: 8px; margin: 4px 0;'>
                <span style='font-size: 26px; font-weight: 900; color: #f8fafc; line-height: 1.1;'>{night_close:,.0f}</span>
                <span style='font-size: 15px; font-weight: 800; color: {night_color};'>{night_ret_pct:+.2f}%</span>
            </div>
            <div style='font-size: 11px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;'>
                即時 K: <b>{night_k_val:.1f}</b> ｜ 日盤昨收: {txf_close:,.0f}
            </div>
        </div>

        <div style='background: {intercept_bg}; border: 1px solid {intercept_color}; border-radius: 12px; padding: 12px 14px; min-width: 0; box-sizing: border-box; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.4);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;'>
                <span style='font-size: 12px; font-weight: 700; color: #94a3b8;'>🛑 08:30 買單攔截</span>
                <span style='font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.08); color: {intercept_color}; font-weight: 800; white-space: nowrap;'>
                    {intercept_badge}
                </span>
            </div>
            <div style='display: flex; align-items: baseline; gap: 8px; margin: 4px 0;'>
                <span style='font-size: 26px; font-weight: 900; color: {intercept_color}; line-height: 1.1;'>{intercept_cap_text}</span>
                <span style='font-size: 13px; font-weight: 800; color: {intercept_color}; background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px;'>{intercept_title_action}</span>
            </div>
            <div style='font-size: 11px; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' title='{intercept_reason_escaped}'>
                {intercept_brief}
            </div>
        </div>

        <div style='background: {mod_c_bg}; border: 1px solid {mod_c_color}; border-radius: 12px; padding: 12px 14px; min-width: 0; box-sizing: border-box; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.4);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;'>
                <span style='font-size: 12px; font-weight: 700; color: #94a3b8;'>⚡ 09:00 開盤防雙巴</span>
                <span style='font-size: 11px; padding: 2px 7px; border-radius: 4px; background: rgba(255,255,255,0.08); color: {mod_c_color}; font-weight: 800; white-space: nowrap;'>
                    {mod_c_badge}
                </span>
            </div>
            <div style='display: flex; align-items: baseline; gap: 8px; margin: 4px 0;'>
                <span style='font-size: 26px; font-weight: 900; color: {mod_c_color}; line-height: 1.1;'>{mod_c_action_text}</span>
                <span style='font-size: 13px; font-weight: 800; color: {mod_c_color}; background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px;'>{mod_c_title_action}</span>
            </div>
            <div style='font-size: 11px; color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' title='{mod_c_reason_escaped}'>
                {mod_c_brief}
            </div>
        </div>
    </div>
    """
    render_html(hud_html)


def render_macro_gap_indicator(regime_data):
    """大盤位階 + 天候燈號 + 進場指引 (MOO/MOC 分流)"""
    render_n7_guardian_hud(regime_data)


import os
import glob

@st.dialog("🔍 無狀態跨界追蹤比對 (十方極速陣)", width="large")
def show_boundary_tracking_modal():
    record_dir = r"C:\Users\m3192\OneDrive\桌面\四大因子輪動記錄"
    if not os.path.exists(record_dir):
        st.error(f"找不到資料夾: {record_dir}")
        return
        
    csv_files = glob.glob(os.path.join(record_dir, "*.csv"))
    if len(csv_files) < 2:
        st.error(f"資料夾內需至少有 2 個 CSV 檔案進行比對 (目前有 {len(csv_files)} 個)")
        return
        
    csv_files.sort(key=os.path.getmtime, reverse=True)
    file_t = csv_files[0]
    file_t_minus_1 = csv_files[1]
    
    st.markdown(f"<div style='font-size:13px; color:#94a3b8; margin-bottom:16px;'>自動比對最新兩份記錄檔:<br>T日 (今日): <b>{os.path.basename(file_t)}</b><br>T-1日 (昨日): <b>{os.path.basename(file_t_minus_1)}</b></div>", unsafe_allow_html=True)
    
    def _read_and_normalize_csv(filepath: str) -> pd.DataFrame:
        try:
            df = pd.read_csv(filepath, encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(filepath, encoding="cp950")
            
        # 清理欄位名稱空白與 BOM
        df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
        
        # 自動映射中英文欄位名稱 (相容 '代號'/'名稱' 與 'stock_id'/'stock_name')
        col_rename = {}
        for c in df.columns:
            c_lower = c.lower()
            if c in ["代號", "股號", "代碼", "股票代號", "股票代碼"] or c_lower in ["stock_id", "ticker", "symbol", "code"]:
                if "stock_id" not in df.columns and "stock_id" not in col_rename.values():
                    col_rename[c] = "stock_id"
            elif c in ["名稱", "股名", "公司名稱", "股票名稱"] or c_lower in ["stock_name", "name", "company"]:
                if "stock_name" not in df.columns and "stock_name" not in col_rename.values():
                    col_rename[c] = "stock_name"
        if col_rename:
            df = df.rename(columns=col_rename)
            
        if "stock_id" not in df.columns:
            raise KeyError(f"找不到股票代號欄位 (目前欄位: {list(df.columns)})")
        if "stock_name" not in df.columns:
            df["stock_name"] = ""
            
        # 判斷排名欄位
        rank_cols = [c for c in df.columns if "排名" in c or "rank" in c.lower() or "ƦW" in c]
        if rank_cols:
            df["Rank"] = pd.to_numeric(df[rank_cols[0]], errors="coerce")
        else:
            df["Rank"] = range(1, len(df) + 1)
            
        df["stock_id"] = df["stock_id"].astype(str).str.strip().str.replace(r"\.TW$", "", regex=True)
        df["stock_name"] = df["stock_name"].astype(str).str.strip()
        return df

    try:
        df_t = _read_and_normalize_csv(file_t)
        df_t1 = _read_and_normalize_csv(file_t_minus_1)
        
        # Merge
        df_merge = pd.merge(df_t[['stock_id', 'stock_name', 'Rank']], 
                            df_t1[['stock_id', 'stock_name', 'Rank']], 
                            on='stock_id', 
                            how='outer', 
                            suffixes=('_T', '_T1'))
        
        # 解決 nan 的問題 (若今日跌出名單外，從昨日資料補回股名)
        df_merge['stock_name'] = df_merge['stock_name_T'].replace('nan', np.nan).fillna(df_merge['stock_name_T1'])
        df_merge['Rank_T'] = df_merge['Rank_T'].fillna(999)
        df_merge['Rank_T1'] = df_merge['Rank_T1'].fillna(999)
        
        sells = df_merge[(df_merge['Rank_T1'] <= 15) & (df_merge['Rank_T'] > 15)].sort_values('Rank_T1').copy()
        buys = df_merge[(df_merge['Rank_T'] <= 9) & (df_merge['Rank_T1'] > 9)].sort_values('Rank_T').copy()
        holds = df_merge[(df_merge['Rank_T'] >= 10) & (df_merge['Rank_T'] <= 15) & (df_merge['Rank_T1'] <= 15)].sort_values('Rank_T').copy()
        
        def _fmt(r):
            if r == 999: return "不在名單內"
            return f"第 {int(r)} 名"
            
        sells['desc'] = [f"{r['stock_id']} {r['stock_name']} (昨日:{_fmt(r['Rank_T1'])} ➔ 今日:{_fmt(r['Rank_T'])})" for _, r in sells.iterrows()]
        buys['desc'] = [f"{r['stock_id']} {r['stock_name']} (昨日:{_fmt(r['Rank_T1'])} ➔ 今日:{_fmt(r['Rank_T'])})" for _, r in buys.iterrows()]
        holds['desc'] = [f"{r['stock_id']} {r['stock_name']} (昨日:{_fmt(r['Rank_T1'])} ➔ 今日:{_fmt(r['Rank_T'])})" for _, r in holds.iterrows()]
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("<h4 style='color:#f43f5e; font-size:16px;'>❌ 必須賣出 (Sells)</h4>", unsafe_allow_html=True)
            st.markdown("<span style='font-size:12px;color:#94a3b8;'>跌出15名外，明日市價賣出</span>", unsafe_allow_html=True)
            if sells.empty: st.markdown("<div style='color:#64748b; margin-top:8px;'>無</div>", unsafe_allow_html=True)
            for item in sells['desc']:
                st.markdown(f"<div style='background:rgba(244,63,94,0.15); padding:8px; border-radius:4px; margin-top:8px; font-size:14px; border:1px solid #f43f5e; color:#fecdd3;'>{item}</div>", unsafe_allow_html=True)
                
        with c2:
            st.markdown("<h4 style='color:#10b981; font-size:16px;'>🎯 必須買入 (Buys)</h4>", unsafe_allow_html=True)
            st.markdown("<span style='font-size:12px;color:#94a3b8;'>進入前9名的新血，明日市價買入</span>", unsafe_allow_html=True)
            if buys.empty: st.markdown("<div style='color:#64748b; margin-top:8px;'>無</div>", unsafe_allow_html=True)
            for item in buys['desc']:
                st.markdown(f"<div style='background:rgba(16,185,129,0.15); padding:8px; border-radius:4px; margin-top:8px; font-size:14px; border:1px solid #10b981; color:#a7f3d0;'>{item}</div>", unsafe_allow_html=True)
                
        with c3:
            st.markdown("<h4 style='color:#f59e0b; font-size:16px;'>🛡️ 緩衝區續抱 (Hold)</h4>", unsafe_allow_html=True)
            st.markdown("<span style='font-size:12px;color:#94a3b8;'>落在10~15名內，續抱不賣</span>", unsafe_allow_html=True)
            if holds.empty: st.markdown("<div style='color:#64748b; margin-top:8px;'>無</div>", unsafe_allow_html=True)
            for item in holds['desc']:
                st.markdown(f"<div style='background:rgba(245,158,11,0.15); padding:8px; border-radius:4px; margin-top:8px; font-size:14px; border:1px solid #f59e0b; color:#fde68a;'>{item}</div>", unsafe_allow_html=True)
                
    except Exception as e:
        st.error(f"比對失敗: {e}")

# ============================================================
# 武器一：波段輪動 (十方極速陣)
# ============================================================
def render_weapon1_swing(portfolio_data, regime_data):
    if not portfolio_data:
        st.error("後端資料載入失敗，請確認資料庫或執行盤後同步。")
        return

    holdings = portfolio_data.get("holdings", [])
    if not holdings:
        st.info("今日無排名訊號。")
        return

    df = pd.DataFrame(holdings)
    if df.empty:
        st.info("今日無持股資料。")
        return

    # rank 從 list 順序推算 (API 回傳已按 total_score 排序)
    df = df.reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))

    # 狀態標籤
    def _status(r):
        if r <= 9:
            return "🎯 買進 (前9)"
        elif r <= 15:
            return "🛡️ 續抱 (10~15)"
        else:
            return "⬇️ 汰弱 (>15)"

    df["操作"] = df["排名"].apply(_status)

    df_show = df.copy()
    if "total_score" in df_show.columns:
        df_show["total_score"] = df_show["total_score"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else "-")
    if "momentum_value" in df_show.columns:
        df_show["momentum_value"] = df_show["momentum_value"].apply(lambda x: f"{(x*100 if abs(x) <= 2.0 else x):+.1f}%" if pd.notnull(x) else "-")
    if "revenue_yoy_value" in df_show.columns:
        df_show["revenue_yoy_value"] = df_show["revenue_yoy_value"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "-")
    if "roe_value" in df_show.columns:
        df_show["roe_value"] = df_show["roe_value"].apply(lambda x: f"{x:.1f}%" if pd.notnull(x) else "-")
    # 雙向欄位容錯映射
    if "stock_name" not in df_show.columns and "name" in df_show.columns:
        df_show["stock_name"] = df_show["name"]
    if "latest_close" not in df_show.columns and "close" in df_show.columns:
        df_show["latest_close"] = df_show["close"]
    if "momentum_value" not in df_show.columns and "momentum_pct" in df_show.columns:
        df_show["momentum_value"] = df_show["momentum_pct"]
    if "revenue_yoy_value" not in df_show.columns and "revenue_yoy_pct" in df_show.columns:
        df_show["revenue_yoy_value"] = df_show["revenue_yoy_pct"]
    if "roe_value" not in df_show.columns and "roe_pct" in df_show.columns:
        df_show["roe_value"] = df_show["roe_pct"]

    if "latest_close" in df_show.columns:
        df_show["latest_close"] = df_show["latest_close"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) and str(x) != "-" else "-")

    if "latest_date" in df_show.columns and not df_show.empty and pd.notnull(df_show["latest_date"].iloc[0]):
        data_date_val = str(df_show["latest_date"].iloc[0])[:10]
    else:
        data_date_val = str(portfolio_data.get("metadata", {}).get("last_updated_time", "最新"))[:10]

    # Header Row
    col_t1, col_t2 = st.columns([0.75, 0.25])
    with col_t1:
        st.markdown(
            f"<div style='display:flex; align-items:baseline; gap:10px; margin-bottom:4px;'>"
            f"<span style='font-size:20px; font-weight:900; color:#38bdf8;'>🏛️ 核心一：波段輪動 (十方極速陣)</span>"
            f"<span style='font-size:12px; color:#10b981; font-weight:700;'>● 基準日: {data_date_val} (1,900+檔截面)</span>"
            f"</div>"
            f"<div style='font-size:12px; color:#94a3b8; margin-bottom:8px;'>"
            f"純因子輪動（動能 70% ｜ 營收年增 20% ｜ ROE 10%）：前 9 名滿水位買進 (每檔 ≈11.1%)，緩衝至 15 名以外無條件市價汰弱。"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_t2:
        if st.button("📊 跨界追蹤比對 (昨日 vs 今日)", use_container_width=True, key="btn_boundary_track"):
            show_boundary_tracking_modal()

    # 高保定零橫向捲軸響應式表格 (Top 15 精選主力)
    table_rows = []
    top_15_df = df_show.head(15)
    for idx, row in top_15_df.iterrows():
        rank = int(row["排名"])
        sid = str(row.get("stock_id", ""))
        sname = str(row.get("stock_name", row.get("name", "")))
        score = str(row.get("total_score", "-"))
        mom = str(row.get("momentum_value", row.get("momentum_pct", "-")))
        yoy = str(row.get("revenue_yoy_value", row.get("revenue_yoy_pct", "-")))
        close = str(row.get("latest_close", row.get("close", "-")))

        if rank == 1:
            rank_badge = "<span style='font-size:14px; font-weight:900; color:#fbbf24;'>🥇 1</span>"
        elif rank == 2:
            rank_badge = "<span style='font-size:14px; font-weight:900; color:#cbd5e1;'>🥈 2</span>"
        elif rank == 3:
            rank_badge = "<span style='font-size:14px; font-weight:900; color:#f97316;'>🥉 3</span>"
        elif rank <= 9:
            rank_badge = f"<span style='background:rgba(16,185,129,0.2); color:#10b981; font-weight:800; border-radius:4px; padding:2px 7px; font-size:12px;'>{rank}</span>"
        else:
            rank_badge = f"<span style='background:rgba(245,158,11,0.2); color:#f59e0b; font-weight:800; border-radius:4px; padding:2px 7px; font-size:12px;'>{rank}</span>"

        if rank <= 9:
            op_badge = "<span style='background:rgba(16,185,129,0.2); border:1px solid #10b981; color:#10b981; padding:3px 8px; border-radius:6px; font-weight:800; font-size:12px; white-space:nowrap;'>🎯 買進 (前9)</span>"
            row_bg = "background: rgba(16,185,129,0.03);"
            border_left = "border-left: 3px solid #10b981;"
        elif rank <= 15:
            op_badge = "<span style='background:rgba(245,158,11,0.2); border:1px solid #f59e0b; color:#f59e0b; padding:3px 8px; border-radius:6px; font-weight:800; font-size:12px; white-space:nowrap;'>🛡️ 續抱 (10~15)</span>"
            row_bg = "background: rgba(245,158,11,0.02);"
            border_left = "border-left: 3px solid #f59e0b;"
        else:
            op_badge = "<span style='background:rgba(244,63,94,0.2); border:1px solid #f43f5e; color:#f43f5e; padding:3px 8px; border-radius:6px; font-weight:800; font-size:12px; white-space:nowrap;'>⬇️ 汰弱 (>15)</span>"
            row_bg = "background: rgba(244,63,94,0.02);"
            border_left = "border-left: 3px solid #f43f5e;"

        mom_color = "#10b981" if mom.startswith("+") else ("#f43f5e" if mom.startswith("-") else "#cbd5e1")
        yoy_color = "#10b981" if yoy.startswith("+") else ("#f43f5e" if yoy.startswith("-") else "#cbd5e1")

        row_html = f"""
        <tr style='{row_bg} {border_left} border-bottom: 1px solid rgba(255,255,255,0.05); height: 36px;'>
            <td style='padding: 6px 4px; text-align: center;'>{rank_badge}</td>
            <td style='padding: 6px 4px; text-align: center; font-family: monospace; font-size: 14px; font-weight: 800; color: #f8fafc;'>{sid}</td>
            <td style='padding: 6px 8px; text-align: left; font-size: 14px; font-weight: 800; color: #ffffff;'>{sname}</td>
            <td style='padding: 6px 4px; text-align: center;'>{op_badge}</td>
            <td style='padding: 6px 8px; text-align: right; font-weight: 800; color: {mom_color}; font-size: 13px;'>{mom}</td>
            <td style='padding: 6px 8px; text-align: right; font-weight: 700; color: {yoy_color}; font-size: 13px;'>{yoy}</td>
            <td style='padding: 6px 8px; text-align: right; font-weight: 800; color: #f8fafc; font-size: 14px;'>{close}</td>
            <td style='padding: 6px 8px; text-align: right; font-weight: 600; color: #94a3b8; font-size: 13px;'>{score}</td>
        </tr>
        """
        table_rows.append(row_html)

        if rank == 9:
            divider_html = """
            <tr style='background: rgba(16,185,129,0.1);'>
                <td colspan='8' style='padding: 5px 12px; text-align: left; font-size: 11px; font-weight: 800; color: #a7f3d0; border-top: 1px dashed rgba(16,185,129,0.35); border-bottom: 1px dashed rgba(16,185,129,0.35);'>
                    ▲ 以上為前 9 名核心買進池 (每檔 ≈11.1%) ｜ ▼ 以下為 10~15 名安全緩衝區 (續抱不換股，跌出 15 名外始無條件市價汰弱)
                </td>
            </tr>
            """
            table_rows.append(divider_html)

    full_table_html = f"""
    <div style='width: 100%; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; overflow: hidden; background: #111827; box-shadow: 0 4px 20px rgba(0,0,0,0.4); margin-bottom: 14px;'>
        <table style='width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px;'>
            <thead>
                <tr style='background: rgba(255,255,255,0.05); border-bottom: 1px solid rgba(255,255,255,0.12); color: #94a3b8; font-weight: 700; height: 38px;'>
                    <th style='width: 7%; padding: 6px 4px; text-align: center;'>排名</th>
                    <th style='width: 9%; padding: 6px 4px; text-align: center;'>代號</th>
                    <th style='width: 16%; padding: 6px 8px; text-align: left;'>股票名稱</th>
                    <th style='width: 18%; padding: 6px 4px; text-align: center;'>作戰指令</th>
                    <th style='width: 12.5%; padding: 6px 8px; text-align: right;'>10日動能</th>
                    <th style='width: 12.5%; padding: 6px 8px; text-align: right;'>營收年增</th>
                    <th style='width: 12.5%; padding: 6px 8px; text-align: right;'>最新收盤</th>
                    <th style='width: 12.5%; padding: 6px 8px; text-align: right;'>綜合分 (Z)</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>
    </div>
    """
    render_html(full_table_html)

    # 深度查詢與 CSV 匯出抽屜
    with st.expander("🔍 展開全市場 1,900+ 檔完整資料庫 (支援搜尋 / 排序 / CSV 匯出)", expanded=False):
        desired_cols = ["排名", "stock_id", "stock_name", "操作", "total_score", "momentum_value", "revenue_yoy_value", "latest_close"]
        actual_cols = [c for c in desired_cols if c in df_show.columns]
        col_cfg = {
            "排名": st.column_config.NumberColumn("排名", width="x-small"),
            "stock_id": st.column_config.TextColumn("代號", width="x-small"),
            "stock_name": st.column_config.TextColumn("名稱", width="small"),
            "操作": st.column_config.TextColumn("操作", width="small"),
            "total_score": st.column_config.TextColumn("綜合分", width="x-small"),
            "momentum_value": st.column_config.TextColumn("10日動能", width="small"),
            "revenue_yoy_value": st.column_config.TextColumn("營收年增", width="small"),
            "latest_close": st.column_config.TextColumn("最新收盤", width="small"),
        }
        st.dataframe(df_show[actual_cols], column_config=col_cfg, use_container_width=True, hide_index=True)


# ============================================================
# 武器二：排名當沖 (汰弱強空陣)
# ============================================================
def render_weapon2_daytrade(portfolio_data, regime_data):
    st.markdown(
        "<div class='weapon-title weapon-2'>武器二：排名當沖 (汰弱強空陣)</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='font-size:13px; color:#94a3b8;'>"
        "當大盤開高 > +0.1% 時，針對跌出前 15 名的弱勢股，開盤 MOO 放空、尾盤 MOC 平倉。"
        "</p>",
        unsafe_allow_html=True,
    )

    # 無狀態 UI 限制：我們沒有 prev_rank，所以僅展示規則說明 + 排名 16~30 候選觀察池
    st.markdown(
        "<div style='padding:10px; background:rgba(255,255,255,0.04); border-radius:8px; font-size:13px; color:#94a3b8; margin-bottom:12px;'>"
        "📌 <b>無狀態提示</b>：UI 無法追蹤昨日排名。請對照桌面輪動記錄 CSV 中的前一交易日名單，"
        "找出「昨日 ≤ 15 → 今日 > 15」的跨界股票，即為今日空單候選。<br>"
        "以下列出目前排名 16~30 的邊緣觀察池："
        "</div>",
        unsafe_allow_html=True,
    )

    if not portfolio_data:
        return

    holdings = portfolio_data.get("holdings", [])
    df = pd.DataFrame(holdings)
    if df.empty or len(df) < 16:
        st.info("排名數據不足，無法顯示觀察池。")
        return

    df = df.reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))

    edge_pool = df[(df["排名"] >= 16) & (df["排名"] <= 30)]
    show_cols = ["排名", "stock_id", "stock_name", "total_score"]
    for c in ["momentum_value", "revenue_yoy_value", "latest_close"]:
        if c in edge_pool.columns:
            show_cols.append(c)
    st.dataframe(edge_pool[show_cols], use_container_width=True, hide_index=True)


def normalize_disposal_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    防禦性標準化處置股資料表結構，確保所有關鍵標籤、指標與布林值欄位皆存在且非空，
    徹底防止 KeyError 或型態不匹配導致前端 Error Boundary 報錯。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    
    df = df.copy()

    # 確保關鍵布林值與優先權欄位存在
    if 'is_hit_b' not in df.columns:
        if 'status' in df.columns:
            df['is_hit_b'] = df['status'].astype(str).str.contains('策略 B|策略B', regex=True)
        else:
            df['is_hit_b'] = False
    else:
        df['is_hit_b'] = df['is_hit_b'].fillna(False).astype(bool)

    if 'is_hit_c' not in df.columns:
        if 'status' in df.columns:
            df['is_hit_c'] = df['status'].astype(str).str.contains('策略 C|策略C', regex=True)
        else:
            df['is_hit_c'] = False
    else:
        df['is_hit_c'] = df['is_hit_c'].fillna(False).astype(bool)

    if 'priority' not in df.columns:
        df['priority'] = np.where(df['is_hit_b'], 1, np.where(df['is_hit_c'], 2, 4))
    else:
        df['priority'] = pd.to_numeric(df['priority'], errors='coerce').fillna(4).astype(int)

    defaults = {
        'stock_id': '',
        'name': '',
        'status': '監控中',
        'action_timing': '觀望',
        'entry_sop': '處置中持續監控動能',
        'turnover_str': '0元',
        'countdown_desc': '-',
        'td_to_exit': 99,
        'latest_price': 0.0,
        'disp_cum_ret': 0.0,
        'vol_ratio': 1.0,
        'ma5': 0.0,
    }
    for col, def_val in defaults.items():
        if col not in df.columns:
            df[col] = def_val
        else:
            df[col] = df[col].fillna(def_val)

    # 確保數值型欄位正確型態
    for num_col in ['latest_price', 'disp_cum_ret', 'vol_ratio', 'ma5']:
        df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0.0)

    return df


# ============================================================
# 武器三：處置出關狙擊 (處置王回測研究室)
# ============================================================
def render_weapon3_disposal():
    st.markdown(
        "<div class='weapon-title weapon-3'>🎯 核心二：處置出關狙擊 (策略 B 搶跑 ｜ 策略 C 開高走低做空)</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='font-size:13px; color:#94a3b8; margin-top:-6px;'>"
        "<b>【策略 B (T-2 搶跑買入)】</b>出關前 T-2 日收盤買進，T-1 收盤賣 50%，T 日開盤賣 50% ｜ "
        "<b>【策略 C (T-1警戒/T日出關空)】</b>處置超漲股出關開高走低，09:00 MOO 券賣放空，設 5% 停損，13:25 平倉。"
        "</p>",
        unsafe_allow_html=True,
    )

    # 2026/8/10 處置新制提示橫幅
    render_html("""
<div style='background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 8px; padding: 8px 14px; margin-bottom: 10px; font-size: 13px; color: #fbbf24; line-height: 1.5;'>
            <b>🟡 處置新制全面支援：</b>上市櫃一般處置為 <b>5 個營業日</b>（含當沖過高為 <b>7 個營業日</b>）、約每 <b>2 分鐘分盤撮合</b>。系統自動依據 DuckDB 與歷史交易日精確計算 T-2、T-1 與 T 日出關錨點。
        </div>
""")

    try:
        # 透過 api_client 向後端 API 或 published_signals.json 快照獲取，嚴禁前端直接連線 DuckDB 產生跨程序鎖死
        resp_disp = api_client.fetch_active_disposals(target_mode='latest_closed')
        dates_info = resp_disp.get('dates_info', {})
        last_closed_val = dates_info.get('last_closed_date', '2026-10-16')
        next_trading_val = dates_info.get('next_trading_date', '2026-10-19')
        is_closed = bool(dates_info.get('is_market_closed', True))

        last_closed_d = pd.to_datetime(last_closed_val).date() if last_closed_val else datetime.date(2026, 10, 16)
        next_trading_d = pd.to_datetime(next_trading_val).date() if next_trading_val else datetime.date(2026, 10, 19)

        WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
        last_closed_w = WEEKDAY_NAMES[last_closed_d.weekday()]
        next_trading_w = WEEKDAY_NAMES[next_trading_d.weekday()]

        data_disp = resp_disp.get('data', [])
        df_disp = normalize_disposal_df(pd.DataFrame(data_disp) if data_disp else pd.DataFrame())

        # 2. 次一交易日推演資料 (下週開盤/收盤作戰推演)
        df_next = pd.DataFrame()
        if is_closed:
            resp_next = api_client.fetch_active_disposals(target_mode='next_trading_day')
            data_next = resp_next.get('data', [])
            df_next = normalize_disposal_df(pd.DataFrame(data_next) if data_next else pd.DataFrame())

        if df_disp.empty:
            st.info("目前全市場暫無處置中或即將出關之股票。")
            return

        # 頂部：市場休市防呆與日期透明化橫幅
        market_badge = (
            "<span style='background:rgba(245,158,11,0.18); color:#f59e0b; padding:2px 8px; border-radius:4px; font-weight:700;'>🟡 休市中 (已啟用颱風假/國定補假防呆順延)</span>"
            if is_closed else
            "<span style='background:rgba(16,185,129,0.18); color:#10b981; padding:2px 8px; border-radius:4px; font-weight:700;'>🟢 市場開市中</span>"
        )
        render_html(f"""
        <div style='background: rgba(17, 24, 39, 0.85); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;'>
            <div style='font-size: 13px; color: #cbd5e1;'>
                📅 <b>基準收盤數據</b>：<span style='color: #38bdf8; font-weight: bold;'>{last_closed_d.strftime('%Y-%m-%d')} ({last_closed_w}收盤)</span>
                <span style='margin-left: 10px;'>{market_badge}</span>
            </div>
            <div style='font-size: 12px; color: #94a3b8;'>
                🎯 次一開盤交易日：<b style='color: #10b981;'>{next_trading_d.strftime('%Y-%m-%d')} ({next_trading_w})</b> ｜ 嚴格排除期貨夜盤日期干擾
            </div>
        </div>
        """)


        total_disp = len(df_disp)
        hits_b = df_disp[df_disp['is_hit_b'] == True]
        hits_c = df_disp[df_disp['is_hit_c'] == True]
        imminent = df_disp[df_disp['td_to_exit'].isin([0, 1, 2])]
        total_signals = len(hits_b) + len(hits_c)

        hits_next_b = df_next[df_next['is_hit_b'] == True] if not df_next.empty else pd.DataFrame()
        hits_next_c = df_next[df_next['is_hit_c'] == True] if not df_next.empty else pd.DataFrame()

        sig_color = "#10b981" if total_signals > 0 else "#94a3b8"
        imm_color = "#f59e0b" if len(imminent) > 0 else "#94a3b8"

        # 頂部 4 個高保定深色 KPI 方塊 (無橫向捲軸，自適應 4 等分)
        kpi_title_text = f"🎯 {last_closed_w}收盤訊號 (策略B/C)" if is_closed else "🎯 今日收盤訊號 (策略B/C)"
        kpi_html = f"""
        <div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 8px 0 14px 0;'>
            <div style='background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; text-align: center;'>
                <div style='font-size: 11px; color: #94a3b8; margin-bottom: 4px;'>{kpi_title_text}</div>
                <div style='font-size: 22px; font-weight: 800; color: {sig_color};'>{total_signals} <span style='font-size: 12px; font-weight: normal;'>檔觸發</span></div>
            </div>
            <div style='background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; text-align: center;'>
                <div style='font-size: 11px; color: #94a3b8; margin-bottom: 4px;'>⏳ 即將出關警戒 (T-1 / T-2)</div>
                <div style='font-size: 22px; font-weight: 800; color: {imm_color};'>{len(imminent)} <span style='font-size: 12px; font-weight: normal;'>檔蓄勢</span></div>
            </div>
            <div style='background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; text-align: center;'>
                <div style='font-size: 11px; color: #94a3b8; margin-bottom: 4px;'>📋 全市場處置中監控池</div>
                <div style='font-size: 22px; font-weight: 800; color: #38bdf8;'>{total_disp} <span style='font-size: 12px; font-weight: normal;'>檔在籠</span></div>
            </div>
            <div style='background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; text-align: center;'>
                <div style='font-size: 11px; color: #94a3b8; margin-bottom: 4px;'>🏆 處置雙向回測驗證</div>
                <div style='font-size: 22px; font-weight: 800; color: #10b981;'>B: 71.4% ｜ C: 73.3%</div>
            </div>
        </div>
        """
        render_html(kpi_html)

        # 實戰即時標靶專區 (收盤已觸發標的)
        if total_signals > 0:
            for idx, r in hits_b.iterrows():
                render_html(f"""
                <div style='background: rgba(16, 185, 129, 0.12); border-left: 4px solid #10b981; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;'>
                        <div>
                            <span style='font-size: 16px; font-weight: bold; color: #10b981;'>🔥 策略 B 觸發 (T-2 搶跑買入)：{r['stock_id']} {r['name']}</span>
                            <span style='margin-left: 12px; font-size: 13px; color: #cbd5e1;'>現價: {r['latest_price']:.2f} ｜ 處置累積: {r['disp_cum_ret']:+.1f}% ｜ 5MA: {r['ma5']:.2f} ｜ 成交金額: {r['turnover_str']}</span>
                        </div>
                        <span style='background: #10b981; color: #0a0a0a; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;'>{r['action_timing']}</span>
                    </div>
                    <div style='font-size: 13px; color: #a7f3d0; margin-top: 6px;'>
                        👉 <b>作戰SOP</b>：{r['entry_sop']}
                    </div>
                </div>
                """)

            for idx, r in hits_c.iterrows():
                render_html(f"""
                <div style='background: rgba(244, 63, 94, 0.12); border-left: 4px solid #f43f5e; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;'>
                        <div>
                            <span style='font-size: 16px; font-weight: bold; color: #f43f5e;'>🎯 策略 C 警戒 (出關做空)：{r['stock_id']} {r['name']}</span>
                            <span style='margin-left: 12px; font-size: 13px; color: #cbd5e1;'>現價: {r['latest_price']:.2f} ｜ 處置超漲: {r['disp_cum_ret']:+.1f}% ｜ 量比: {r['vol_ratio']:.2f} ｜ 成交金額: {r['turnover_str']}</span>
                        </div>
                        <span style='background: #f43f5e; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;'>{r['action_timing']}</span>
                    </div>
                    <div style='font-size: 13px; color: #fecdd3; margin-top: 6px;'>
                        👉 <b>作戰SOP</b>：{r['entry_sop']}
                    </div>
                </div>
                """)

        # 次一交易日作戰推演卡片 (例如 3597 映興)
        if is_closed and not hits_next_b.empty:
            for idx, r in hits_next_b.iterrows():
                render_html(f"""
                <div style='background: rgba(56, 189, 248, 0.10); border-left: 4px solid #38bdf8; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;'>
                        <div>
                            <span style='font-size: 16px; font-weight: bold; color: #38bdf8;'>🚀 次一交易日 {next_trading_w} ({next_trading_d.strftime('%m/%d')}) T-2 搶跑買點預備：{r['stock_id']} {r['name']}</span>
                            <span style='margin-left: 12px; font-size: 13px; color: #cbd5e1;'>現價: {r['latest_price']:.2f} ｜ 累積漲幅: {r['disp_cum_ret']:+.1f}% ｜ 5MA: {r['ma5']:.2f} ｜ 成交: {r['turnover_str']}</span>
                        </div>
                        <span style='background: #38bdf8; color: #0a0a0a; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;'>{next_trading_w} 13:25 尾盤進場</span>
                    </div>
                    <div style='font-size: 13px; color: #bae6fd; margin-top: 6px;'>
                        👉 <b>推演SOP</b>：{next_trading_w}盤中若維持站上 5MA 且成交金額 > 3,000 萬，於 13:25 尾盤市價買進 ｜ 次日(T-1)收盤賣 50% ｜ 出關日(T)09:00 開盤賣剩餘 50%！
                    </div>
                </div>
                """)

        if is_closed:
            tab_hits, tab_next_tab, tab_all, tab_quant = st.tabs([
                f"📌 {last_closed_w}收盤已觸發 ({last_closed_d.strftime('%m/%d')} 結算)", 
                f"🚀 次一交易日作戰推演 ({next_trading_d.strftime('%m/%d')} {next_trading_w})",
                "📋 全市場處置監控池 (出關倒數排序)", 
                "📊 蒙地卡羅 1,000 次尋優與回測驗證"
            ])
        else:
            tab_hits, tab_all, tab_quant = st.tabs([
                "🎯 策略 B / C 實戰命中名單", 
                "📋 全市場處置監控池 (出關倒數排序)", 
                "📊 蒙地卡羅 1,000 次尋優與回測驗證"
            ])
            tab_next_tab = None


        # ----------------------------------------------------
        # Tab 1: 策略 B / C 實戰命中名單
        # ----------------------------------------------------
        with tab_hits:
            hit_df = df_disp[(df_disp['is_hit_b'] == True) | (df_disp['is_hit_c'] == True) | (df_disp['priority'].isin([1, 2]))].copy()
            if hit_df.empty:
                st.markdown("""
                <div style='background: rgba(16, 185, 129, 0.04); border: 1px solid rgba(16, 185, 129, 0.15); border-radius: 12px; padding: 18px 20px; margin: 8px 0;'>
                    <div style='font-size: 15px; font-weight: bold; color: #10b981; margin-bottom: 6px;'>
                        🟢 今日全市場處置股暫無觸發【策略 B】或【策略 C】進場門檻
                    </div>
                    <div style='font-size: 13px; color: #94a3b8; line-height: 1.6;'>
                        • <b>策略 B (T-2 搶跑) 門檻</b>：出關倒數 2 天 ｜ 處置累積漲幅介於 -5%~+12% ｜ 收盤站上 5MA ｜ 成交金額 &ge; 3,000 萬<br>
                        • <b>策略 C (T-1 開高空) 門檻</b>：出關倒數 1 天 ｜ 處置累積漲幅 &ge; +7.2% (超漲) ｜ 量比 0.8~1.8 ｜ 成交金額 &ge; 3,000 萬<br>
                        今日無標的完全符合上述嚴格量化濾網。量化交易首重紀律，無訊號時空手觀望，靜待最佳勝率時機！
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                hit_cols = ["stock_id", "name", "countdown_desc", "latest_price", "disp_cum_ret", "turnover_str", "vol_ratio", "action_timing", "status", "entry_sop"]
                safe_hit_cols = [c for c in hit_cols if c in hit_df.columns]
                df_hit_show = hit_df[safe_hit_cols].copy()
                df_hit_show = df_hit_show.rename(columns={
                    "stock_id": "代號", "name": "名稱", "countdown_desc": "出關倒數",
                    "latest_price": "現價", "disp_cum_ret": "處置累積%", "turnover_str": "成交金額",
                    "vol_ratio": "量比", "action_timing": "作戰時機", "status": "命中訊號", "entry_sop": "作戰SOP"
                })
                if "現價" in df_hit_show.columns:
                    df_hit_show["現價"] = df_hit_show["現價"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")
                if "處置累積%" in df_hit_show.columns:
                    df_hit_show["處置累積%"] = df_hit_show["處置累積%"].apply(lambda x: f"{float(x):+.1f}%" if pd.notnull(x) else "-")
                if "量比" in df_hit_show.columns:
                    df_hit_show["量比"] = df_hit_show["量比"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")

                col_cfg_hit = {
                    "代號": st.column_config.TextColumn("代號", width="x-small"),
                    "名稱": st.column_config.TextColumn("名稱", width="small"),
                    "出關倒數": st.column_config.TextColumn("出關倒數", width="small"),
                    "現價": st.column_config.TextColumn("現價", width="x-small"),
                    "處置累積%": st.column_config.TextColumn("處置累積%", width="x-small"),
                    "成交金額": st.column_config.TextColumn("成交金額", width="x-small"),
                    "量比": st.column_config.TextColumn("量比", width="x-small"),
                    "作戰時機": st.column_config.TextColumn("作戰時機", width="medium"),
                    "命中訊號": st.column_config.TextColumn("命中訊號", width="medium"),
                    "作戰SOP": st.column_config.TextColumn("作戰SOP", width="large"),
                }
                st.dataframe(df_hit_show, column_config=col_cfg_hit, use_container_width=True, hide_index=True)

        # ----------------------------------------------------
        # Tab 1.5 (休市/週末專用): 次一交易日開盤/收盤作戰推演
        # ----------------------------------------------------
        if tab_next_tab is not None:
            with tab_next_tab:
                hit_next_df = df_next[(df_next['is_hit_b'] == True) | (df_next['is_hit_c'] == True) | (df_next['priority'].isin([1, 2]))].copy() if not df_next.empty else pd.DataFrame()
                if hit_next_df.empty:
                    st.markdown(f"""
                    <div style='background: rgba(56, 189, 248, 0.04); border: 1px solid rgba(56, 189, 248, 0.15); border-radius: 12px; padding: 18px 20px; margin: 8px 0;'>
                        <div style='font-size: 15px; font-weight: bold; color: #38bdf8; margin-bottom: 6px;'>
                            ℹ️ 次一交易日 {next_trading_w} ({next_trading_d.strftime('%m/%d')}) 盤前推演暫無觸發【策略 B】或【策略 C】進場門檻之處置股
                        </div>
                        <div style='font-size: 13px; color: #94a3b8; line-height: 1.6;'>
                            無標的符合出關前夕門檻。請持續追蹤全市場監控池。
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style='font-size:12.5px; color:#38bdf8; margin-bottom:8px;'>
                        🚀 <b>次一交易日 {next_trading_w} ({next_trading_d.strftime('%m/%d')}) 開盤/收盤作戰推演清單</b>（共 {len(hit_next_df)} 檔，提早部署{next_trading_w}尾盤買點與次日放空標的）：
                    </div>
                    """, unsafe_allow_html=True)
                    hit_cols_next = ["stock_id", "name", "countdown_desc", "latest_price", "disp_cum_ret", "turnover_str", "vol_ratio", "action_timing", "status", "entry_sop"]
                    safe_hit_next_cols = [c for c in hit_cols_next if c in hit_next_df.columns]
                    df_hit_next_show = hit_next_df[safe_hit_next_cols].copy()
                    df_hit_next_show = df_hit_next_show.rename(columns={
                        "stock_id": "代號", "name": "名稱", "countdown_desc": "出關倒數",
                        "latest_price": "現價", "disp_cum_ret": "處置累積%", "turnover_str": "成交金額",
                        "vol_ratio": "量比", "action_timing": "作戰時機", "status": "命中訊號", "entry_sop": "推演SOP"
                    })
                    if "現價" in df_hit_next_show.columns:
                        df_hit_next_show["現價"] = df_hit_next_show["現價"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")
                    if "處置累積%" in df_hit_next_show.columns:
                        df_hit_next_show["處置累積%"] = df_hit_next_show["處置累積%"].apply(lambda x: f"{float(x):+.1f}%" if pd.notnull(x) else "-")
                    if "量比" in df_hit_next_show.columns:
                        df_hit_next_show["量比"] = df_hit_next_show["量比"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")

                    col_cfg_next = {
                        "代號": st.column_config.TextColumn("代號", width="x-small"),
                        "名稱": st.column_config.TextColumn("名稱", width="small"),
                        "出關倒數": st.column_config.TextColumn("出關倒數", width="small"),
                        "現價": st.column_config.TextColumn("現價", width="x-small"),
                        "處置累積%": st.column_config.TextColumn("處置累積%", width="x-small"),
                        "成交金額": st.column_config.TextColumn("成交金額", width="x-small"),
                        "量比": st.column_config.TextColumn("量比", width="x-small"),
                        "作戰時機": st.column_config.TextColumn("作戰時機", width="medium"),
                        "命中訊號": st.column_config.TextColumn("命中訊號", width="medium"),
                        "推演SOP": st.column_config.TextColumn("推演SOP", width="large"),
                    }
                    st.dataframe(df_hit_next_show, column_config=col_cfg_next, use_container_width=True, hide_index=True)

        # ----------------------------------------------------
        # Tab 2: 全市場處置監控池 (出關倒數排序)
        # ----------------------------------------------------
        with tab_all:
            st.markdown(
                f"<div style='font-size:12px; color:#94a3b8; margin-bottom:8px;'>"
                f"全市場監控中處置標的共 <b>{len(df_disp)}</b> 檔（依優先級與出關倒數天數排序，2026/8/10 處置新制 5 營業日與 2 分盤）："
                f"</div>",
                unsafe_allow_html=True,
            )

            all_cols = ["stock_id", "name", "countdown_desc", "latest_price", "disp_cum_ret", "turnover_str", "vol_ratio", "status", "entry_sop"]
            safe_all_cols = [c for c in all_cols if c in df_disp.columns]
            df_all_show = df_disp[safe_all_cols].copy()
            df_all_show = df_all_show.rename(columns={
                "stock_id": "代號", "name": "名稱", "countdown_desc": "出關倒數",
                "latest_price": "現價", "disp_cum_ret": "處置累積%", "turnover_str": "成交金額",
                "vol_ratio": "量比", "status": "作戰信號", "entry_sop": "作戰執行SOP"
            })
            if "現價" in df_all_show.columns:
                df_all_show["現價"] = df_all_show["現價"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")
            if "處置累積%" in df_all_show.columns:
                df_all_show["處置累積%"] = df_all_show["處置累積%"].apply(lambda x: f"{float(x):+.1f}%" if pd.notnull(x) else "-")
            if "量比" in df_all_show.columns:
                df_all_show["量比"] = df_all_show["量比"].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) else "-")

            col_cfg_all = {
                "代號": st.column_config.TextColumn("代號", width="x-small"),
                "名稱": st.column_config.TextColumn("名稱", width="small"),
                "出關倒數": st.column_config.TextColumn("出關倒數", width="small"),
                "現價": st.column_config.TextColumn("現價", width="x-small"),
                "處置累積%": st.column_config.TextColumn("處置累積%", width="x-small"),
                "成交金額": st.column_config.TextColumn("成交金額", width="x-small"),
                "量比": st.column_config.TextColumn("量比", width="x-small"),
                "作戰信號": st.column_config.TextColumn("作戰信號", width="medium"),
                "作戰執行SOP": st.column_config.TextColumn("作戰執行SOP", width="large"),
            }
            st.dataframe(df_all_show, column_config=col_cfg_all, use_container_width=True, hide_index=True)

        # ----------------------------------------------------
        # Tab 3: 蒙地卡羅 1,000 次尋優與回測驗證
        # ----------------------------------------------------
        with tab_quant:
            st.markdown("#### 🔬 處置波段策略 (A & B) 回測報告")
            strat_ab_html = """
            <table style='width:100%; border-collapse:collapse; font-size:13px; text-align:center; margin-bottom:16px;'>
                <thead>
                    <tr style='background:rgba(255,255,255,0.06); border-bottom:1px solid rgba(255,255,255,0.1);'>
                        <th style='padding:8px; text-align:left;'>策略名稱</th>
                        <th style='padding:8px;'>核心邏輯</th>
                        <th style='padding:8px;'>歷史交易次數</th>
                        <th style='padding:8px;'>勝率</th>
                        <th style='padding:8px;'>平均單筆報酬</th>
                        <th style='padding:8px;'>獲利因子 (PF)</th>
                        <th style='padding:8px;'>2026/8/10 新制勝率</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style='border-bottom:1px solid rgba(255,255,255,0.04);'>
                        <td style='padding:8px; text-align:left; font-weight:bold; color:#38bdf8;'>策略 A (量縮突破)</td>
                        <td style='padding:8px; color:#cbd5e1;'>量<0.6均量 + 突破區間高 + 破5MA停損</td>
                        <td style='padding:8px;'>32 筆</td>
                        <td style='padding:8px; color:#10b981; font-weight:bold;'>62.5%</td>
                        <td style='padding:8px; color:#10b981;'>+4.82%</td>
                        <td style='padding:8px;'>2.85</td>
                        <td style='padding:8px; color:#10b981; font-weight:bold;'>66.7%</td>
                    </tr>
                    <tr style='border-bottom:1px solid rgba(255,255,255,0.04);'>
                        <td style='padding:8px; text-align:left; font-weight:bold; color:#10b981;'>策略 B (T-2搶跑)</td>
                        <td style='padding:8px; color:#cbd5e1;'>T-2 買進 + T-1 賣50% + T 賣50%</td>
                        <td style='padding:8px;'>49 筆</td>
                        <td style='padding:8px; color:#10b981; font-weight:bold;'>71.4%</td>
                        <td style='padding:8px; color:#10b981;'>+3.25%</td>
                        <td style='padding:8px;'>3.42</td>
                        <td style='padding:8px; color:#10b981; font-weight:bold;'>80.0%</td>
                    </tr>
                </tbody>
            </table>
            """
            render_html(strat_ab_html)

            st.markdown("#### 🎲 策略 C (出關日做空) 蒙地卡羅 1,000 次尋優 Top 參數")
            render_html("""
            <div style='background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:12px; margin-bottom:16px; font-size:13px; color:#cbd5e1; line-height:1.6;'>
                • <b>最佳處置累積漲幅門檻</b>：<code>disp_cum_ret &ge; +7.2% ~ +8.0%</code>（處置期間過熱累積賣壓）<br>
                • <b>出關日開盤跳空區間</b>：<code>+0.95% &le; 個股跳空 &le; +5.20%</code>（過低無肉，過高恐軋空）<br>
                • <b>最優停損防禦幅度</b>：<code>stop_loss = 4.8% ~ 5.6%</code>（避開早盤微幅洗盤噪音）<br>
                • <b>盲測集 (Out-of-Sample) 驗證</b>：勝率 50%~60%，獲利因子 1.28~1.80，95% 蒙地卡羅 MDD &le; 28.9%
            </div>
            """)

            # 歷史處置資料庫過濾查詢 (解耦至 FastAPI，嚴禁直接連線 DuckDB 避免跨行程鎖死)
            st.markdown("#### 📜 歷史處置資料庫查詢 (近 5 年)")
            f_col1, f_col2, f_col3 = st.columns(3)
            with f_col1:
                sel_year = st.selectbox("📅 歷史年份", ["全部", "2026", "2025", "2024", "2023", "2022"], index=0, key="disp_filter_yr")
            with f_col2:
                sel_tier = st.selectbox("⏱️ 撮合等級", ["全部", "2分盤 (新制)", "5分盤 (舊制)", "20分盤", "10分盤", "25分盤", "45分盤"], index=0, key="disp_filter_tier")
            with f_col3:
                sel_mkt = st.selectbox("🏛️ 市場別", ["全部", "上市", "上櫃"], index=0, key="disp_filter_mkt")

            hist_res = api_client.fetch_disposal_history(year=sel_year, market=sel_mkt, tier=sel_tier, limit=300)
            stats = hist_res.get("stats", {})
            hist_records = hist_res.get("data", [])
            df_hist = pd.DataFrame(hist_records) if hist_records else pd.DataFrame()

            if stats and stats.get("total_events", 0) > 0:
                render_html(f"""
                <div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px;'>
                    <div style='background: #111827; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 8px 12px; text-align: center;'>
                        <div style='font-size: 11px; color: #94a3b8;'>📜 歷史處置事件總數</div>
                        <div style='font-size: 18px; font-weight: bold; color: #38bdf8;'>{stats.get("total_events", 0):,} 筆</div>
                    </div>
                    <div style='background: #111827; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 8px 12px; text-align: center;'>
                        <div style='font-size: 11px; color: #94a3b8;'>🎯 處置全程勝率</div>
                        <div style='font-size: 18px; font-weight: bold; color: #10b981;'>{stats.get("win_rate", 0):.1f}%</div>
                    </div>
                    <div style='background: #111827; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 8px 12px; text-align: center;'>
                        <div style='font-size: 11px; color: #94a3b8;'>📈 全程平均報酬</div>
                        <div style='font-size: 18px; font-weight: bold; color: #10b981;'>{stats.get("avg_return", 0):+.2f}%</div>
                    </div>
                    <div style='background: #111827; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 8px 12px; text-align: center;'>
                        <div style='font-size: 11px; color: #94a3b8;'>🚀 平均最大拉升</div>
                        <div style='font-size: 18px; font-weight: bold; color: #f59e0b;'>{stats.get("avg_runup", 0):+.2f}%</div>
                    </div>
                </div>
                """)

            if not df_hist.empty:
                hist_rename = {
                    "stock_id": "代號", "name": "名稱", "market": "市場", "disposal_tier": "撮合等級",
                    "prepay_type": "預收限制", "regulatory_regime": "制度規整", "start_date": "起始日",
                    "end_date": "結束日", "exit_date": "出關日", "t_minus_1_close": "入處置前價",
                    "exit_close": "出關日收盤價", "disposal_return_pct": "處置全程報酬%",
                    "t0_to_exit_return_pct": "首日進場報酬%", "max_runup_pct": "最大拉升%",
                    "max_drawdown_pct": "最大跌幅%", "reason": "處置原因"
                }
                cols_hist = [c for c in hist_rename.keys() if c in df_hist.columns]
                df_h_show = df_hist[cols_hist].copy()
                for price_col in ["t_minus_1_close", "exit_close"]:
                    if price_col in df_h_show.columns:
                        df_h_show[price_col] = df_h_show[price_col].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) and x != "-" else "-")
                for pct_col in ["disposal_return_pct", "t0_to_exit_return_pct", "max_runup_pct", "max_drawdown_pct"]:
                    if pct_col in df_h_show.columns:
                        df_h_show[pct_col] = df_h_show[pct_col].apply(lambda x: f"{float(x):+.2f}%" if pd.notnull(x) and x != "-" else "-")
                df_h_show = df_h_show.rename(columns=hist_rename)

                st.dataframe(df_h_show, use_container_width=True, hide_index=True)
            else:
                st.info("查無符合條件之歷史處置紀錄。")

    except Exception as e:
        import traceback
        traceback.print_exc()
        st.error(f"處置資料載入錯誤: {e}")


# ============================================================
# 武器四：5D 動能斷崖空
# ============================================================
def render_weapon4_cliffhanger():
    st.markdown(
        "<div class='weapon-title weapon-4'>武器四：5D 動能斷崖空</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='font-size:13px; color:#94a3b8;'>"
        "5 日均線下穿 20 日均線且乖離過大，MOO 放空。"
        "</p>",
        unsafe_allow_html=True,
    )

    try:
        res = req_lib.get(f"{api_client.API_BASE_URL}/cliffhanger_shorts", timeout=8)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if not data:
                st.info("今日無斷崖空訊號。")
            else:
                df = pd.DataFrame(data)
                # 只顯示存在的欄位
                desired = ["stock_id", "stock_name", "turnover_ma5", "close", "ma5", "ma20"]
                actual = [c for c in desired if c in df.columns]
                if actual:
                    st.dataframe(df[actual], use_container_width=True, hide_index=True)
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("斷崖空 API 暫無資料。")
    except Exception as e:
        st.error(f"斷崖空資料載入錯誤: {e}")


# ============================================================
# 全市場 Z-Score 排行榜 (Top 30)
# ============================================================
def render_super_grid_v2(portfolio_data):
    st.markdown("### 📋 全市場 Z-Score 排行榜 (Top 30 觀測站)")
    if not portfolio_data:
        st.error("無法載入全市場資料")
        return

    holdings = portfolio_data.get("holdings", [])
    df = pd.DataFrame(holdings)
    if df.empty:
        st.warning("無排行資料")
        return

    # 自動加上排名
    df = df.reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))

    df_show = df.copy()
    if "total_score" in df_show.columns:
        df_show["total_score"] = df_show["total_score"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else "-")
    if "momentum_value" in df_show.columns:
        df_show["momentum_value"] = df_show["momentum_value"].apply(lambda x: f"{(x*100 if abs(x) <= 2.0 else x):+.1f}%" if pd.notnull(x) else "-")
    if "revenue_yoy_value" in df_show.columns:
        df_show["revenue_yoy_value"] = df_show["revenue_yoy_value"].apply(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "-")
    if "latest_close" in df_show.columns:
        df_show["latest_close"] = df_show["latest_close"].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else "-")

    desired_cols = ["排名", "stock_id", "stock_name", "total_score", "momentum_value", "revenue_yoy_value", "latest_close"]
    actual_cols = [c for c in desired_cols if c in df_show.columns]
    
    col_cfg = {
        "排名": st.column_config.NumberColumn("排名", width="x-small"),
        "stock_id": st.column_config.TextColumn("代號", width="x-small"),
        "stock_name": st.column_config.TextColumn("名稱", width="small"),
        "total_score": st.column_config.TextColumn("綜合分", width="x-small"),
        "momentum_value": st.column_config.TextColumn("10日動能", width="small"),
        "revenue_yoy_value": st.column_config.TextColumn("營收年增", width="small"),
        "latest_close": st.column_config.TextColumn("最新收盤", width="small"),
    }

    st.dataframe(df_show[actual_cols].head(30), column_config=col_cfg, use_container_width=True, hide_index=True)


def render_macro_hedge_radar(regime_data):
    """大盤避險雷達 (Macro Hedge Radar) — 注入 ComeWish 實戰風控體系"""
    if not regime_data:
        return
        
    st.markdown("<h3>大盤避險雷達 (Macro Hedge Radar)</h3>", unsafe_allow_html=True)
    
    taiex_close = regime_data.get('close', 0)
    ma5 = regime_data.get('ma5', 0)
    ma20 = regime_data.get('ma20', 0)
    ma60 = regime_data.get('ma60', 0)
    ma120 = regime_data.get('ma120', 0)
    mom_20d = regime_data.get('mom_20d', 0)
    hv_20 = regime_data.get('hv_20', 0)
    
    bias_60 = ((taiex_close / ma60) - 1) * 100 if ma60 > 0 else 0
    bias_20 = ((taiex_close / ma20) - 1) * 100 if ma20 > 0 else 0
    
    # 決定避險燈號 (Smart Hedge Module)
    # 🚨 空單進場條件：大盤破月線 且 波動率 > 15%
    is_panic_crash = taiex_close < ma20 and hv_20 > 15
    hedge_need = is_panic_crash
    
    # 狀態機顯示保留原分層邏輯以供參考
    is_black_swan = (taiex_close <= ma120 and ma120 > 0) or (mom_20d <= -0.10)
    is_broken = taiex_close < ma60 and taiex_close < ma20
    is_rebound = taiex_close < ma60 and taiex_close >= ma20
    is_warning = taiex_close >= ma60 and taiex_close < ma20
    is_bull = taiex_close >= ma60 and taiex_close >= ma20
    
    if hedge_need:
        signal = "恐慌性崩盤 (Panic Crash)"
        signal_color = "#ef5350"
        signal_msg = "🚨 大盤跌破月線且波動率(HV20)突破 15%！啟動 50% Beta 智慧避險模組。"
    elif is_black_swan:
        signal = "黑天鵝斷電 (Circuit Breaker)"
        signal_color = "#9C27B0"
        signal_msg = "大盤跌破半年線！現貨全數清倉變現，持有 100% 現金觀望。"
    elif is_rebound:
        signal = "破線強彈 (Recovery)"
        signal_color = "#FBBF24"
        signal_msg = "大盤站回月線！恐慌解除，若原有避險空單應立即全數平倉！"
    elif is_warning:
        signal = "震盪警戒 (Pullback)"
        signal_color = "#FBBF24"
        signal_msg = "大盤跌破月線但波動率平穩。停止買進新名單，弱勢股優先減碼。"
    else:
        signal = "多頭攻擊 (Bull Run)"
        signal_color = "#10b981"
        signal_msg = "大盤站穩月線，波動率安全。維持 100% 現貨持股，嚴禁放空避險！"
        
    c1, c2, c3 = st.columns([1, 1, 2])
    
    with c1:
        bias_color = "#10b981" if bias_60 > 0 else "#ef5350"
        st.markdown(f"""
        <div class='info-box'>
            <div class='status-text-muted'>目前大盤點位 (TAIEX)</div>
            <div style='font-size:24px; font-weight:bold;'>{taiex_close:,.2f}</div>
            <div style='font-size:14px; color:{bias_color}'>距季線(MA60): {bias_60:+.2f}%</div>
        </div>
        """, unsafe_allow_html=True)
        
    with c2:
        vol_note = "強彈劇震" if (is_bull and hv_20 > 25) else ("恐慌殺盤" if is_broken else "正常區間")
        st.markdown(f"""
        <div class='info-box'>
            <div class='status-text-muted'>市場風速計 (HV_20)</div>
            <div style='font-size:24px; font-weight:bold;'>{hv_20:.2f}%</div>
            <div style='font-size:14px; color:#94A3B8'>屬性: <strong style='color:#FBBF24'>{vol_note}</strong></div>
        </div>
        """, unsafe_allow_html=True)
        
    with c3:
        st.markdown(f"""
        <div class='info-box' style='border-left: 4px solid {signal_color}; height: 100%; display: flex; flex-direction: column; justify-content: center;'>
            <div style='font-size:18px; font-weight:bold; color:{signal_color}; margin-bottom:8px;'>{signal}</div>
            <div style='font-size:15px; line-height: 1.4;'>{signal_msg}</div>
        </div>
        """, unsafe_allow_html=True)
        
    # --- ComeWish AI 實戰風控總評 (Gemini LLM Integration) ---
    ai_hedge = api_client.fetch_macro_hedge_ai()
    if ai_hedge:
        insight = ai_hedge.get("comewish_insight", "")
        tactical = ai_hedge.get("tactical_command", "")
        exit_cond = ai_hedge.get("exit_condition", "")
        pos_ratio = ai_hedge.get("position_ratio", "100%")
        vol_ctx = ai_hedge.get("volatility_context", "")
        
        st.markdown(f"""
        <div class='info-box' style='background: rgba(16, 185, 129, 0.03); border: 1px solid rgba(16, 185, 129, 0.2); margin-top: 10px;'>
            <div style='display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;'>
                <div style='font-size: 16px; font-weight: bold; color: #10B981;'>
                    🧠 ComeWish 實戰風控大腦總評 (Gemini AI 指揮官)
                </div>
                <div style='font-size: 13px; color: #94A3B8; font-family: monospace;'>
                    即時戰略指導
                </div>
            </div>
            <div style='display: grid; grid-template-columns: 1fr 1.2fr 1fr; gap: 12px; margin-bottom: 12px;'>
                <div style='background: rgba(255,255,255,0.03); padding: 10px; border-radius: 8px;'>
                    <div class='status-text-muted' style='font-size: 12px;'>建議持股水位</div>
                    <div style='font-size: 18px; font-weight: bold; color: #10B981; margin-top: 4px;'>{pos_ratio}</div>
                </div>
                <div style='background: rgba(255,255,255,0.03); padding: 10px; border-radius: 8px;'>
                    <div class='status-text-muted' style='font-size: 12px;'>操盤行動指令</div>
                    <div style='font-size: 14px; font-weight: bold; margin-top: 4px;'>{tactical}</div>
                </div>
                <div style='background: rgba(255,255,255,0.03); padding: 10px; border-radius: 8px;'>
                    <div class='status-text-muted' style='font-size: 12px;'>避險平倉 / 退場點</div>
                    <div style='font-size: 13px; color: #FBBF24; margin-top: 4px;'>{exit_cond}</div>
                </div>
            </div>
            <div style='font-size: 14px; line-height: 1.6; color: #E2E8F0; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 8px; border-left: 3px solid #10B981;'>
                💬 <strong>ComeWish 操盤心法</strong>：{insight}
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
    
    with st.expander("智慧避險模組 (Smart Hedge Calculator)", expanded=False):
        hc1, hc2 = st.columns([1, 1])
        with hc1:
            total_value = st.number_input("目前股票部位總市值 AUM (NTD)", min_value=0, value=st.session_state.get('hedge_total_val', 10000000), step=100000)
            st.session_state['hedge_total_val'] = total_value
            st.markdown(f"<div style='font-size: 13px; color: #94A3B8; margin-top: 4px;'>對沖比例 (Beta Scaling): <strong>50%</strong> (目標對沖市值: {total_value * 0.5:,.0f} NTD)</div>", unsafe_allow_html=True)
        with hc2:
            st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
            if taiex_close > 0:
                if hedge_need:
                    target_hedge_value = total_value * 0.5
                    
                    tx_value = taiex_close * 200
                    mtx_value = taiex_close * 50
                    tmf_value = taiex_close * 10
                    
                    tx_lots = int(target_hedge_value // tx_value)
                    remainder = target_hedge_value % tx_value
                    
                    mtx_lots = int(remainder // mtx_value)
                    remainder = remainder % mtx_value
                    
                    tmf_lots = int(round(remainder / tmf_value))
                    
                    st.markdown(f"""
                    <div style='padding: 12px; background-color: rgba(239, 68, 68, 0.1); border: 1px solid #ef5350; border-radius: 8px;'>
                        <div style='font-size: 15px;'>🚨 系統建議：請放空以下台指期貨部位 (逼近 50% AUM)</div>
                        <div style='font-size: 18px; color: #ef5350; margin-top: 8px;'>
                            大台 (TX): <strong>{tx_lots}</strong> 口 ｜ 小台 (MTX): <strong>{mtx_lots}</strong> 口 ｜ 微台 (TMF): <strong>{tmf_lots}</strong> 口
                        </div>
                        <div style='font-size: 12px; color: #FBBF24; margin-top: 8px;'>⚠️ <strong>平倉紀律</strong>：若大盤收盤站回 5 日均線 ({ma5:,.0f} 點)，必須市價全數平倉空單！</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style='padding: 12px; background-color: rgba(16, 185, 129, 0.1); border: 1px solid #10b981; border-radius: 8px;'>
                        <div style='font-size: 15px;'>🟢 系統建議：<strong style='font-size: 20px; color: #10b981;'>無需避險 (0 口)</strong></div>
                        <div style='font-size: 12px; color: #94A3B8; margin-top: 6px;'>目前大盤未同時觸發破月線與 HV20>15% 條件，請維持現貨多單，嚴禁逆勢放空！</div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.markdown("無大盤點位資料，無法計算。")

def render_stock_futures_matrix(portfolio_data=None):
    """
    【雙核心攻擊】個股期貨自動映射與槓桿資金管理面板
    """
    try:
        from stock_futures_engine import StockFuturesEngine
        engine = StockFuturesEngine()
        
        st.markdown("<div class='kpi-title' style='margin-top: 15px; margin-bottom: 8px;'>🚀 【雙核心攻擊】十方極速陣 個股期貨自動映射與 2.5x~3x 槓桿資金計算機</div>", unsafe_allow_html=True)
        
        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            target_alloc = st.number_input("每檔標的預計分配本金 (NTD)", min_value=50000, value=1000000, step=50000)
        with col_c2:
            max_lev = st.slider("目標操作槓桿上限 (倍)", min_value=1.0, max_value=5.0, value=2.5, step=0.1)
            
        # 取得目前前 10 強持股標的
        stock_list = []
        holdings = []
        if portfolio_data:
            if "holdings" in portfolio_data:
                holdings = portfolio_data["holdings"]
            elif "stocks" in portfolio_data:
                holdings = portfolio_data["stocks"]
        if holdings:
            for s in holdings[:10]:
                close_p = s.get("latest_close", s.get("close", 0.0))
                if close_p is None or close_p == 0:
                    close_p = 100.0
                stock_list.append({
                    "stock_id": str(s.get("stock_id", "")),
                    "name": s.get("stock_name", s.get("name", "")),
                    "close": float(close_p),
                    "action": "BUY"
                })
        else:
            stock_list = [
                {"stock_id": "2383", "name": "台光電", "close": 568.0, "action": "BUY"},
                {"stock_id": "2059", "name": "川湖", "close": 1259.0, "action": "BUY"},
                {"stock_id": "2408", "name": "南亞科", "close": 52.8, "action": "BUY"},
                {"stock_id": "3498", "name": "陽程", "close": 192.0, "action": "BUY"}
            ]
        df_fut = engine.analyze_portfolio_futures(stock_list, target_alloc_per_stock=target_alloc, max_leverage=max_lev)
        
        # 整理為乾淨表格
        display_rows = []
        for idx, r in df_fut.iterrows():
            rec = r['recommendation']
            display_rows.append({
                '股號': r['stock_id'],
                '股名': r['name'],
                '現價': f"{r['price']:,.1f}",
                '下單執行方式': rec['exec_type'],
                '期貨代碼': rec['symbol'],
                '建議口數/張數': rec['suggested_contracts'],
                '所需保證金/本金': f"{rec['total_margin']:,.0f}",
                '名目曝險金額': f"{rec['nominal_exposure']:,.0f}",
                '實質槓桿': f"{rec['effective_leverage']:.2f}x",
                '交易稅率優勢': rec['tax_rate'],
                '實戰戰術說明': rec['notes']
            })
            
        st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True)
        st.markdown("<div style='font-size: 12px; color: #94A3B8; margin-top: -5px;'>💡 <strong>大神操盤心法</strong>：高價飆股以小型期貨（1口=100股）下單，除解決高價股曝險過大痛點外，期交稅僅 0.002%（遠低於現股當沖 0.15% 與現股 0.3%），每年省下數十萬手續費！</div>", unsafe_allow_html=True)
    except Exception as e:
        st.error(f"個股期貨模組載入失敗: {e}")

def render_disposal_events_panel():
    """
    【事件加速器】處置股票 (關狗籠) 雙向狙擊與當沖做空作戰面板
    """
    try:
        st.markdown("<div class='kpi-title' style='margin-top: 25px; margin-bottom: 12px;'>🎯 【事件加速器】處置股票 (關狗籠) 雙向作戰雷達：T-1 中午多單狙擊 & T+0 剛出關當沖做空</div>", unsafe_allow_html=True)
        
        resp_disp = api_client.fetch_active_disposals(target_mode='latest_closed')
        data_disp = resp_disp.get('data', [])
        df_disp = pd.DataFrame(data_disp) if data_disp else pd.DataFrame()
        if df_disp.empty:
            st.info("目前全市場無符合條件之處置股票事件。")
            return

            
        # 整理為乾淨表格 (僅保留符合動能條件之標的)
        display_rows = []
        for idx, r in df_disp.iterrows():
            display_rows.append({
                '作戰狀態': r['status'],
                '代碼': r['stock_id'],
                '名稱': r['name'],
                '現價': f"{r['latest_price']:,.1f}",
                '20日動能': f"{r['mom_20_pct']:+.1f}%",
                '處置迄日': r['end_date'],
                '出關倒數': f"{r['countdown_days']} 天" if r['countdown_days'] > 0 else "首日恢復交易",
                '進場觸發條件 (SOP)': r['entry_sop'],
                '出場/停利停損條件 (SOP)': r['exit_sop']
            })
            
        st.dataframe(
            pd.DataFrame(display_rows),
            column_config={
                '作戰狀態': st.column_config.TextColumn('作戰狀態', width='medium'),
                '代碼': st.column_config.TextColumn('代碼', width='small'),
                '名稱': st.column_config.TextColumn('名稱', width='small'),
                '現價': st.column_config.TextColumn('現價', width='small'),
                '20日動能': st.column_config.TextColumn('20日動能', width='small'),
                '處置迄日': st.column_config.TextColumn('處置迄日', width='small'),
                '出關倒數': st.column_config.TextColumn('出關倒數', width='small'),
                '進場觸發條件 (SOP)': st.column_config.TextColumn('進場觸發條件 (SOP)', width='large'),
                '出場/停利停損條件 (SOP)': st.column_config.TextColumn('出場/停利停損條件 (SOP)', width='large')
            },
            hide_index=True,
            use_container_width=True
        )
        st.markdown("<div style='font-size: 12px; color: #94A3B8; margin-top: -5px;'>💡 <strong>事件交易核心心法</strong>：量化交易的本質是「大樣本下的極高正期望值」。只要嚴格執行進出場 SOP 與停損紀律，長期執行即可在統計優勢下持續穩定獲利！</div>", unsafe_allow_html=True)
    except Exception as e:
        st.error(f"處置事件掃描模組載入失敗: {e}")


def render_cliffhanger_shorts_panel():
    """
    【斷崖做空獵殺】5 日動能由正轉負・神級斷崖做空雷達 (The Cliffhanger Shorts - Top 3~5 精選)
    """
    try:
        shorts = api_client.fetch_cliffhanger_shorts(min_turnover=100_000_000, delta_drop=-10.0, top_n=5)
        
        col_hdr1, col_hdr2 = st.columns([7, 3])
        with col_hdr1:
            st.markdown("""
            <div style='display: flex; align-items: center; gap: 10px; margin-top: 25px; margin-bottom: 8px; flex-wrap: wrap;'>
                <span style='font-size: 20px; font-weight: bold; color: #ef4444;'>💥 【斷崖做空獵殺】5 日動能由正轉負・神級斷崖做空雷達</span>
                <span style='background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; border-radius: 4px; padding: 2px 8px; font-size: 12px; color: #fca5a5; font-weight: bold;'>勝率 93.0% ｜ PF 41.44</span>
                <span style='background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; border-radius: 4px; padding: 2px 8px; font-size: 12px; color: #10b981; font-weight: bold;'>單筆均賺 +3.94%</span>
                <span style='background: rgba(56, 189, 248, 0.15); border: 1px solid #38bdf8; border-radius: 4px; padding: 2px 8px; font-size: 12px; color: #38bdf8; font-weight: bold;'>1億均量 ｜ 暴跌>10% ｜ 精選 Top 3~5</span>
            </div>
            """, unsafe_allow_html=True)
            
        with col_hdr2:
            if shorts:
                csv_content = "\n".join([f"{r['stock_id']}.TW" for r in shorts])
                st.download_button(
                    label="⚡ 匯出斷崖做空 XQ 匯入檔 (.dsl)",
                    data=csv_content,
                    file_name="cliffhanger_shorts.dsl",
                    mime="text/plain",
                    use_container_width=True
                )
                
        if not shorts:
            st.info("今日無符合「1 億均量 + 5 日動能急墜 > 10%」之神級斷崖做空標的（市場結構強勢無極端做空訊號）。")
            return
            
        df_shorts = pd.DataFrame(shorts)
        
        col_cfg = {
            'stock_id': st.column_config.TextColumn('代碼', width='small'),
            'stock_name': st.column_config.TextColumn('名稱', width='small'),
            'latest_close': st.column_config.NumberColumn('收盤價', width='small', format='%.2f'),
            'mom_5d_prev': st.column_config.NumberColumn('昨日5D動能', width='small', format='%+.2f%%'),
            'mom_5d_curr': st.column_config.NumberColumn('今日5D動能', width='small', format='%+.2f%%'),
            'delta_mom': st.column_config.NumberColumn('急墜幅度 (ΔMom)', width='small', format='%+.2f%%'),
            'turnover_ma5_str': st.column_config.TextColumn('5日均成交值', width='small'),
            'entry_sop': st.column_config.TextColumn('進場觸發 SOP', width='large'),
            'exit_sop': st.column_config.TextColumn('出場/停損 SOP', width='large')
        }
        
        display_cols = ['stock_id', 'stock_name', 'latest_close', 'mom_5d_prev', 'mom_5d_curr', 'delta_mom', 'turnover_ma5_str', 'entry_sop', 'exit_sop']
        df_show = df_shorts[[c for c in display_cols if c in df_shorts.columns]]
        
        selected_event = st.dataframe(
            df_show,
            column_config=col_cfg,
            hide_index=True,
            use_container_width=True,
            height=280,
            on_select='rerun',
            selection_mode='single-row'
        )
        
        if selected_event and selected_event.selection and selected_event.selection.rows:
            row_idx = selected_event.selection.rows[0]
            ticker = str(df_show.iloc[row_idx]['stock_id'])
            if st.session_state.get('selected_ticker') != ticker:
                st.session_state['selected_ticker'] = ticker
                st.rerun()
                
        st.markdown("<div style='font-size: 12px; color: #94A3B8; margin-top: -5px;'>💡 <strong>神級斷崖做空心法</strong>：當 5 日動能單日急墜超過 -10%（昨日正、今日負），代表主力主力踩踏倒貨。09:00 MOO 券賣或反彈碰 VWAP 券賣，13:25 尾盤 MOC 市價全平，實測勝率 93.0%、獲利因子 41.44！</div>", unsafe_allow_html=True)

    except Exception as e:
        st.error(f"斷崖做空雷達模組載入失敗: {e}")


def render_strategy_playbook_page():
    """
    【全新分頁】三大武器庫與全天候量化作戰手冊 (Weapons Playbook Page)
    """
    logo_b64 = get_logo_base64()
    logo_html = f"<img src='data:image/png;base64,{logo_b64}' style='width: 72px; height: 72px; border-radius: 14px; border: 2px solid #10b981; box-shadow: 0 0 20px rgba(16, 185, 129, 0.45); object-fit: cover;'>" if logo_b64 else "⚔️"

    st.markdown(f"""
    <div style='background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid rgba(16, 185, 129, 0.4); border-radius: 16px; padding: 22px 26px; margin-bottom: 25px; box-shadow: 0 10px 25px rgba(0,0,0,0.5);'>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;'>
            <div style='display: flex; align-items: center; gap: 18px;'>
                {logo_html}
                <div>
                    <h2 style='margin: 0; color: #f8fafc; font-size: 28px; font-weight: 900; background: linear-gradient(135deg, #ffffff 0%, #10b981 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
                        ⚔️ 【新科狀元系統與事件量化作戰手冊】
                    </h2>
                    <div style='color: #94a3b8; font-size: 15px; margin-top: 6px;'>
                        08:45 台指期天候分流 ｜ 新科狀元 10日動能 (2.0億流動性) ｜ 處置出關雙向狙擊 ｜ 2018~2026 全景回測實證
                    </div>
                </div>
            </div>
            <div style='background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; border-radius: 12px; padding: 12px 22px; text-align: center;'>
                <span style='color: #10b981; font-weight: 800; font-size: 22px;'>8年 16,992 筆</span><br>
                <span style='color: #cbd5e1; font-size: 13px;'>實扣手續費真實回測</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 1. 頂部四大當沖量化戰力大圖示 HUD (4 Big Metric Cards)
    st.markdown("<div style='font-size: 19px; font-weight: bold; color: #38bdf8; margin-bottom: 14px;'>⚡ 【四大因子跨界當沖：四大極致天候戰力榜 (2018~2026 最新回測實證)】</div>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #ef4444; border-radius: 14px; padding: 18px; text-align: center; box-shadow: 0 4px 15px rgba(239, 68, 68, 0.15);'>
<div style='font-size: 32px; margin-bottom: 4px;'>🔴 📉</div>
<div style='font-size: 16px; font-weight: bold; color: #ef4444;'>大盤開高 • 空弱勢</div>
<div style='font-size: 13px; color: #94a3b8; margin-bottom: 8px;'>標的：跌出前 10 名</div>
<div style='font-size: 28px; font-weight: 800; color: #f8fafc;'>72.8%</div>
<div style='font-size: 13px; color: #10b981; font-weight: bold;'>單筆 +1.66% ｜ PF 4.13 ｜ MDD -6.4%</div>
<div style='background: rgba(239, 68, 68, 0.15); border-radius: 6px; padding: 4px 8px; font-size: 13px; color: #fca5a5; margin-top: 10px;'>09:00 MOO 券賣 ｜ 13:25 尾盤平</div>
</div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #10b981; border-radius: 14px; padding: 18px; text-align: center; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.15);'>
<div style='font-size: 32px; margin-bottom: 4px;'>🟢 📈</div>
<div style='font-size: 16px; font-weight: bold; color: #10b981;'>大盤開低 • 多強勢</div>
<div style='font-size: 13px; color: #94a3b8; margin-bottom: 8px;'>標的：新進前 10 名</div>
<div style='font-size: 28px; font-weight: 800; color: #f8fafc;'>75.6%</div>
<div style='font-size: 13px; color: #10b981; font-weight: bold;'>單筆 +2.77% ｜ PF 5.67 ｜ 錯殺搶進</div>
<div style='background: rgba(16, 185, 129, 0.15); border-radius: 6px; padding: 4px 8px; font-size: 13px; color: #86efac; margin-top: 10px;'>09:00 MOO 買進 ｜ 13:25 尾盤平</div>
</div>""", unsafe_allow_html=True)
    with c3:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #38bdf8; border-radius: 14px; padding: 18px; text-align: center; box-shadow: 0 4px 15px rgba(56, 189, 248, 0.15);'>
<div style='font-size: 32px; margin-bottom: 4px;'>🟢 🚀</div>
<div style='font-size: 16px; font-weight: bold; color: #38bdf8;'>大盤開高 • 多強勢</div>
<div style='font-size: 13px; color: #94a3b8; margin-bottom: 8px;'>標的：新進前 10 名</div>
<div style='font-size: 28px; font-weight: 800; color: #f8fafc;'>75.7%</div>
<div style='font-size: 13px; color: #10b981; font-weight: bold;'>單筆 +2.64% ｜ PF 6.55 ｜ 順勢噴發</div>
<div style='background: rgba(56, 189, 248, 0.15); border-radius: 6px; padding: 4px 8px; font-size: 13px; color: #bae6fd; margin-top: 10px;'>09:05 站穩VWAP早買 (或尾盤)</div>
</div>""", unsafe_allow_html=True)
    with c4:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #f59e0b; border-radius: 14px; padding: 18px; text-align: center; box-shadow: 0 4px 15px rgba(245, 158, 11, 0.15);'>
<div style='font-size: 32px; margin-bottom: 4px;'>⚠️ 📉</div>
<div style='font-size: 16px; font-weight: bold; color: #f59e0b;'>大盤開低 • 空弱勢</div>
<div style='font-size: 13px; color: #94a3b8; margin-bottom: 8px;'>標的：跌出前 10 名</div>
<div style='font-size: 28px; font-weight: 800; color: #f8fafc;'>64.2%</div>
<div style='font-size: 13px; color: #10b981; font-weight: bold;'>單筆 +1.20% ｜ PF 2.53 ｜ 慎防洗盤</div>
<div style='background: rgba(245, 158, 11, 0.15); border-radius: 6px; padding: 4px 8px; font-size: 13px; color: #fde68a; margin-top: 10px;'>開盤不追空，等碰 VWAP 遇阻空</div>
</div>""", unsafe_allow_html=True)

    st.write("")


    # 2. 中層：極簡高對比全景作戰矩陣大表
    render_html("""
<div style='background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 20px; margin-bottom: 25px;'>
        <div style='font-size: 19px; font-weight: bold; color: #f8fafc; margin-bottom: 14px; display: flex; align-items: center; gap: 8px;'>
            🗺️ 【三大天候 × 三大作戰武器 全景分秒指令矩陣】
        </div>
        <table style='width: 100%; border-collapse: collapse; font-size: 15px; color: #cbd5e1;'>
            <thead>
                <tr style='background: rgba(255,255,255,0.05); border-bottom: 2px solid rgba(255,255,255,0.1); text-align: left;'>
                    <th style='padding: 12px; color: #38bdf8; width: 20%; font-size: 16px;'>08:45 台指期天候</th>
                    <th style='padding: 12px; color: #10b981; width: 26%; font-size: 16px;'>🏛️ 武器一：波段輪動 (十方極速陣)</th>
                    <th style='padding: 12px; color: #f59e0b; width: 27%; font-size: 16px;'>⚡ 武器二：排名當沖</th>
                    <th style='padding: 12px; color: #ec4899; width: 27%; font-size: 16px;'>🎯 武器三：處置出關狙擊</th>
                </tr>
            </thead>
            <tbody>
                <tr style='border-bottom: 1px solid rgba(255,255,255,0.05);'>
                    <td style='padding: 14px;'>
                        <span style='background: #ef4444; color: #fff; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 14px;'>🔴 開高 >+0.1%</span><br>
                        <span style='font-size: 13px; color: #fca5a5;'>空頭獵殺日</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>淘汰股</strong>：跌出10名 09:00 市價賣<br>
                        • <strong>新進股</strong>：新進10名 09:05 站穩VWAP早買 (或等13:25)
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>跌出10名</strong>：<span style='color:#ef4444; font-weight:bold;'>09:00 券賣做空 (勝率 72.8% ｜ 單筆 +1.66% ｜ PF 4.13)</span>🥇<br>
                        • <strong>新進10名</strong>：<span style='color:#94a3b8;'>嚴禁逆勢做空 (勝率僅 17.8%，必遭嘎空爆倉)</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>T-1多單</strong>：09:00~09:15 +3%~5%秒停利<br>
                        • <strong>T+0空單</strong>：<span style='color:#ef4444; font-weight:bold;'>09:20 破VWAP重拳券賣</span>🥇
                    </td>
                </tr>
                <tr style='border-bottom: 1px solid rgba(255,255,255,0.05);'>
                    <td style='padding: 14px;'>
                        <span style='background: #10b981; color: #fff; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 14px;'>🟢 開低 <-0.1%</span><br>
                        <span style='font-size: 13px; color: #86efac;'>多頭撿特價日</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>淘汰股</strong>：跌出10名 09:00 市價斬斷<br>
                        • <strong>新進股</strong>：新進10名 09:00 搶錯殺特價 (60MA下只出不進)
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>新進10名</strong>：<span style='color:#10b981; font-weight:bold;'>09:00 MOO市價做多 (勝率 75.6% ｜ 單筆 +2.77% ｜ PF 5.67)</span>🥇<br>
                        • <strong>跌出10名</strong>：<span style='color:#f59e0b;'>開盤不追空，等反彈碰VWAP遇阻再空 (勝率 64.2% ｜ 單筆 +1.20%)</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>T-1多單</strong>：09:00 平盤秒撤<br>
                        • <strong>T+0空單</strong>：<span style='color:#f59e0b; font-weight:bold;'>開盤不追空，等拉到09:30前昨收價(平盤)觀察，破均價線或早盤高才能空空看</span>
                    </td>
                </tr>
                <tr>
                    <td style='padding: 14px;'>
                        <span style='background: #64748b; color: #fff; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 14px;'>⚪ 開平 ±0.1%</span><br>
                        <span style='font-size: 13px; color: #cbd5e1;'>標準換股日</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>標準換股</strong>：09:00 賣淘汰(跌出10名)、13:25 買新進(前10檔滿水位)
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <span style='color:#94a3b8;'>⚪ 今日不當沖 (無極端天候情緒，保留手續費與稅金)</span>
                    </td>
                    <td style='padding: 14px; line-height: 1.6;'>
                        • <strong>T-1多單</strong>：09:10 衝高即出<br>
                        • <strong>T+0空單</strong>：09:25 破線輕倉空
                    </td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- 最佳進出場與停損機制深度實證 Callout Box -->
    <div style='background: linear-gradient(135deg, rgba(15,23,42,0.95) 0%, rgba(30,41,59,0.9) 100%); border: 1px solid rgba(56,189,248,0.3); border-radius: 14px; padding: 18px 22px; margin-bottom: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.3);'>
        <div style='font-size: 17px; font-weight: bold; color: #38bdf8; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;'>
            🔬 【武器二進出場機制最優化深度實證 (2018~2026 全量 14,657 筆回測結論)】
        </div>
        <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 20px; font-size: 14px; color: #cbd5e1; line-height: 1.7;'>
            <div style='background: rgba(255,255,255,0.03); border-radius: 10px; padding: 12px 16px;'>
                <strong style='color: #10b981; font-size: 15px;'>⏱️ 最優進出場時間架構：09:00 進 ｜ 13:25 出</strong><br>
                • <strong>進場時機</strong>：開高空弱勢與開低多強勢，均以 <span style='color:#10b981; font-weight:bold;'>09:00:00 開盤第一盤市價 (MOO)</span> 進場勝率最高！因為開盤是當日情緒最極端的高點/低點。<br>
                • <strong>出場時機</strong>：<span style='color:#38bdf8; font-weight:bold;'>13:20~13:25 尾盤市價單 (MOC)</span> 全數平倉。趨勢日具備「終點收斂性」（強的收最高、弱的殺收最低），持有整日勝率 (75.6%) 遠勝早盤急著平倉。
            </div>
            <div style='background: rgba(255,255,255,0.03); border-radius: 10px; padding: 12px 16px;'>
                <strong style='color: #ef4444; font-size: 15px;'>🛡️ 最優停損風控：改設 3.0%~3.5% 災難停損防線</strong><br>
                • <strong>避開早盤噪音</strong>：實測顯示設 1.5% 窄停損時，勝率從 <span style='color:#10b981; font-weight:bold;'>72.8% 暴跌至 51.9%</span>（早盤 09:05 假跳動極易被掃損，隨後股票才一路狂瀉收盤）！<br>
                • <strong>實戰建議</strong>：平時讓趨勢自然舒展抱到收盤，停損防線放寬至 <span style='color:#f59e0b; font-weight:bold;'>3.0%~3.5%</span>，專門防範極端妖股突發漲停之黑天鵝風險，期望值全場第一！
            </div>
        </div>
    </div>
""")

    # 3. 底層四大獨立武器 SOP 圖示卡片
    col_w1, col_w2, col_w3, col_w4 = st.columns(4)

    with col_w1:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #10b981; border-radius: 14px; padding: 18px; height: 100%;'>
<div style='font-size: 22px; color: #10b981; font-weight: bold; margin-bottom: 10px;'>🏛️ 武器一：波段輪動庫</div>
<div style='display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap;'>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>動能 70%</span>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>年增 20%</span>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>ROE 10%</span>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>2.0億流動性</span>
</div>
<div style='font-size: 14px; color: #cbd5e1; line-height: 1.7;'>
• <strong>陣型編制</strong>：新科狀元陣 (前10強各10%滿水位)。<br>
• <strong>流動性</strong>：5日均量 >= 2.0 億元 (零滑價)。<br>
• <strong>核心動能</strong>：10 日動能主導 (70%)。<br>
• <strong>淘汰鐵律</strong>：0 緩衝！跌出 10 名 09:00 MOO 必斬！<br>
• <strong>避險燈號</strong>：大盤破 60MA「只出不進」。
</div>
</div>""", unsafe_allow_html=True)

    with col_w2:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #f59e0b; border-radius: 14px; padding: 18px; height: 100%;'>
<div style='font-size: 22px; color: #f59e0b; font-weight: bold; margin-bottom: 10px;'>⚡ 武器二：排名當沖拳</div>
<div style='display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap;'>
<span style='background: rgba(239,68,68,0.2); color:#ef4444; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>開高空弱 72.8%</span>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>開低多強 75.6%</span>
<span style='background: rgba(56,189,248,0.2); color:#38bdf8; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>PF 4.13~5.67</span>
</div>
<div style='font-size: 14px; color: #cbd5e1; line-height: 1.7;'>
• <strong>開盤決策</strong>：08:45 看台指期直接決定當日多空。<br>
• <strong>進場時間</strong>：開高 09:00 MOO 空弱；開低 09:00 MOO 搶多強。<br>
• <strong>出場時間</strong>：13:20~13:25 尾盤 MOC 市價全平，零留倉！<br>
• <strong>停損風控最優解</strong>：實證改設 3.0%~3.5% 災難停損，避開早盤噪音掃損，期望值全場最高！<br>
• <strong>唯一禁忌</strong>：大盤開高嚴禁放空新進前10名 (做空勝率僅 17.8%)！
</div>
</div>""", unsafe_allow_html=True)

    with col_w3:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #ec4899; border-radius: 14px; padding: 18px; height: 100%;'>
<div style='font-size: 22px; color: #ec4899; font-weight: bold; margin-bottom: 10px;'>🎯 武器三：處置出關狙擊 (順勢版)</div>
<div style='display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap;'>
<span style='background: rgba(16,185,129,0.2); color:#10b981; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>T-1 12:00買</span>
<span style='background: rgba(239,68,68,0.2); color:#ef4444; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>大盤開高禁空</span>
<span style='background: rgba(236,72,153,0.2); color:#ec4899; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>09:45後進場</span>
<span style='background: rgba(139,92,246,0.2); color:#8b5cf6; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>破VWAP空</span>
</div>
<div style='font-size: 14px; color: #cbd5e1; line-height: 1.7;'>
• <strong>T-1 停利 (主戰力)</strong>：出關日 09:00~09:15 趁亂衝高 +3%~5% 獲利秒出。<br>
• 🚨 <strong>天候濾網 (保命符)</strong>：台指期若「開高 (+0.3%以上)」，<strong>當日嚴禁放空！</strong>大盤順風會導致出關股無量軋漲停。<br>
• <strong>T+0 券賣 (時間與VWAP濾網)</strong>：大盤開平/低時，<strong>09:45前嚴禁進場</strong>。等誘多結束，1分K <strong>跌破均價線 (VWAP)</strong> 時市價放空。<br>
• <strong>絕對防禦</strong>：自動掛 <strong>均價線 (VWAP) + 1%</strong> 觸價停損，站回立刻砍。尾盤 13:20 獲利回補。
</div>
</div>""", unsafe_allow_html=True)

    with col_w4:
        st.markdown("""<div style='background: #0f172a; border: 1px solid #f43f5e; border-radius: 14px; padding: 18px; height: 100%;'>
<div style='font-size: 22px; color: #f43f5e; font-weight: bold; margin-bottom: 10px;'>💥 武器四：5D動能斷崖空</div>
<div style='display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap;'>
<span style='background: rgba(244,63,94,0.2); color:#f43f5e; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>勝率 93.0%</span>
<span style='background: rgba(244,63,94,0.2); color:#f43f5e; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>PF 41.44</span>
<span style='background: rgba(56,189,248,0.2); color:#38bdf8; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold;'>1億均量</span>
</div>
<div style='font-size: 14px; color: #cbd5e1; line-height: 1.7;'>
• <strong>核心訊號</strong>：昨日 5D 動能 &ge; 0%、今日暴跌轉負 (ΔMom &le; -10%，精選 Top 3~5)。<br>
• <strong>09:00 進場</strong>：【激進】MOO 券賣；【穩健】碰 VWAP 券賣。<br>
• <strong>風控防線</strong>：漲破開盤價 +2%~2.5% 無條件停損！<br>
• <strong>13:25 平倉</strong>：尾盤 MOC 市價全數回補，絕不留倉！
</div>
</div>""", unsafe_allow_html=True)

    st.write("")
    
    # 4. 附錄：大數據警示與 RS 象限
    st.markdown("""<div style='background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 20px; margin-top: 15px;'>
<div style='font-size: 18px; font-weight: bold; color: #38bdf8; margin-bottom: 12px;'>📊 【量化實證大數據警示：8~9月打底 vs Q4 作帳真相】</div>
<div style='display: grid; grid-template-columns: 1fr 1fr; gap: 16px;'>
<div style='background: rgba(255,255,255,0.03); border-radius: 10px; padding: 14px; font-size: 15px; color: #cbd5e1; line-height: 1.8;'>
<strong style='color: #ef4444; font-size: 16px;'>💀 11,100 筆 8~9 月個股套牢死抱到 12 月底</strong><br>
• <strong>年底成功解套率</strong>：僅 <span style='color:#ef4444; font-weight:bold; font-size:17px;'>15.94%</span> (高達 84% 抱到年底依然深套)！<br>
• <strong>Q4 繼續腰斬雪崩率</strong>：<span style='color:#ef4444; font-weight:bold; font-size:17px;'>26.36%</span> (每 4 檔有 1 檔破底腰斬)！<br>
• <strong>操盤鐵律</strong>：大盤套牢等作帳，個股套牢等斷頭！必須依照 RS 相對強弱汰弱換強。
</div>
<div style='background: rgba(255,255,255,0.03); border-radius: 10px; padding: 14px; font-size: 15px; color: #cbd5e1; line-height: 1.8;'>
<strong style='color: #10b981; font-size: 16px;'>⚔️ 個股 vs 大盤 RS 相對強弱四大象限</strong><br>
• 🥇 <span style='color:#10b981; font-weight:bold; font-size:16px;'>【領先攻堅】(RS > +5%, 站穩MA60)</span>：Q4 真龍頭，拉回加碼。<br>
• 🛡️ <span style='color:#38bdf8; font-weight:bold; font-size:16px;'>【強勢抗跌】(RS > 0%)</span>：主力護盤，大盤一止跌率先噴出。<br>
• 💀 <span style='color:#ef4444; font-weight:bold; font-size:16px;'>【弱勢破底】(RS < -5%)</span>：大盤小跌它暴跌，果斷停損汰弱！
</div>
</div>
</div>""", unsafe_allow_html=True)







def render_market_regime_panel(regime_data):
    if not regime_data:
        return
        
    st.markdown("#### 大盤位階智慧警示燈 (Market Regime)")
    
    code = regime_data['code'].lower()
    name = regime_data['name']
    position = regime_data['position']
    action = regime_data['action']
    close = regime_data['close']
    ma60 = regime_data['ma60']
    ma120 = regime_data['ma120']
    close_vs_ma60 = regime_data['close_vs_ma60']
    mom_20d = regime_data['mom_20d']
    df_60d = regime_data['df_60d']
    
    col1, col2, col3 = st.columns([1, 1.5, 1.5])
    
    with col1:
        st.markdown(f"""
        <div style='height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:10px;'>
            <div class='regime-light {code}' title='{name}'></div>
            <div style='text-align:center; font-size:24px; font-weight:bold; margin-top:20px;'>{name}</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col2:
        mom_color = "#ef5350" if mom_20d > 0 else "#26a69a"
        bias_color = "#ef5350" if close_vs_ma60 > 0 else "#26a69a"
        st.markdown(f"""
        <div class='info-box' style='height: 100%;'>
            <div style='font-size:16px; margin-bottom:8px;'><strong>大盤技術指標</strong></div>
            <div style='font-size:14px; margin-bottom:4px;'><span class='status-text-muted'>大盤收盤:</span> <strong>{close:.2f}</strong></div>
            <div style='font-size:14px; margin-bottom:4px;'><span class='status-text-muted'>季線(MA60):</span> <strong>{ma60:.2f}</strong></div>
            <div style='font-size:14px; margin-bottom:4px;'><span class='status-text-muted'>偏離季線:</span> <strong style='color:{bias_color}'>{(close_vs_ma60*100):.2f}%</strong></div>
            <div style='font-size:14px; margin-bottom:4px;'><span class='status-text-muted'>20日動能:</span> <strong style='color:{mom_color}'>{(mom_20d*100):.2f}%</strong></div>
            <div style='font-size:14px;'><span class='status-text-muted'>半年線(MA120):</span> <strong>{ma120:.2f}</strong></div>
        </div>
        """, unsafe_allow_html=True)
        
    with col3:
        st.markdown(f"""
        <div class='info-box' style='height: 100%;'>
            <div style='font-size:16px; margin-bottom:8px;'><strong>明日操作建議</strong></div>
            <div style='font-size:14px; margin-bottom:12px;'><span class='status-text-muted'>建議持倉比例:</span> <strong style='font-size:20px;'>{position}</strong></div>
            <div style='font-size:14px; line-height:1.5;'><span class='status-text-muted'>行動:</span> {action}</div>
        </div>
        """, unsafe_allow_html=True)
        
    bg_colors = {
        "green": "rgba(16,185,129,0.05)",
        "yellow": "rgba(251,191,36,0.05)",
        "orange": "rgba(249,115,22,0.05)",
        "red": "rgba(239,68,68,0.05)"
    }
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_60d['Date'], y=df_60d['Close'], line=dict(color='white', width=2), name='大盤收盤'))
    fig.add_trace(go.Scatter(x=df_60d['Date'], y=df_60d['ma20'], line=dict(color='#60A5FA', width=1.5, dash='dash'), name='MA20'))
    fig.add_trace(go.Scatter(x=df_60d['Date'], y=df_60d['ma60'], line=dict(color='#FBBF24', width=2), name='MA60'))
    fig.add_trace(go.Scatter(x=df_60d['Date'], y=df_60d['ma120'], line=dict(color='#EF4444', width=1.5, dash='dash'), name='MA120'))
    
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor=bg_colors.get(code, 'rgba(0,0,0,0)'),
        font=dict(color='#E2E8F0'),
        margin=dict(l=10, r=10, t=10, b=10),
        height=180,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode='x unified'
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(255,255,255,0.05)', rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(255,255,255,0.05)')
    
    st.markdown("<div class='info-box' style='margin-top:10px;'>", unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

