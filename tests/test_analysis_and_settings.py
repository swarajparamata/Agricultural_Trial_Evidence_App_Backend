from tests.conftest import trials_by_id


def test_compare_returns_rows_charts_and_warnings(client, viewer):
    body = client.post("/api/compare", headers=viewer, json={"trial_ids": ["T01", "t04", "T05"]}).json()
    rows = {row["key"]: row for row in body["rows"]}
    assert rows["crop"]["values"] == {"T01": "Wheat", "T04": "Potato", "T05": "Wheat"}
    assert rows["crop"]["differs"] is True
    assert rows["uplift_pct"]["values"]["T01"] == "+5.0%"
    assert rows["custom:soil_type"]["kind"] == "custom"

    charts = {chart["id"]: chart for chart in body["charts"]}
    assert charts["yield"]["categories"] == ["T01", "T04", "T05"]
    assert charts["yield"]["series"][1]["values"] == [8.0, 45.0, None]
    assert charts["uplift"]["series"][0]["values"] == [5.0, 0.0, None]
    assert "custom:application_rate" not in charts  # no trial has a value yet

    assert body["summary"]["best"] == {"trial_ids": ["T01"], "uplift_pct": 5.0}
    assert body["summary"]["worst"] == {"trial_ids": ["T04"], "uplift_pct": 0.0}
    assert body["summary"]["mean_uplift_pct"] == 2.5
    assert any("different crops" in warning for warning in body["warnings"])
    assert any(warning.startswith("T05: control yield missing") for warning in body["warnings"])


def test_compare_lists_every_trial_tied_for_the_highest_uplift(client, viewer):
    # T01 (8.4 vs 8 t/ha) and T03 (42 vs 40 t/ha) both show +5.0%.
    summary = client.post("/api/compare", headers=viewer, json={"trial_ids": ["T01", "T03", "T04"]}).json()["summary"]
    assert summary["best"] == {"trial_ids": ["T01", "T03"], "uplift_pct": 5.0}
    assert summary["worst"] == {"trial_ids": ["T04"], "uplift_pct": 0.0}


def test_compare_limits(client, viewer):
    assert client.post("/api/compare", headers=viewer, json={"trial_ids": ["T98", "T99"]}).status_code == 404
    partial = client.post("/api/compare", headers=viewer, json={"trial_ids": ["T01", "T99"]}).json()
    assert partial["not_found"] == ["T99"]
    too_many = client.post("/api/compare", headers=viewer, json={"trial_ids": [f"T0{i}" for i in range(1, 8)]})
    assert too_many.status_code == 400


def test_analytics_overview(client, viewer):
    body = client.get("/api/analytics/overview", headers=viewer).json()
    assert body["kpis"]["trials"] == 6
    assert body["kpis"]["mean_uplift_pct"] == 4.8  # (5 + 4 + 5 + 0 + 10) / 5
    assert body["kpis"]["open_conflicts"] == 1
    products = {group["name"]: group for group in body["by_product"]}
    assert products["Harvest Plus"]["mean_uplift_pct"] == 6.3
    assert products["Harvest Plus"]["trials_with_uplift"] == 3
    filtered = client.get("/api/analytics/overview?crop=potatoes", headers=viewer).json()
    assert filtered["kpis"]["trials"] == 2


def test_display_unit_changes_the_output(client, admin):
    display = client.get("/api/settings", headers=admin).json()["display"]
    response = client.put("/api/settings/display", headers=admin, json={**display, "yield_unit": "kg/ha"})
    assert response.status_code == 200
    assert response.json()["rebuild"] is None
    t01 = trials_by_id(client, admin)["T01"]
    assert (t01["treatment_yield"], t01["yield_unit"], t01["uplift_pct"]) == (8400.0, "kg/ha", 5.0)


