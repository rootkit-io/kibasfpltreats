from __future__ import annotations

import streamlit as st

from fpl_xpts.config import AppConfig
from fpl_xpts.pipeline import run_live_projection


st.set_page_config(page_title="FPL xPts", layout="wide")
st.title("FPL xPts")

n_sim = st.sidebar.slider("Monte Carlo sims", min_value=1000, max_value=50000, value=10000, step=1000)
run_mc = st.sidebar.checkbox("Run Monte Carlo", value=True)

if st.sidebar.button("Refresh live projections", type="primary"):
    st.cache_data.clear()


@st.cache_data(ttl=900, show_spinner=True)
def load_projection(n_sim_value: int, include_mc: bool):
    return run_live_projection(AppConfig(n_sim=n_sim_value), include_mc=include_mc)


results = load_projection(n_sim, run_mc)

tab_picks, tab_mc, tab_fixtures, tab_audit = st.tabs(["Picks", "Monte Carlo", "Fixtures", "Audit"])

with tab_picks:
    weekly = results["weekly"]
    event = st.selectbox("Gameweek", sorted(weekly["event"].dropna().unique()))
    view = weekly.loc[weekly["event"] == event].head(100)
    st.dataframe(view, use_container_width=True, hide_index=True)

with tab_mc:
    mc = results["monte_carlo"]
    if mc.empty:
        st.info("Monte Carlo skipped.")
    else:
        event = st.selectbox("MC gameweek", sorted(mc["event"].dropna().unique()))
        st.dataframe(mc.loc[mc["event"] == event].head(100), use_container_width=True, hide_index=True)

with tab_fixtures:
    st.dataframe(results["fixtures_forecast"], use_container_width=True, hide_index=True)

with tab_audit:
    pf = results["player_fixture"]
    st.metric("Player-fixture rows", len(pf))
    st.metric("Zero-minute rows", int((pf["expected_minutes"] <= 0).sum()) if not pf.empty else 0)
    st.metric("Fixture rows", len(results["fixtures_forecast"]))

