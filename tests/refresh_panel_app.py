"""Streamlit page used by tests/test_refresh_panel.py: the refresh block on a fake sheet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from src.dashboard.components.refresh_panel import render_refresh_panel
from tests.test_refresh_request import FakeClient

if "fake_client" not in st.session_state:
    st.session_state.fake_client = FakeClient(exists=False)

with st.sidebar:
    render_refresh_panel(lambda: st.session_state.fake_client)
