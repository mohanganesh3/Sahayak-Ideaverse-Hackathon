"""Page 2 – Patient information collection."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Collect patient demographics and medical conditions."""
    st.header("Patient Information")

    name = st.text_input("Patient name", value=st.session_state.get("patient_name", ""))
    gender = st.selectbox(
        "Gender",
        options=["", "Female", "Male", "Other"],
        index=["", "Female", "Male", "Other"].index(st.session_state.get("patient_gender", "")),
    )
    age = st.number_input(
        "Age (years)", min_value=0, max_value=120,
        value=st.session_state.get("patient_age", 0),
    )
    weight_kg = st.number_input(
        "Weight (kg) – optional",
        min_value=0.0,
        max_value=250.0,
        step=0.5,
        value=float(st.session_state.get("patient_weight_kg", 0.0)),
    )
    conditions = st.multiselect(
        "Existing medical conditions",
        options=[
            "Diabetes", "Hypertension", "Heart Disease", "Kidney Disease",
            "Liver Disease", "Asthma / COPD", "Arthritis", "Thyroid Disorder",
            "Depression / Anxiety", "Parkinson's", "Alzheimer's / Dementia",
        ],
        default=st.session_state.get("patient_conditions", []),
    )

    st.subheader("Optional Home / Recent Clinical Values")
    col1, col2 = st.columns(2)
    with col1:
        systolic_bp = st.number_input(
            "Systolic BP (mmHg)",
            min_value=0,
            max_value=300,
            value=int(st.session_state.get("systolic_bp", 0)),
        )
        fasting_blood_sugar = st.number_input(
            "Fasting Blood Sugar (mg/dL)",
            min_value=0.0,
            max_value=1000.0,
            step=1.0,
            value=float(st.session_state.get("fasting_blood_sugar", 0.0)),
        )
        spo2 = st.number_input(
            "SpO2 (%)",
            min_value=0,
            max_value=100,
            value=int(st.session_state.get("spo2", 0)),
        )
        serum_creatinine = st.number_input(
            "Serum Creatinine (mg/dL)",
            min_value=0.0,
            max_value=20.0,
            step=0.1,
            value=float(st.session_state.get("serum_creatinine", 0.0)),
        )
    with col2:
        diastolic_bp = st.number_input(
            "Diastolic BP (mmHg)",
            min_value=0,
            max_value=200,
            value=int(st.session_state.get("diastolic_bp", 0)),
        )
        postprandial_blood_sugar = st.number_input(
            "Post-Meal Blood Sugar (mg/dL)",
            min_value=0.0,
            max_value=1000.0,
            step=1.0,
            value=float(st.session_state.get("postprandial_blood_sugar", 0.0)),
        )
        heart_rate = st.number_input(
            "Heart Rate (bpm)",
            min_value=0,
            max_value=250,
            value=int(st.session_state.get("heart_rate", 0)),
        )

    if st.button("Save & Continue →"):
        st.session_state["patient_name"] = name
        st.session_state["patient_gender"] = gender
        st.session_state["patient_age"] = age
        st.session_state["patient_weight_kg"] = weight_kg
        st.session_state["patient_conditions"] = conditions
        st.session_state["systolic_bp"] = systolic_bp
        st.session_state["diastolic_bp"] = diastolic_bp
        st.session_state["fasting_blood_sugar"] = fasting_blood_sugar
        st.session_state["postprandial_blood_sugar"] = postprandial_blood_sugar
        st.session_state["spo2"] = spo2
        st.session_state["heart_rate"] = heart_rate
        st.session_state["serum_creatinine"] = serum_creatinine
        st.success("Patient information saved.")


render()
