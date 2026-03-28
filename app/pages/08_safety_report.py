"""Page 8 – Generate and display the final safety report."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Generate an LLM-synthesised safety report and offer download."""
    st.header("Safety Report")

    interactions: list[dict] = st.session_state.get("interactions", [])
    if not interactions:
        st.info("No interactions to report. Complete the previous steps first.")
        return

    if st.button("Generate Report"):
        with st.spinner("Generating safety report..."):
            # TODO: call report_generator.generate()
            st.session_state["safety_report"] = ""

    report: str = st.session_state.get("safety_report", "")

    if report:
        st.markdown(report)

        language = st.session_state.get("language", "en")
        if language != "en":
            if st.button(f"Translate to {language}"):
                with st.spinner("Translating..."):
                    # TODO: call translation_service.translate()
                    pass

        if st.button("Read Aloud"):
            with st.spinner("Generating audio..."):
                # TODO: call voice_service.text_to_speech()
                pass

        st.download_button(
            label="Download Report (TXT)",
            data=report,
            file_name="sahayak_safety_report.txt",
            mime="text/plain",
        )


render()
