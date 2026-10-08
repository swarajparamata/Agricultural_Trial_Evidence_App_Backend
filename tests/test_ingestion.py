"""The bundled sample data is reconciled as the sample README describes."""

from tests.conftest import trials_by_id


def test_sample_data_is_reconciled(client, viewer):
    trials = trials_by_id(client, viewer)
    assert list(trials) == ["T01", "T02", "T03", "T04", "T05", "T06"]

    summary = {
        trial_id: (t["crop"], t["product"], t["country"], t["year"], t["trial_type"], t["treatment_yield"], t["control_yield"], t["uplift_pct"], t["status"])
        for trial_id, t in trials.items()
    }  # fmt: skip
    assert summary == {
        "T01": ("Wheat", "Harvest Plus", "France", 2023, "Scientific", 8.4, 8.0, 5.0, "consistent"),
        "T02": ("Wheat", "Harvest Plus", "Germany", 2024, "Demonstration", 7.8, 7.5, 4.0, "consistent"),
        "T03": ("Potato", "Root Boost", "Spain", 2023, "Demonstration", 42.0, 40.0, 5.0, "conflict"),
        "T04": ("Potato", "Root Boost", "France", 2024, "Scientific", 45.0, 45.0, 0.0, "consistent"),
        "T05": ("Wheat", "Harvest Plus", "Germany", 2025, "Demonstration", 8.1, None, None, "incomplete"),
        "T06": ("Wheat", "Harvest Plus", "Spain", 2021, "Scientific", 6.6, 6.0, 10.0, "single_source"),
    }


def test_conflict_keeps_both_values_and_the_uplift_range(client, viewer):
    t03 = trials_by_id(client, viewer)["T03"]
    conflict = t03["conflicts"][0]
    assert conflict["field"] == "treatment_yield" and conflict["status"] == "open"
    assert [(c["display"], c["sources"]) for c in conflict["candidates"]] == [
        ("42 t/ha", ["trials_a.csv"]),
        ("44 t/ha", ["T03_summary.txt"]),
    ]
    assert t03["uplift_range"] == [5.0, 10.0]
    assert t03["caveats"] == ["Conflicting treated yield between sources"]


def test_provenance_keeps_values_as_written(client, viewer):
    sources = {s["file"]: s for s in trials_by_id(client, viewer)["T02"]["sources"]}
    assert sources["trials_a.csv"]["values"]["product"] == "HARVESTPLUS"
    assert sources["trials_a.csv"]["values"]["treatment_yield"] == "7800 kg/ha"
    assert sources["T02_field_report.txt"]["values"]["treatment_yield"] == "7.8 t/ha"
    assert sources["trials_a.csv"]["locator"] == "row 3"


def test_caveats_from_notes_and_structure(client, viewer):
    trials = trials_by_id(client, viewer)
    assert trials["T02"]["caveats"] == [
        "Replication not documented",
        "Statistical analysis not documented",
        "Single demonstration site",
    ]
    assert trials["T05"]["missing_fields"] == ["control_yield"]
    assert trials["T05"]["caveats"] == ["Control yield missing – uplift cannot be calculated", "Reported by a single source"]
    assert trials["T06"]["notes"][0]["source"] == "T06_archive_note.txt"


def test_source_files_are_listed_and_readable(client, admin, viewer):
    files = client.get("/api/data/files", headers=admin).json()
    assert sorted(f["filename"] for f in files) == [
        "T02_field_report.txt", "T03_summary.txt", "T04_scientific_report.txt", "T06_archive_note.txt",
        "trials_a.csv", "trials_b.csv",
    ]  # fmt: skip
    trials_b = next(f for f in files if f["filename"] == "trials_b.csv")
    assert trials_b["trial_ids"] == ["T01", "T04", "T05"]
    content = client.get("/api/sources/T03_summary.txt", headers=viewer).json()["content"]
    assert "Neither source indicates which result is final" in content


CSV_SEMICOLON = """Trial reference;Crop name;Product name;Country code;Season year;Trial category;Treated yield (t/ha);Untreated yield (t/ha);Irrigation
T07;maize;HarvestPlus;IT;2025;Scientific;9,6;9,1;drip
"""


