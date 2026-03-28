"""Complete Neo4j graph audit for Sahayak medical application."""

from neo4j import GraphDatabase

import os
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "")

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

queries = [
    # === SECTION 1: NODE COUNTS AND COMPLETENESS ===
    ("1. Node counts by label", """
        CALL db.labels() YIELD label
        MATCH (n) WHERE label IN labels(n)
        RETURN label, count(n) AS count ORDER BY count DESC
    """),
    ("2. Labels with spaces (should be 0)", """
        CALL db.labels() YIELD label WHERE label CONTAINS ' ' RETURN label
    """),
    ("3. Drug node completeness", """
        MATCH (d:Drug) RETURN
        count(d) AS total_drugs,
        count(d.generic_name) AS has_generic_name,
        count(d.rxcui) AS has_rxcui,
        count(d.drug_class) AS has_drug_class,
        count(d.is_beers) AS has_beers_flag,
        count(d.anticholinergic_score) AS has_acb_score,
        count(d.ndc_code) AS has_ndc,
        count(d.renal_dose_adjust) AS has_renal_adjust,
        count(d.geriatric_dose_adjust) AS has_geriatric_adjust
    """),
    ("4. Herb node completeness", """
        MATCH (h:Herb) RETURN
        count(h) AS total_herbs,
        count(h.hindi_name) AS has_hindi,
        count(h.tamil_name) AS has_tamil,
        count(h.telugu_name) AS has_telugu,
        count(h.kannada_name) AS has_kannada,
        count(h.scientific_name) AS has_scientific,
        count(h.category) AS has_category
    """),

    # === SECTION 2: RELATIONSHIP COUNTS BY SOURCE ===
    ("5. INTERACTS_WITH by source", """
        MATCH ()-[r:INTERACTS_WITH]->()
        RETURN r.source AS source, count(r) AS count
        ORDER BY count DESC
    """),
    ("6. INTERACTS_WITH_DRUG by source", """
        MATCH ()-[r:INTERACTS_WITH_DRUG]->()
        RETURN r.source AS source, count(r) AS count
        ORDER BY count DESC
    """),
    ("7. MAY_CAUSE by source", """
        MATCH ()-[r:MAY_CAUSE]->()
        RETURN r.source AS source, count(r) AS count
        ORDER BY count DESC
    """),
    ("8. COPRESCRIPTION_EFFECT count", """
        MATCH ()-[r:COPRESCRIPTION_EFFECT]->() RETURN count(r) AS count
    """),
    ("9. CONTAINS count", """
        MATCH ()-[r:CONTAINS]->() RETURN count(r) AS count
    """),
    ("10. CONTRAINDICATED_IN by source", """
        MATCH ()-[r:CONTRAINDICATED_IN]->()
        RETURN r.source AS source, count(r) AS count
    """),
    ("11. FLAGGED_BY count", """
        MATCH ()-[r:FLAGGED_BY]->() RETURN count(r) AS count
    """),
    ("12. All relationship types with counts", """
        CALL db.relationshipTypes() YIELD relationshipType
        MATCH ()-[r]->() WHERE type(r) = relationshipType
        RETURN relationshipType, count(r) AS count
        ORDER BY count DESC
    """),

    # === SECTION 3: DATA QUALITY — CRITICAL MEDICAL CHECKS ===
    ("13. Orphan drugs (no relationships)", """
        MATCH (d:Drug) WHERE NOT (d)--() RETURN count(d) AS orphan_drugs
    """),
    ("14. DDInter severity distribution", """
        MATCH ()-[r:INTERACTS_WITH {source:"ddinter"}]->()
        RETURN r.severity AS severity, count(r) AS count ORDER BY count DESC
    """),
    ("15. DDID severity distribution", """
        MATCH ()-[r:INTERACTS_WITH_DRUG {source:"ddid"}]->()
        RETURN r.severity AS severity, count(r) AS count ORDER BY count DESC
    """),
    ("16. Beers list drug coverage", """
        UNWIND ["diphenhydramine","hydroxyzine","amitriptyline","diazepam",
        "chlordiazepoxide","alprazolam","glimepiride","nifedipine",
        "doxazosin","methyldopa","megestrol","nitrofurantoin"] AS drug_name
        MATCH (d:Drug) WHERE toLower(d.generic_name) = drug_name
        RETURN drug_name, d.is_beers, d.beers_category, d.anticholinergic_score
    """),
    ("17. Warfarin + Aspirin interaction check", """
        MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
        WHERE toLower(a.generic_name) CONTAINS "warfarin"
        AND toLower(b.generic_name) CONTAINS "aspirin"
        RETURN a.generic_name, b.generic_name, r.severity, r.source LIMIT 5
    """),
    ("18. Metformin herb/food interactions", """
        MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
        WHERE toLower(d.generic_name) CONTAINS "metformin"
        RETURN h.name, d.generic_name, r.severity, r.mechanism LIMIT 10
    """),
    ("19. Ashwagandha herb-drug interactions", """
        MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
        WHERE toLower(h.name) CONTAINS "ashwagandha"
        OR toLower(h.name) CONTAINS "withania"
        RETURN h.name, d.generic_name, r.severity, r.mechanism LIMIT 10
    """),
    ("20. Ecosprin → Aspirin interaction chain", """
        MATCH (b:IndianBrand)-[:CONTAINS]->(d:Drug)-[r:INTERACTS_WITH]-(d2:Drug)
        WHERE toLower(b.brand_name) CONTAINS "ecosprin"
        RETURN b.brand_name, d.generic_name, d2.generic_name, r.severity
        LIMIT 10
    """),
    ("21. Thyronorm → Levothyroxine interaction chain", """
        MATCH (b:IndianBrand)-[:CONTAINS]->(d:Drug)-[r:INTERACTS_WITH]-(d2:Drug)
        WHERE toLower(b.brand_name) CONTAINS "thyronorm"
        RETURN b.brand_name, d.generic_name, d2.generic_name, r.severity
        LIMIT 10
    """),
    ("22. Metformin side effects with frequency", """
        MATCH (d:Drug)-[r:MAY_CAUSE]->(s:SideEffect)
        WHERE r.frequency IS NOT NULL AND toLower(d.generic_name) = "metformin"
        RETURN d.generic_name, s.name, r.frequency, r.source LIMIT 10
    """),
    ("23. TwoSIDES aspirin coprescription effects", """
        MATCH (a:Drug)-[r:COPRESCRIPTION_EFFECT]->(b:Drug)
        WHERE toLower(a.generic_name) CONTAINS "aspirin"
        RETURN a.generic_name, b.generic_name, r.top_events, r.event_count
        LIMIT 5
    """),

    # === SECTION 4: CROSS-SOURCE INTEGRATION CHECK ===
    ("24. Drugs appearing in ALL major sources", """
        MATCH (d:Drug)
        WHERE (d)-[:INTERACTS_WITH {source:"ddinter"}]-()
        AND (d)-[:INTERACTS_WITH {source:"primekg"}]-()
        AND (d)-[:MAY_CAUSE {source:"sider"}]-()
        AND (d)-[:MAY_CAUSE {source:"onsides"}]-()
        AND (d)<-[:CONTAINS]-(:IndianBrand)
        RETURN d.generic_name, d.is_beers, d.anticholinergic_score,
        d.drug_class LIMIT 10
    """),
    ("25. Full connectivity test — Metformin", """
        MATCH (d:Drug) WHERE toLower(d.generic_name) = "metformin"
        OPTIONAL MATCH (d)-[ddi:INTERACTS_WITH]-(d2:Drug)
        OPTIONAL MATCH (d)-[mc:MAY_CAUSE]->(se:SideEffect)
        OPTIONAL MATCH (d)<-[c:CONTAINS]-(ib:IndianBrand)
        OPTIONAL MATCH (d)-[ci:CONTRAINDICATED_IN]->(cond:Condition)
        OPTIONAL MATCH (h:Herb)-[hdi:INTERACTS_WITH_DRUG]->(d)
        RETURN d.generic_name,
        count(DISTINCT ddi) AS drug_interactions,
        count(DISTINCT mc) AS side_effects,
        count(DISTINCT ib) AS indian_brands,
        count(DISTINCT ci) AS contraindications,
        count(DISTINCT hdi) AS herb_interactions
    """),

    # === SECTION 5: INDEX CHECK ===
    ("26. Show indexes", "SHOW INDEXES"),
]

with driver.session() as session:
    for title, query in queries:
        print(f"\n{'='*70}")
        print(f"  {title}")
        print(f"{'='*70}")
        try:
            result = session.run(query)
            records = list(result)
            if not records:
                print("  (no rows returned)")
            else:
                keys = records[0].keys()
                # Print header
                print("  " + " | ".join(str(k) for k in keys))
                print("  " + "-" * 60)
                for rec in records:
                    vals = []
                    for k in keys:
                        v = rec[k]
                        if isinstance(v, list):
                            v = str(v)[:80]
                        vals.append(str(v))
                    print("  " + " | ".join(vals))
        except Exception as e:
            print(f"  ERROR: {e}")

driver.close()
print("\n\nAUDIT COMPLETE.")
