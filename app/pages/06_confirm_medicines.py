"""Page 6 – Confirm the final medicine list before interaction check."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Show combined medicine list and let the user confirm or edit."""
    st.header("Confirm Your Medicine List")

    allopathic: list[str] = st.session_state.get("allopathic_medicines", [])
    ayurvedic: list[str] = st.session_state.get("ayurvedic_medicines", [])
    combined = allopathic + ayurvedic

    if not combined:
        st.info("No medicines detected yet. Please complete the upload steps first.")
        return

    st.subheader("Allopathic")
    for med in allopathic:
        st.write(f"- {med}")

    st.subheader("Ayurvedic / Herbal")
    for med in ayurvedic:
        st.write(f"- {med}")

    extra = st.text_input("Add another medicine (optional)")
    if extra:
        combined.append(extra)

    if st.button("Confirm & Check Interactions →"):
        st.session_state["confirmed_medicines"] = combined
        st.success(f"{len(combined)} medicine(s) confirmed.")


render()
