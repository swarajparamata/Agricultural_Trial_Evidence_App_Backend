from tests.conftest import trials_by_id

NEW_TRIAL = {
    "id": "t10",
    "crop": "wheat",
    "product": "HARVESTPLUS",
    "country": "FR",
    "year": 2026,
    "trial_type": "research",
    "treatment_yield": 8200,
    "control_yield": 7900,
    "yield_unit": "kg/ha",
    "notes": "Two sites, four replicates each.",
    "custom_fields": {"soil_type": "loam", "application_rate": "2.5"},
}


def test_admin_adds_a_trial_with_custom_fields(client, admin):
    response = client.post("/api/trials", headers=admin, json=NEW_TRIAL)
    assert response.status_code == 201, response.text
    trial = response.json()
    assert (trial["id"], trial["crop"], trial["product"], trial["country"], trial["trial_type"]) == (
        "T10", "Wheat", "Harvest Plus", "France", "Scientific",
    )  # fmt: skip
    assert (trial["treatment_yield"], trial["control_yield"], trial["uplift_pct"]) == (8.2, 7.9, 3.8)
    assert trial["custom_fields"] == {"soil_type": "Loam", "application_rate": 2.5}
    assert trial["status"] == "manual"
    assert trial["origin"] == "manual"
    assert "Entered manually, no source document" in trial["caveats"]


def test_duplicate_ids_and_invalid_custom_values_are_rejected(client, admin):
    assert client.post("/api/trials", headers=admin, json={**NEW_TRIAL, "id": "T01"}).status_code == 409
    bad = client.post("/api/trials", headers=admin, json={**NEW_TRIAL, "custom_fields": {"soil_type": "Gravel", "colour": "red"}})
    assert bad.status_code == 422
    problems = {tuple(error["loc"]): error["msg"] for error in bad.json()["detail"]}
    assert problems[("body", "custom_fields", "soil_type")] == "Soil type must be one of: Clay, Loam, Sandy, Silt"
    assert problems[("body", "custom_fields", "colour")] == "Unknown custom field"
    assert client.post("/api/trials", headers=admin, json={**NEW_TRIAL, "yield_unit": "bushels"}).status_code == 422


def test_strict_vocabulary(client, admin):
    reference = client.get("/api/settings", headers=admin).json()["reference_data"]
    client.put("/api/settings/reference_data", headers=admin, json={**reference, "strict_vocabulary": True})
    response = client.post("/api/trials", headers=admin, json={**NEW_TRIAL, "crop": "Sorghum"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"].startswith("Unknown crop. Use one of: Wheat, Potato")


def test_editing_an_imported_trial_stores_an_override(client, admin):
    response = client.patch("/api/trials/T01", headers=admin, json={"control_yield": 7.9, "reason": "Corrected from lab sheet"})
    trial = response.json()
    assert trial["control_yield"] == 7.9
    assert trial["overridden_fields"] == ["control_yield"]
    detail = client.get("/api/trials/T01", headers=admin).json()
    assert detail["overrides"][0]["note"] == "Corrected from lab sheet"
    assert {record["file"] for record in detail["records"]} == {"trials_a.csv", "trials_b.csv"}

    reverted = client.delete("/api/trials/T01/overrides/control_yield", headers=admin).json()
    assert reverted["control_yield"] == 8.0
    assert reverted["overridden_fields"] == []


def test_editing_a_manual_trial_changes_its_entry(client, admin):
    client.post("/api/trials", headers=admin, json=NEW_TRIAL)
    trial = client.patch("/api/trials/T10", headers=admin, json={"treatment_yield": 8.4, "custom_fields": {"soil_type": "Clay"}}).json()
    assert trial["treatment_yield"] == 8.4
    assert trial["custom_fields"]["soil_type"] == "Clay"
    assert trial["overridden_fields"] == []


def test_resolving_a_conflict(client, admin, viewer):
    response = client.post(
        "/api/trials/T03/conflicts/treatment_yield/resolve", headers=admin, json={"value": 44, "note": "Report is the final version"}
    )
    trial = response.json()
    assert trial["treatment_yield"] == 44.0
    assert trial["uplift_pct"] == 10.0
    assert trial["status"] == "consistent"
    assert trial["uplift_range"] is None
    conflict = trial["conflicts"][0]
    assert (conflict["status"], conflict["resolved_by"], conflict["note"]) == ("resolved", "admin@agrievidence.demo", "Report is the final version")
    assert "Treated yield set by an admin to resolve conflicting sources" in trial["caveats"]
    audit = client.get("/api/audit", headers=admin).json()
    assert audit[0]["action"] == "trial.resolve_conflict"
    assert client.post("/api/trials/T01/conflicts/crop/resolve", headers=admin, json={"value": "Wheat"}).status_code == 404


def test_deleted_trials_stay_deleted_until_restored(client, admin):
    assert client.delete("/api/trials/T06", headers=admin).status_code == 204
    assert "T06" not in trials_by_id(client, admin)
    client.post("/api/data/rebuild", headers=admin)
    assert "T06" not in trials_by_id(client, admin)
    assert [item["id"] for item in client.get("/api/trials/deleted", headers=admin).json()] == ["T06"]
    restored = client.post("/api/trials/T06/restore", headers=admin)
    assert restored.status_code == 200
    assert "T06" in trials_by_id(client, admin)


def test_filters_accept_spelling_variants_and_custom_fields(client, admin, viewer):
    def ids(query: str) -> list[str]:
        return [trial["id"] for trial in client.get(f"/api/trials?{query}", headers=viewer).json()["items"]]

    assert ids("product=Harvest%20Plus&country=Germany") == ["T02", "T05"]
    assert ids("status=conflict") == ["T03"]
    assert ids("q=archive") == ["T06"]
    client.post("/api/trials", headers=admin, json=NEW_TRIAL)
    assert ids("cf_soil_type=loam") == ["T10"]


def test_unknown_trial(client, viewer):
    assert client.get("/api/trials/T99", headers=viewer).status_code == 404