def test_import_csv_with_new_layout(client, admin):
    response = client.post(
        "/api/data/import", headers=admin, files=[("files", ("partner_2025.csv", CSV_SEMICOLON.encode(), "text/csv"))]
    )
    assert response.status_code == 200, response.text
    result = response.json()["files"][0]
    assert result["created"] == ["T07"]
    assert any("Irrigation" in warning for warning in result["warnings"])
    t07 = trials_by_id(client, admin)["T07"]
    assert (t07["crop"], t07["product"], t07["country"], t07["treatment_yield"], t07["control_yield"]) == (
        "Maize", "Harvest Plus", "Italy", 9.6, 9.1,
    )  # fmt: skip
    assert t07["uplift_pct"] == 5.5


def test_dry_run_saves_nothing(client, admin):
    response = client.post(
        "/api/data/import",
        headers=admin,
        files=[("files", ("partner_2025.csv", CSV_SEMICOLON.encode(), "text/csv"))],
        data={"dry_run": "true"},
    )
    assert response.json()["dry_run"] is True
    assert response.json()["files"][0]["created"] == ["T07"]
    assert "T07" not in trials_by_id(client, admin)


def test_reimport_replaces_the_earlier_file(client, admin):
    fixed = "Trial T03 summary\nResults\nTreated yield: 42 t/ha. Control yield: 40 t/ha.\n"
    client.post("/api/data/import", headers=admin, files=[("files", ("T03_summary.txt", fixed.encode(), "text/plain"))])
    t03 = trials_by_id(client, admin)["T03"]
    assert t03["status"] == "consistent"
    assert t03["conflicts"] == []


def test_removing_a_file_rebuilds_its_trials(client, admin):
    file_id = next(f["id"] for f in client.get("/api/data/files", headers=admin).json() if f["filename"] == "trials_b.csv")
    response = client.delete(f"/api/data/files/{file_id}", headers=admin)
    assert response.json()["affected_trials"] == ["T01", "T04", "T05"]
    trials = trials_by_id(client, admin)
    assert "T05" not in trials
    assert trials["T01"]["status"] == "single_source"
    assert trials["T04"]["sources"][0]["file"] == "T04_scientific_report.txt"


def test_unsupported_and_empty_files(client, admin):
    response = client.post(
        "/api/data/import",
        headers=admin,
        files=[("files", ("photo.png", b"\x89PNG", "image/png")), ("files", ("empty.csv", b"", "text/csv"))],
    )
    results = response.json()["files"]
    assert results[0]["error"].startswith("Unsupported file type")
    assert results[1]["error"] == "No trial records were found in this file."


def test_unreadable_report_uses_the_llm_extraction(client, admin, fake_llm):
    fake_llm.extraction = {
        "trial_id": "T08", "crop": "Wheat", "product": "HarvestPlus", "country": "Poland", "year": 2025,
        "trial_type": "Scientific", "treatment_yield": 6.9, "control_yield": 6.5, "yield_unit": "t/ha", "notes": None,
    }  # fmt: skip
    text = "In 2025 the Polish team compared HarvestPlus-treated wheat (6.9 t/ha) with untreated plots (6.5 t/ha), trial T08."
    response = client.post("/api/data/import", headers=admin, files=[("files", ("pl_note.txt", text.encode(), "text/plain"))])
    result = response.json()["files"][0]
    assert result["parser"] == "report-llm"
    assert result["llm_extraction"]["ok"] is True
    t08 = trials_by_id(client, admin)["T08"]
    assert (t08["country"], t08["treatment_yield"], t08["uplift_pct"]) == ("Poland", 6.9, 6.2)
    assert "Some values were extracted by AI from free text" in t08["caveats"]
    assert fake_llm.requests[-1][0] == "/v1/extract/report"


def test_rebuild_reproduces_the_same_trials(client, admin):
    before = trials_by_id(client, admin)
    response = client.post("/api/data/rebuild", headers=admin)
    assert response.json() == {"files": 6, "trials": 6, "conflicts": 1, "warnings": []}
    after = trials_by_id(client, admin)
    assert {k: v["status"] for k, v in before.items()} == {k: v["status"] for k, v in after.items()}
