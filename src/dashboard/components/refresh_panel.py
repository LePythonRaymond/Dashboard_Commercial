"""
Sidebar block "Actualiser les données": ask the server to re-read Furious now.

The click writes a request in the Actualisation tab (src/integrations/refresh_request.py).
The server's watcher picks it up within a minute and rewrites the sheets (2 to 6
minutes). Meanwhile this block re-reads the tab every 10 seconds; when the run that
served the request ends, it empties the cached sheets and reloads the page.
"""

import time
from datetime import datetime
from typing import Callable, Optional, Tuple

import streamlit as st

from src.integrations import refresh_request as rr

REQUEST_KEY = "_refresh_requested_at"
STATE_KEY = "_refresh_state"
POLL_SECONDS = 10
# Without a pending request of this session, the tab is re-read at most this often
IDLE_MAX_AGE_SECONDS = 30


def _read_state(client_factory: Callable, max_age: float) -> Tuple[Optional[rr.RefreshState], str]:
    """The tab's state, re-read when older than max_age; (None, error) when Google fails."""
    cached = st.session_state.get(STATE_KEY)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1], ""
    try:
        state = rr.read_state(client_factory())
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    st.session_state[STATE_KEY] = (time.monotonic(), state)
    return state, ""


def _body(client_factory: Callable) -> None:
    requested_at: Optional[datetime] = st.session_state.get(REQUEST_KEY)
    state, error = _read_state(client_factory, 0 if requested_at else IDLE_MAX_AGE_SECONDS)

    if requested_at and state and state.served(requested_at):
        del st.session_state[REQUEST_KEY]
        if state.status == rr.STATUS_DONE:
            st.cache_data.clear()  # the sheets are read again with the new data
        st.rerun()

    if state and state.data_as_of:
        st.caption(f"Données Furious du {rr.paris_time(state.data_as_of, with_date=True)}")

    phase, text = rr.describe(state, rr.now_utc()) if state else ("idle", "")
    busy = phase in ("pending", "running")
    if st.button(
        "🔄 Actualiser les données",
        disabled=busy,
        use_container_width=True,
        help="Relit Furious et réécrit les feuilles, comme le run de 6 h (2 à 6 minutes).",
    ):
        try:
            st.session_state[REQUEST_KEY] = rr.write_request(client_factory())
            st.session_state.pop(STATE_KEY, None)
        except Exception as e:
            st.error(f"Impossible d'envoyer la demande : {type(e).__name__}: {e}")
        else:
            st.rerun()

    if error:
        st.warning(f"État de l'actualisation illisible ({error}).")
    elif phase in ("pending", "running"):
        st.info(text)
    elif phase == "stuck":
        st.warning(text)
    elif phase == "failed":
        st.error(text)
    elif phase == "done" and requested_at is None:
        st.caption(text)


def render_refresh_panel(client_factory: Callable) -> None:
    """Draw the block; client_factory returns the dashboard's GoogleSheetsClient."""
    fragment = getattr(st, "fragment", None)
    if st.session_state.get(REQUEST_KEY) and fragment is not None:
        # Only this block reruns every 10 s, not the whole page
        fragment(run_every=POLL_SECONDS)(_body)(client_factory)
        return
    _body(client_factory)
    if st.session_state.get(REQUEST_KEY) and fragment is None:
        st.button("Vérifier l'avancement", use_container_width=True)
