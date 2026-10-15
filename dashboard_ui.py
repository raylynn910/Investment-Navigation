import sys
import os

_project_root = r"c:\Users\m3192\OneDrive\文件\npx create-video@speaceX\twstockstest\dashboard_ui"
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
st.set_page_config(page_title="新科狀元・事件量化作戰手冊", layout="wide")

import api_client
import components_weapons as components

# ============================================================
# Session State Initialize
# ============================================================
if "is_syncing" not in st.session_state: st.session_state.is_syncing = False
if "is_syncing_score" not in st.session_state: st.session_state.is_syncing_score = False
if "is_syncing_revenue" not in st.session_state: st.session_state.is_syncing_revenue = False
if "is_syncing_revenue_moneydj" not in st.session_state: st.session_state.is_syncing_revenue_moneydj = False
if "is_syncing_financials" not in st.session_state: st.session_state.is_syncing_financials = False
if "is_syncing_disposal" not in st.session_state: st.session_state.is_syncing_disposal = False
if "is_exporting_signals" not in st.session_state: st.session_state.is_exporting_signals = False
if "export_signal_result" not in st.session_state: st.session_state.export_signal_result = None
if "sync_success_msg" not in st.session_state: st.session_state.sync_success_msg = None

# ============================================================
# CSS
# ============================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Urbanist:wght@400;700;900&display=swap');

:root {
    --color-bg:      #0a0a0a;
    --color-up:      #10B981;
    --color-down:    #F43F5E;
    --color-panel:   rgba(255, 255, 255, 0.04);
    --color-text:    #E2E8F0;
    --color-muted:   #94A3B8;
    --color-border:  rgba(255, 255, 255, 0.1);
}

