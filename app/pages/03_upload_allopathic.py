"""Page 3 – Upload allopathic prescription image for OCR."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Allow user to upload an allopathic prescription image and run OCR."""
    st.header("Upload Allopathic Prescription")

    uploaded = st.file_uploader(
        "Upload a photo or scan of your allopathic prescription",
        type=["png", "jpg", "jpeg", "pdf"],
        key="allopathic_uploader",
    )

    if uploaded is not None:
        st.session_state["allopathic_image"] = uploaded
        st.image(uploaded, caption="Uploaded prescription", use_container_width=True)

    if st.button("Extract Medicines →"):
        if st.session_state.get("allopathic_image") is None:
            st.warning("Please upload an image first.")
            return

        with st.spinner("Running OCR and extracting medicines..."):
            # TODO: call ocr_service + drug_extractor
            st.session_state["allopathic_medicines"] = []

        st.success("Extraction complete. Proceed to the next step.")


render()
