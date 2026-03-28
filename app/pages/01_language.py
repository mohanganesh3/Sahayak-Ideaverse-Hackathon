"""Page 1 – Language selection."""

from __future__ import annotations

import streamlit as st

from app.config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES


def render() -> None:
    """Render the language selection page."""
    st.header("Select Language / भाषा चुनें")

    selected = st.selectbox(
        "Choose your preferred language:",
        options=list(SUPPORTED_LANGUAGES.keys()),
        format_func=lambda code: f"{SUPPORTED_LANGUAGES[code]} ({code})",
        index=list(SUPPORTED_LANGUAGES.keys()).index(
            st.session_state.get("language", DEFAULT_LANGUAGE)
        ),
    )

    if st.button("Continue →"):
        st.session_state["language"] = selected
        st.success(f"Language set to {SUPPORTED_LANGUAGES[selected]}")


render()
