"""Page 7 – Categorised drug-drug and drug-herb interactions."""

from __future__ import annotations

import streamlit as st


_SEVERITY_COLORS: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "moderate": "🟡",
    "low": "🟢",
}


def render() -> None:
    """Display interactions grouped by severity."""
    st.header("Interaction Results")

    confirmed: list[str] = st.session_state.get("confirmed_medicines", [])
    if not confirmed:
        st.info("Please confirm your medicine list first.")
        return

    if st.button("Run Interaction Check"):
        with st.spinner("Querying knowledge graph..."):
            # TODO: call interaction_checker + beers_checker + dosage_checker
            st.session_state["interactions"] = []

    interactions: list[dict] = st.session_state.get("interactions", [])

    if not interactions:
        st.info("No interactions found yet. Click the button above to check.")
        return

    for severity in ("critical", "high", "moderate", "low"):
        group = [i for i in interactions if i.get("severity") == severity]
        if group:
            icon = _SEVERITY_COLORS.get(severity, "")
            st.subheader(f"{icon} {severity.title()} ({len(group)})")
            for item in group:
                st.markdown(
                    f"**{item.get('drug_a', '?')}** ↔ **{item.get('drug_b', '?')}** — "
                    f"{item.get('description', 'No details available.')}"
                )


render()
