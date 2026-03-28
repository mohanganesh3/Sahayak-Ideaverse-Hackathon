"""SAHAYAK – Streamlit entry point and multi-page configuration."""

from __future__ import annotations

import streamlit as st


def _configure_page() -> None:
    """Set Streamlit page config with SAHAYAK branding."""
    st.set_page_config(
        page_title="SAHAYAK – Medication Safety Assistant",
        page_icon="💊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def _apply_branding() -> None:
    """Render the SAHAYAK header and sidebar branding."""
    st.sidebar.title("SAHAYAK")
    st.sidebar.caption("AI-powered medication safety for Indian elderly patients")
    st.sidebar.divider()


def _init_session_state() -> None:
    """Initialise session-state keys used across pages."""
    defaults: dict = {
        "language": "en",
        "patient_name": "",
        "patient_age": 0,
        "patient_gender": "",
        "patient_weight_kg": 0.0,
        "patient_conditions": [],
        "systolic_bp": 0,
        "diastolic_bp": 0,
        "fasting_blood_sugar": 0.0,
        "postprandial_blood_sugar": 0.0,
        "spo2": 0,
        "heart_rate": 0,
        "serum_creatinine": 0.0,
        "allopathic_image": None,
        "ayurvedic_image": None,
        "allopathic_medicines": [],
        "ayurvedic_medicines": [],
        "unrecognized_medicines": [],
        "confirmed_medicines": [],
        "interactions": [],
        "safety_report": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    """Application entry point."""
    _configure_page()
    _apply_branding()
    _init_session_state()

    st.title("Welcome to SAHAYAK")
    st.markdown(
        """
        **SAHAYAK** checks your medications for potentially harmful interactions
        and provides safety guidance tailored for elderly patients in India.

        👈 Use the sidebar to navigate through each step.

        ### How it works
        1. **Choose your language**
        2. **Enter patient information**
        3. **Upload allopathic prescription** (photo / scan)
        4. **Upload ayurvedic / herbal medicines** (photo / scan)
        5. **Review unrecognised medicines**
        6. **Confirm medicine list**
        7. **View categorised interactions**
        8. **Download safety report**
        """
    )


if __name__ == "__main__":
    main()
