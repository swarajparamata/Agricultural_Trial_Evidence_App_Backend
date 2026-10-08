from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_app_settings, get_current_user, require_admin
from ..models import User
from ..schemas.settings import AppSettings
from ..schemas.trials import ConflictResolution, TrialCreate, TrialDetailOut, TrialListOut, TrialOut, TrialUpdate
from ..services import trials as service

router = APIRouter(prefix="/trials", tags=["trials"])

FILTER_PARAMS = ("crop", "product", "country", "year", "trial_type", "status", "q")


def trial_filters(request: Request) -> tuple[dict[str, str], dict[str, str]]:
    """Core filters plus custom-field filters passed as `cf_<key>=value`."""
    params = request.query_params
    core = {name: params[name] for name in FILTER_PARAMS if params.get(name)}
    custom = {name[3:]: value for name, value in params.items() if name.startswith("cf_") and value}
    return core, custom


@router.get("", response_model=TrialListOut, summary="List reconciled trials")
def list_trials(
    request: Request,
    crop: str | None = None,
    product: str | None = None,
    country: str | None = None,
    year: int | None = None,
    trial_type: str | None = None,
    status: str | None = None,
    q: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialListOut:
    """Filters match ignoring case and spelling variants of the canonical value; add `cf_<key>=value`
    to filter on a custom field and `q` for free text."""
    view = service.TrialView(settings)
    trials = [view.out(trial) for trial in service.all_trials(db)]
    core, custom = trial_filters(request)
    items = service.filter_trials(trials, core, custom, settings)
    return TrialListOut(items=items, total=len(items), unit=view.unit)


@router.get("/deleted", summary="Trials deleted by admins (admin)")
def list_deleted(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return service.deleted_trials(db)


@router.get("/{trial_id}", response_model=TrialDetailOut, summary="One trial with its source records and overrides")
def get_trial(
    trial_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialDetailOut:
    trial = service.get_trial_or_404(db, trial_id)
    return service.TrialView(settings).detail(
        trial, service.trial_records(db, trial.id), service.trial_overrides(db, trial.id)
    )


@router.post("", response_model=TrialOut, status_code=status.HTTP_201_CREATED, summary="Add a trial (admin)")
def create_trial(
    body: TrialCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialOut:
    trial = service.create_trial(db, settings, body, admin.email)
    db.commit()
    return service.TrialView(settings).out(trial)


@router.patch("/{trial_id}", response_model=TrialOut, summary="Edit a trial (admin)")
def update_trial(
    trial_id: str,
    body: TrialUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialOut:
    """Edits of imported trials are stored as admin overrides on top of the source values, so the
    sources stay intact; send null for a field to go back to its source value."""
    trial = service.update_trial(db, settings, trial_id, body, admin.email)
    db.commit()
    return service.TrialView(settings).out(trial)


@router.delete("/{trial_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a trial (admin)")
def delete_trial(
    trial_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> None:
    service.delete_trial(db, settings, trial_id, admin.email)
    db.commit()


@router.post("/{trial_id}/restore", response_model=TrialOut, summary="Restore a deleted trial (admin)")
def restore_trial(
    trial_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialOut:
    trial = service.restore_trial(db, settings, trial_id, admin.email)
    db.commit()
    return service.TrialView(settings).out(trial)


@router.post(
    "/{trial_id}/conflicts/{field}/resolve", response_model=TrialOut, summary="Choose the value of a conflicting field (admin)"
)
def resolve_conflict(
    trial_id: str,
    field: str,
    body: ConflictResolution,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialOut:
    trial = service.resolve_conflict(db, settings, trial_id, field, body, admin.email)
    db.commit()
    return service.TrialView(settings).out(trial)


@router.delete("/{trial_id}/overrides/{field}", response_model=TrialOut, summary="Revert a field to its source value (admin)")
def revert_field(
    trial_id: str,
    field: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TrialOut:
    trial = service.remove_override(db, settings, trial_id, field, admin.email)
    db.commit()
    return service.TrialView(settings).out(trial)