def test_unknown_display_unit_is_rejected(client, admin):
    display = client.get("/api/settings", headers=admin).json()["display"]
    response = client.put("/api/settings/display", headers=admin, json={**display, "yield_unit": "bu/ac"})
    assert response.status_code == 422
    assert "not defined in the reference units" in str(response.json()["detail"])


def test_tolerance_setting_decides_what_counts_as_a_conflict(client, admin):
    reconciliation = client.get("/api/settings", headers=admin).json()["reconciliation"]
    response = client.put("/api/settings/reconciliation", headers=admin, json={**reconciliation, "numeric_tolerance_pct": 5})
    assert response.json()["rebuild"]["conflicts"] == 0  # 42 vs 44 t/ha differ by 4.5%
    assert trials_by_id(client, admin)["T03"]["status"] == "consistent"


def test_manual_conflict_strategy_leaves_the_value_open(client, admin):
    reconciliation = client.get("/api/settings", headers=admin).json()["reconciliation"]
    client.put("/api/settings/reconciliation", headers=admin, json={**reconciliation, "conflict_strategy": "manual"})
    t03 = trials_by_id(client, admin)["T03"]
    assert t03["treatment_yield"] is None
    assert t03["uplift_pct"] is None
    assert t03["uplift_range"] == [5.0, 10.0]


def test_new_alias_is_applied_to_imported_files(client, admin):
    csv = "Trial reference,Crop name,Product name,Country code,Season year,Trial category,Treated yield kg per ha,Untreated yield kg per ha\nT09,wheat,HP-2000,FR,2025,scientific,8000,7600\n"
    client.post("/api/data/import", headers=admin, files=[("files", ("hp2000.csv", csv.encode(), "text/csv"))])
    assert trials_by_id(client, admin)["T09"]["product"] == "Hp-2000"

    reference = client.get("/api/settings", headers=admin).json()["reference_data"]
    for product in reference["products"]:
        if product["name"] == "Harvest Plus":
            product["aliases"].append("HP-2000")
    response = client.put("/api/settings/reference_data", headers=admin, json=reference)
    assert response.json()["rebuild"]["files"] == 7
    assert trials_by_id(client, admin)["T09"]["product"] == "Harvest Plus"


def test_custom_field_is_read_from_a_csv_column(client, admin):
    fields = client.get("/api/settings", headers=admin).json()["custom_fields"]["fields"]
    for field in fields:
        if field["key"] == "application_rate":
            field["source_aliases"] = ["Dose L/ha"]
    client.put("/api/settings/custom_fields", headers=admin, json={"fields": fields})
    csv = "trial_id,crop,product,country,year,trial_type,treatment_yield,control_yield,yield_unit,Dose L/ha\nT11,Wheat,Root Boost,Spain,2025,Scientific,6.2,6.0,t/ha,1.5\n"
    client.post("/api/data/import", headers=admin, files=[("files", ("doses.csv", csv.encode(), "text/csv"))])
    assert trials_by_id(client, admin)["T11"]["custom_fields"] == {"application_rate": 1.5}
    charts = client.post("/api/compare", headers=admin, json={"trial_ids": ["T01", "T11"]}).json()["charts"]
    assert any(chart["id"] == "custom:application_rate" for chart in charts)


def test_invalid_custom_fields_are_rejected(client, admin):
    response = client.put(
        "/api/settings/custom_fields", headers=admin,
        json={"fields": [{"key": "crop", "label": "Crop again"}, {"key": "x", "label": "X", "type": "select"}]},
    )  # fmt: skip
    assert response.status_code == 422


def test_reset_restores_defaults(client, admin):
    client.put("/api/settings/assistant", headers=admin, json={"model": "qwen3.6:27b", "max_revisions": 4})
    assert client.get("/api/settings", headers=admin).json()["assistant"]["model"] == "qwen3.6:27b"
    client.post("/api/settings/assistant/reset", headers=admin)
    assistant = client.get("/api/settings", headers=admin).json()["assistant"]
    assert (assistant["model"], assistant["max_revisions"]) == ("", 2)
