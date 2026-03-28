"""Page 4 – Upload ayurvedic / herbal medicine image for OCR."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Allow user to upload an ayurvedic medicine image and run OCR."""
    st.header("Upload Ayurvedic / Herbal Medicines")

    uploaded = st.file_uploader(
        "Upload a photo of your ayurvedic / herbal medicine labels",
        type=["png", "jpg", "jpeg", "pdf"],
        key="ayurvedic_uploader",
    )

    if uploaded is not None:
        st.session_state["ayurvedic_image"] = uploaded
        st.image(uploaded, caption="Uploaded ayurvedic label", use_container_width=True)

    if st.button("Extract Medicines →"):
        if st.session_state.get("ayurvedic_image") is None:
            st.warning("Please upload an image first.")
            return

        with st.spinner("Running OCR and extracting medicines..."):
            # TODO: call ocr_service + drug_extractor for ayurvedic
            st.session_state["ayurvedic_medicines"] = []

        st.success("Extraction complete. Proceed to the next step.")


render()