.stApp { background: var(--color-bg); color: var(--color-text); font-family: 'Urbanist', sans-serif; }
[data-testid="stAppViewContainer"] { background: var(--color-bg); color: var(--color-text); }
.main .block-container { padding: 1.5rem; max-width: 100%; }
h1, h2, h3, h4, h5, h6 { color: #FFFFFF; font-family: 'Urbanist', sans-serif; font-weight: 900; }

.info-box {
    background-color: var(--color-panel);
    padding: 16px;
    border-radius: 16px;
    border: 1px solid var(--color-border);
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    backdrop-filter: blur(8px);
    margin-bottom: 16px;
}

.weapon-title {
    font-size: 20px;
    font-weight: 900;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--color-border);
}
.weapon-1 { color: #38bdf8; border-bottom-color: rgba(56,189,248,0.3); }
.weapon-2 { color: #f43f5e; border-bottom-color: rgba(244,63,94,0.3); }
.weapon-3 { color: #f59e0b; border-bottom-color: rgba(245,158,11,0.3); }
.weapon-4 { color: #a855f7; border-bottom-color: rgba(168,85,247,0.3); }

.tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: bold;
    margin-right: 6px;
}
.tag-moo { background: rgba(16,185,129,0.15); border: 1px solid #10b981; color: #10b981; }
.tag-moc { background: rgba(56,189,248,0.15); border: 1px solid #38bdf8; color: #38bdf8; }
.tag-short { background: rgba(244,63,94,0.15); border: 1px solid #f43f5e; color: #f43f5e; }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

# ============================================================
# Main Render
# ============================================================
def main():
    if st.session_state.sync_success_msg:
        st.info(st.session_state.sync_success_msg, icon="ℹ️")
        st.session_state.sync_success_msg = None

    # Top Header
    st.markdown("""
    <div style='margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.1); display: flex; justify-content: space-between; align-items: center;'>
        <div>
            <h1 style='margin: 0; font-size: 28px; background: linear-gradient(135deg, #fff 0%, #10b981 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
                新科狀元・全市場搜尋與量化作戰中心
            </h1>
            <div style='margin-top: 8px; font-size: 14px; color: #94a3b8;'>
                <span class='tag tag-moo'>真・全市場掃描 (1,900+ 檔)</span>
                <span class='tag' style='background: rgba(255,255,255,0.1); color: #fff;'>四大底線濾網 + 5000萬流動性</span>
                <span class='tag tag-moc'>四大因子 10/20/0/70</span>
            </div>
        </div>
        <div style='text-align: right;'>
            <div style='color: #10b981; font-weight: bold; font-size: 14px;'>● ENGINE ONLINE & DATA VERIFIED</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ============================================================
    # 📤 前後台分離：匯出並發布親友版最新訊號 (Git Push)
    # ============================================================
    col_pub_btn, col_pub_note = st.columns([0.65, 0.35])
    with col_pub_btn:
        if st.session_state.is_exporting_signals:
            st.button("⏳ 正在匯出訊號並推送至 Git...", disabled=True, width="stretch")
            try:
                from signal_publisher import export_and_publish_signals
                res = export_and_publish_signals()
                st.session_state.export_signal_result = res
            except Exception as e:
                st.session_state.export_signal_result = {"success": False, "message": str(e)}
            st.session_state.is_exporting_signals = False
            st.rerun()
        else:
            if st.button("📤 匯出並發布親友版最新訊號 (Git Push)", type="primary", width="stretch"):
                st.session_state.is_exporting_signals = True
                st.rerun()
    with col_pub_note:
        st.markdown(
            "<div style='font-size:12px; color:#10b981; line-height:1.4; padding-top:6px;'>"
            "● 防洗掉歷史快照 ｜ 雙雷達保守水位 ｜ 處置避雷針 ｜ 自動 Git Push"
            "</div>",
            unsafe_allow_html=True
        )

    if st.session_state.export_signal_result:
        res = st.session_state.export_signal_result
        if res.get("success"):
            git_info = res.get("git_status", {})
            st.success(
                f"✅ **親友版最新訊號發布成功 (資料日期: {res.get('data_date')})！**\n\n"
                f"• **核心一波段**：強勢前 9 強 `{res.get('top_9_count')}` 檔 ｜ 緩衝區 `{res.get('buffer_count')}` 檔 ｜ 最新跌出 15 名外 `{res.get('dropped_count')}` 檔\n"
                f"• **核心二處置**：策略 B 今日觸發 `{res.get('hits_b_count')}` 檔 ｜ 策略 C 今日觸發 `{res.get('hits_c_count')}` 檔\n"
                f"• **Git 狀態**：{git_info.get('message', '完成')}"
            )
        else:
            st.error(f"❌ 發布失敗: {res.get('message')}")
        if st.button("✖ 關閉發布通知", key="dismiss_export_msg"):
            st.session_state.export_signal_result = None
            st.rerun()

    st.markdown("<div style='margin-bottom: 12px;'></div>", unsafe_allow_html=True)

    # Toolbar
    col_t1, col_t2, col_t3, col_t4, col_t5, col_t6, col_t7 = st.columns(7)
    
    with col_t1:
        if st.session_state.is_syncing_score:
            st.button("全市場掃描中...", disabled=True, width="stretch")
            try:
                res = api_client.run_screener()
                st.session_state.sync_success_msg = "全市場跑分完畢！最新四大武器名單已更新。"
                st.cache_data.clear()
            except Exception as e:
                st.session_state.sync_success_msg = f"跑分錯誤: {str(e)}"
            st.session_state.is_syncing_score = False
            st.rerun()
        else:
            if st.button("🚀 執行全市場掃描跑分 (Daily)", width="stretch"):
                st.session_state.is_syncing_score = True
                st.rerun()
                
    with col_t2:
        if st.session_state.is_syncing_disposal:
            st.button("處置同步中...", disabled=True, width="stretch")
            try:
                from disposal_event_scanner import DisposalEventScanner
                scanner = DisposalEventScanner()
                cnt = scanner.sync_disposals()
                st.cache_data.clear()
                st.session_state.sync_success_msg = f"處置名單與 DuckDB 資料庫同步完畢 (已更新/儲存 {cnt} 筆記錄)！"
            except Exception as e:
                st.session_state.sync_success_msg = f"錯誤: {e}"
            st.session_state.is_syncing_disposal = False
            st.rerun()
        else:
            if st.button("🚨 同步處置出關名單", width="stretch"):
                st.session_state.is_syncing_disposal = True
                st.rerun()

    with col_t3:
        if st.session_state.is_syncing_revenue_moneydj:
            st.button("快報爬取中...", disabled=True, width="stretch")
            try:
                from moneydj_revenue_scraper import scrape_moneydj_revenue
                res = scrape_moneydj_revenue()
                if res.get("status") == "success":
                    st.session_state.sync_success_msg = f"MoneyDJ 今日營收快報爬取成功！已更新/寫入 {res.get('count')} 檔個股 (目標歸屬月: {res.get('target_date')})。"
                else:
                    st.session_state.sync_success_msg = f"爬取營收快報失敗: {res.get('message')}"
                st.cache_data.clear()
            except Exception as e:
                st.session_state.sync_success_msg = f"錯誤: {e}"
            st.session_state.is_syncing_revenue_moneydj = False
            st.rerun()
        else:
            if st.button("每月10以前爬蟲最新營收", width="stretch"):
                st.session_state.is_syncing_revenue_moneydj = True
                st.rerun()

    with col_t4:
        if st.session_state.is_syncing_revenue:
            st.button("官方全量同步中...", disabled=True, width="stretch")
            try:
                from update_revenue_openapi import fetch_and_update_openapi_revenue
                res = fetch_and_update_openapi_revenue()
                st.session_state.sync_success_msg = res.get("message", "全市場官方營收已同步完畢！")
                st.cache_data.clear()
            except Exception as e:
                st.session_state.sync_success_msg = f"官方營收同步錯誤: {e}"
            st.session_state.is_syncing_revenue = False
            st.rerun()
        else:
            if st.button("同步最新月營收 (OpenAPI)", width="stretch"):
                st.session_state.is_syncing_revenue = True
                st.rerun()

    with col_t5:
        if st.session_state.is_syncing_financials:
            st.button("財報同步中...", disabled=True, width="stretch")
            try:
                api_client.update_financials()
                st.session_state.sync_success_msg = "財報 (ROE) 背景更新已啟動"
            except: pass
            st.session_state.is_syncing_financials = False
            st.rerun()
        else:
            if st.button("同步最新財報ROE", width="stretch"):
                st.session_state.is_syncing_financials = True
                st.rerun()

    with col_t6:
        if st.session_state.is_syncing:
            st.button("K線同步中...", disabled=True, width="stretch")
            try:
                api_client.sync_daily_market_data()
                st.session_state.sync_success_msg = "K線歷史已自動補齊"
            except: pass
            st.session_state.is_syncing = False
            st.cache_data.clear()
            st.rerun()
        else:
            if st.button("同步全市場K線", width="stretch"):
                st.session_state.is_syncing = True
                st.rerun()

    with col_t7:
        if st.session_state.get("is_syncing_taiex", False):
            st.button("大盤同步中...", disabled=True, width="stretch")
            try:
                api_client.update_taiex()
                st.session_state.sync_success_msg = "大盤點位與波動率已更新"
            except: pass
            st.session_state.is_syncing_taiex = False
            st.cache_data.clear()
            st.rerun()
        else:
            if st.button("同步最新數據(大盤)", width="stretch"):
                st.session_state.is_syncing_taiex = True
                st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Auto-update TAIEX on first load
    if not st.session_state.get("has_auto_updated_taiex", False):
        with st.spinner("系統連線中：自動校正大盤水位與波動率..."):
            try:
                api_client.update_taiex()
                api_client.fetch_market_regime.clear()
            except:
                pass
        st.session_state.has_auto_updated_taiex = True
        st.rerun()

    # Fetch Data
    with st.spinner("載入全市場作戰數據..."):
        portfolio_data = api_client.fetch_portfolio_data()
        market_regime = api_client.fetch_market_regime()

    # Macro TAIEX Gap Indicator & Radar
    if market_regime:
        components.render_macro_hedge_radar(market_regime)
        components.render_market_regime_panel(market_regime)

    # 陣型C - 雙核心實戰作戰中心 (移除武器二、武器四，全寬展開零橫向卷軸)
    st.markdown("<h2 style='text-align: center; margin: 36px 0 20px 0; font-weight: 800; color: #f8fafc; letter-spacing: 0.5px;'>⚔️ 陣型C - 雙核心實戰作戰中心</h2>", unsafe_allow_html=True)
    
    # 核心一：波段輪動 (十方極速陣) 全寬展開
    components.render_weapon1_swing(portfolio_data, market_regime)
    
    st.markdown("<div style='margin: 32px 0;'><hr style='border: none; border-top: 1px solid rgba(255,255,255,0.08);'></div>", unsafe_allow_html=True)
    
    # 核心二：處置出關狙擊 (策略 B 搶跑 ｜ 策略 C 開高走低做空) 全寬展開
    components.render_weapon3_disposal()

    st.markdown("<div style='margin: 32px 0;'><hr style='border: none; border-top: 1px solid rgba(255,255,255,0.08);'></div>", unsafe_allow_html=True)
    
    # Super Grid Detail (The Full Market Ranks)
    components.render_super_grid_v2(portfolio_data)

if __name__ == "__main__":
    main()
