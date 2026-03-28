"""Page 5 – Review and manually resolve unrecognised medicines."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Display medicines that could not be matched and let the user correct them."""
    st.header("Unrecognised Medicines")

    unrecognized: list[str] = st.session_state.get("unrecognized_medicines", [])

    if not unrecognized:
        st.info("All medicines were recognised. You can skip this step.")
        return

    st.warning(
        f"{len(unrecognized)} medicine(s) could not be matched automatically. "
        "Please correct the names below."
    )

    corrected: list[str] = []
    for idx, name in enumerate(unrecognized):
        new_name = st.text_input(
            f"Medicine {idx + 1}", value=name, key=f"unrecognized_{idx}"
        )
        corrected.append(new_name)

    if st.button("Re-check & Continue →"):
        with st.spinner("Re-matching corrected names..."):
            # TODO: re-run drug_normalizer on corrected names
            st.session_state["unrecognized_medicines"] = corrected

        st.success("Corrections saved.")


render()
