from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..database import get_db
from ..deps import get_config, get_current_user, get_llm_client, require_admin
from ..models import SourceFile, Trial, User
from ..schemas.misc import FileImportResult, ImportResponse, RebuildResponse, SourceFileOut
from ..services import audit
from ..services.assistant import extraction_payload
from ..services.ingestion import field_matcher, import_folder, import_parsed, rebuild_all, remove_file
from ..services.llm_client import LlmClient, LlmServiceError
from ..services.parsers import apply_llm_extraction, decode, file_kind, parse_file
from ..services.settings_store import load_settings
from ..text import natural_key

router = APIRouter(prefix="/data", tags=["data import (admin)"])
sources_router = APIRouter(prefix="/sources", tags=["sources"])


def _file_out(source: SourceFile) -> SourceFileOut:
    return SourceFileOut(
        id=source.id,
        filename=source.filename,
        kind=source.kind,
        parser=source.parser,
        size_bytes=source.size_bytes,
        record_count=source.record_count,
        trial_ids=sorted({record.trial_id for record in source.records}, key=natural_key),
        warnings=source.warnings,
        uploaded_at=source.uploaded_at,
        uploaded_by=source.uploaded_by,
    )


@router.post("/import", response_model=ImportResponse, summary="Import CSV files and trial reports")
async def import_files(
    files: list[UploadFile] = File(..., description=".csv/.tsv tables or .txt/.md reports"),
    dry_run: bool = Form(False, description="Parse and reconcile, report the outcome, but save nothing"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: LlmClient = Depends(get_llm_client),
    config: Settings = Depends(get_config),
) -> ImportResponse:
    """Re-importing a file with the same name replaces its earlier version. Reports the rule-based parser
    can't read are sent to the LLM service's extraction loop when that is switched on."""
    settings = load_settings(db)
    matcher = field_matcher(settings)
    results: list[FileImportResult] = []
    for upload in files:
        name = Path(upload.filename or "upload").name
        raw = await upload.read(config.upload_max_bytes + 1)
        if len(raw) > config.upload_max_bytes:
            results.append(FileImportResult(filename=name, error=f"The file is larger than {config.upload_max_bytes // 1_000_000} MB."))
            continue
        if file_kind(name) is None:
            results.append(FileImportResult(filename=name, error="Unsupported file type. Use .csv, .tsv, .txt or .md."))
            continue
        text = decode(raw)
        parsed = parse_file(name, text, matcher)

        llm_info = None
        if parsed.kind == "report" and parsed.missing and settings.ingestion.use_llm_extraction and settings.assistant.enabled:
            try:
                extraction = await client.extract_report(extraction_payload(name, text, settings))
                llm_info = {key: extraction.get(key) for key in ("ok", "mode", "model", "attempts", "issues")}
                if extraction.get("ok") and extraction.get("fields"):
                    parsed = apply_llm_extraction(parsed, name, extraction["fields"])
            except LlmServiceError as exc:
                llm_info = {"ok": False, "mode": "unavailable", "issues": [str(exc)]}

        if not parsed.records:
            results.append(
                FileImportResult(
                    filename=name, kind=parsed.kind, parser=parsed.parser, warnings=parsed.warnings,
                    error="No trial records were found in this file.", llm_extraction=llm_info,
                )
            )
            continue
        result = import_parsed(db, settings, name, text, len(raw), parsed, admin.email)
        if parsed.missing:
            result.warnings.append(f"Not found in the report: {', '.join(parsed.missing)}.")
        result.llm_extraction = llm_info
        results.append(result)

    trial_count = db.scalar(select(func.count()).select_from(Trial)) or 0
    if dry_run:
        db.rollback()
    else:
        imported = [result.filename for result in results if not result.error]
        if imported:
            audit.record(db, admin.email, "data.import", ", ".join(imported))
        db.commit()
    return ImportResponse(dry_run=dry_run, files=results, trial_count=trial_count)


@router.get("/files", response_model=list[SourceFileOut], summary="Imported source files")
def list_files(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[SourceFileOut]:
    files = db.scalars(select(SourceFile).options(selectinload(SourceFile.records)).order_by(SourceFile.id))
    return [_file_out(source) for source in files]


@router.delete("/files/{file_id}", summary="Remove a source file and its records")
def delete_file(file_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    source = db.get(SourceFile, file_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found.")
    filename = source.filename
    trial_ids = remove_file(db, load_settings(db), source)
    audit.record(db, admin.email, "data.remove_file", filename, f"affected trials {', '.join(trial_ids)}")
    db.commit()
    return {"removed": filename, "affected_trials": trial_ids}


@router.post("/rebuild", response_model=RebuildResponse, summary="Re-parse all files and reconcile every trial")
def rebuild(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> RebuildResponse:
    result = rebuild_all(db, load_settings(db))
    audit.record(db, admin.email, "data.rebuild", "", f"{result.trials} trials, {result.conflicts} with conflicts")
    db.commit()
    return result


@router.post("/import-sample", response_model=ImportResponse, summary="Re-import the bundled sample data")
def import_sample(
    admin: User = Depends(require_admin), db: Session = Depends(get_db), config: Settings = Depends(get_config)
) -> ImportResponse:
    folder = Path(config.sample_data_dir)
    if not folder.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Sample data folder not found: {folder}")
    results = import_folder(db, load_settings(db), folder, admin.email)
    audit.record(db, admin.email, "data.import_sample", folder.name)
    db.commit()
    return ImportResponse(dry_run=False, files=results, trial_count=db.scalar(select(func.count()).select_from(Trial)) or 0)


@sources_router.get("/{filename}", summary="Original content of an imported source file")
def read_source(filename: str, _user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    source = db.scalar(select(SourceFile).where(SourceFile.filename == filename))
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No source file named '{filename}'.")
    return {
        "filename": source.filename,
        "kind": source.kind,
        "parser": source.parser,
        "uploaded_at": source.uploaded_at.isoformat() + "Z",
        "uploaded_by": source.uploaded_by,
        "content": source.content,
    }
