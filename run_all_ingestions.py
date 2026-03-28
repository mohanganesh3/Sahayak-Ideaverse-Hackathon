#!/usr/bin/env python3
"""Master runner: execute ALL Neo4j ingestion scripts in order with verification.

Usage (inside Docker):
    docker compose exec sahayak-app python run_all_ingestions.py

Usage (local):
    cd ~/IDEAVERSE/sahayak
    python3 run_all_ingestions.py

Requires Neo4j running (reads NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD from env).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from neo4j import GraphDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("run_all_ingestions")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# DATA_DIR: external datasets (sahayak-data), APP_DATA: curated JSONs inside the app
DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "sahayak-data")))
APP_DATA = Path(__file__).resolve().parent / "app" / "data"

TOTAL_STEPS = 12


def get_driver():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    return driver


def _step(num: int, name: str):
    LOGGER.info("=" * 60)
    LOGGER.info("STEP %d/%d: %s", num, TOTAL_STEPS, name)


# ── Individual ingest runners ─────────────────────────────────────────────────

def run_schema():
    _step(0, "Creating/updating schema...")
    from app.graph.schema import create_schema
    driver = get_driver()
    try:
        create_schema(driver)
        LOGGER.info("Schema created/updated.")
    finally:
        driver.close()


def run_beers():
    _step(1, "Ingesting Beers Criteria...")
    from app.graph.ingest_beers import ingest
    driver = get_driver()
    try:
        result = ingest(driver, APP_DATA / "beers_criteria.json", database=NEO4J_DATABASE)
        LOGGER.info("Beers result: %s", result)
    finally:
        driver.close()


def run_herbs():
    _step(2, "Ingesting Ayurvedic Herbs...")
    from app.graph.ingest_herbs import ingest_herb_database
    driver = get_driver()
    try:
        result = ingest_herb_database(driver, APP_DATA / "ayurvedic_herbs.json", database=NEO4J_DATABASE)
        LOGGER.info("Herbs result: %s", result)
    finally:
        driver.close()


def run_ddid():
    _step(3, "Ingesting DDID (Drug-Drug, Herb, Food interactions)...")
    from app.graph.ingest_ddid import ingest
    driver = get_driver()
    try:
        result = ingest(driver, DATA_DIR / "ddid", database=NEO4J_DATABASE)
        LOGGER.info("DDID result: %s", result)
    finally:
        driver.close()


def run_ddinter():
    _step(4, "Ingesting DDInter...")
    from app.graph.ingest_ddinter import ingest
    driver = get_driver()
    try:
        result = ingest(driver, DATA_DIR / "ddinter", database=NEO4J_DATABASE)
        LOGGER.info("DDInter result: %s", result)
    finally:
        driver.close()


def run_hetionet():
    _step(5, "Ingesting Hetionet...")
    from app.graph.ingest_hetionet import ingest
    driver = get_driver()
    try:
        result = ingest(
            driver,
            DATA_DIR / "hetionet" / "hetionet-v1.0.json",
            database=NEO4J_DATABASE,
        )
        LOGGER.info("Hetionet result: %s", result)
    finally:
        driver.close()


def run_primekg():
    _step(6, "Ingesting PrimeKG...")
    from app.graph.ingest_primekg import ingest
    driver = get_driver()
    try:
        result = ingest(driver, DATA_DIR / "primekg", database=NEO4J_DATABASE)
        LOGGER.info("PrimeKG result: %s", result)
    finally:
        driver.close()


def run_indian_brands():
    _step(7, "Ingesting Indian Brands...")
    from app.graph.ingest_indian_brands import ingest
    driver = get_driver()
    try:
        result = ingest(
            driver,
            DATA_DIR / "indian-meds" / "DATA",
            database=NEO4J_DATABASE,
        )
        LOGGER.info("Indian Brands result: %s", result)
    finally:
        driver.close()


def run_twosides():
    _step(8, "Ingesting TWOSIDES...")
    from app.graph.ingest_twosides import ingest
    driver = get_driver()
    try:
        result = ingest(
            driver,
            DATA_DIR / "twosides" / "TWOSIDES.csv",
            database=NEO4J_DATABASE,
        )
        LOGGER.info("TWOSIDES result: %s", result)
    finally:
        driver.close()


def run_sider():
    _step(9, "Ingesting SIDER...")
    from app.graph.ingest_sider import ingest
    driver = get_driver()
    try:
        result = ingest(driver, DATA_DIR / "sider", database=NEO4J_DATABASE)
        LOGGER.info("SIDER result: %s", result)
    finally:
        driver.close()


def run_fda_ndc():
    _step(10, "Ingesting FDA-NDC...")
    from app.graph.ingest_fda_ndc import ingest
    driver = get_driver()
    try:
        result = ingest(
            driver,
            DATA_DIR / "fda-ndc" / "drug-ndc-0001-of-0001.json",
            database=NEO4J_DATABASE,
        )
        LOGGER.info("FDA-NDC result: %s", result)
    finally:
        driver.close()


def run_cyp450():
    _step(11, "Ingesting CYP450 / Transporter data...")
    from app.graph.ingest_cyp450 import ingest
    ingest(
        data_path=APP_DATA / "cyp450_data.json",
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
        batch_size=500,
    )
    LOGGER.info("CYP450 ingestion complete.")


def run_onsides():
    _step(12, "Ingesting OnSIDES (large dataset — may take a while)...")
    from app.graph.ingest_onsides import ingest
    driver = get_driver()
    try:
        result = ingest(driver, DATA_DIR / "onsides", database=NEO4J_DATABASE)
        LOGGER.info("OnSIDES result: %s", result)
    finally:
        driver.close()


# ── Verification ──────────────────────────────────────────────────────────────

def run_verification():
    LOGGER.info("=" * 60)
    LOGGER.info("VERIFICATION QUERIES")
    LOGGER.info("=" * 60)

    driver = get_driver()
    try:
        with driver.session(database=NEO4J_DATABASE) as s:
            queries = [
                ("Drug nodes",
                 "MATCH (d:Drug) RETURN count(d) AS cnt"),
                ("Herb nodes",
                 "MATCH (h:Herb) RETURN count(h) AS cnt"),
                ("IndianBrand nodes",
                 "MATCH (b:IndianBrand) RETURN count(b) AS cnt"),
                ("Condition/Disease nodes",
                 "MATCH (c:Condition) RETURN count(c) AS cnt"),
                ("SideEffect nodes",
                 "MATCH (s:SideEffect) RETURN count(s) AS cnt"),
                ("Enzyme nodes",
                 "MATCH (e:Enzyme) RETURN count(e) AS cnt"),
                ("INTERACTS_WITH edges",
                 "MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r) AS cnt"),
                ("COPRESCRIPTION_EFFECT edges (TWOSIDES)",
                 "MATCH ()-[r:COPRESCRIPTION_EFFECT]->() RETURN count(r) AS cnt"),
                ("MAY_CAUSE edges",
                 "MATCH ()-[r:MAY_CAUSE]->() RETURN count(r) AS cnt"),
                ("HERB_DRUG_INTERACTION edges",
                 "MATCH ()-[r:HERB_DRUG_INTERACTION]->() RETURN count(r) AS cnt"),
                ("FDA-enriched Drug nodes (ndc_code)",
                 "MATCH (d:Drug) WHERE d.ndc_code IS NOT NULL RETURN count(d) AS cnt"),
                ("Beers nodes (is_beers = true)",
                 "MATCH (d:Drug) WHERE d.is_beers = true RETURN count(d) AS cnt"),
            ]

            all_pass = True
            for label, query in queries:
                result = s.run(query).single()
                count = result["cnt"]
                status = "PASS" if count > 0 else "FAIL"
                if count == 0:
                    all_pass = False
                LOGGER.info("  [%s] %s: %s", status, label, f"{count:,}")

            # Total node and relationship counts
            total_nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            total_rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            LOGGER.info("  --- TOTALS ---")
            LOGGER.info("  Total nodes: %s", f"{total_nodes:,}")
            LOGGER.info("  Total relationships: %s", f"{total_rels:,}")

            # Label breakdown
            result = s.run(
                "CALL db.labels() YIELD label "
                "CALL { WITH label MATCH (n) WHERE label IN labels(n) RETURN count(n) AS cnt } "
                "RETURN label, cnt ORDER BY cnt DESC"
            )
            LOGGER.info("  --- LABEL BREAKDOWN ---")
            for rec in result:
                LOGGER.info("    :%s  %s", rec["label"], f"{rec['cnt']:,}")

            if all_pass:
                LOGGER.info("ALL VERIFICATION CHECKS PASSED!")
            else:
                LOGGER.warning("SOME VERIFICATION CHECKS FAILED — review output above.")

    finally:
        driver.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    start_time = time.time()

    LOGGER.info("Starting full ingestion pipeline (all 12 datasets)...")
    LOGGER.info("Neo4j: %s", NEO4J_URI)
    LOGGER.info("External data dir: %s", DATA_DIR)
    LOGGER.info("App data dir: %s", APP_DATA)

    try:
        run_schema()

        # Phase 1: Foundation datasets (reference data, small-medium)
        run_beers()
        run_herbs()
        run_ddid()
        run_ddinter()
        run_hetionet()
        run_primekg()
        run_indian_brands()

        # Phase 2: Drug-level enrichment
        run_twosides()
        run_sider()
        run_fda_ndc()
        run_cyp450()

        # Phase 3: Largest dataset last
        run_onsides()

        run_verification()
    except Exception as e:
        LOGGER.error("Ingestion failed: %s", e, exc_info=True)
        return 1

    elapsed = time.time() - start_time
    LOGGER.info("Total elapsed time: %.1f seconds (%.1f minutes)", elapsed, elapsed / 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
