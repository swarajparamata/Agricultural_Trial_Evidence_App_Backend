from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_app_settings, get_current_user
from ..models import User
from ..schemas.analysis import AnalyticsOut, CompareOut, CompareRequest
from ..schemas.settings import AppSettings
from ..services import trials as trial_service
from ..services.analysis import analytics, compare_trials
from ..services.parsers import clean_trial_id
from .trials import trial_filters

router = APIRouter(tags=["analysis"])


@router.post("/compare", response_model=CompareOut, summary="Compare trials side by side, with chart data")
def compare(
    body: CompareRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> CompareOut:
    ids = list(dict.fromkeys(clean_trial_id(trial_id) for trial_id in body.trial_ids))
    if len(ids) > settings.comparison.max_trials:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Compare at most {settings.comparison.max_trials} trials at a time."
        )
    view = trial_service.TrialView(settings)
    by_id = {trial.id: trial for trial in trial_service.all_trials(db)}
    found = [view.out(by_id[trial_id]) for trial_id in ids if trial_id in by_id]
    not_found = [trial_id for trial_id in ids if trial_id not in by_id]
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="None of the requested trials exist.")
    return compare_trials(found, settings, not_found)


@router.get("/analytics/overview", response_model=AnalyticsOut, summary="KPIs and uplift by product, crop, country, year")
def overview(
    request: Request,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> AnalyticsOut:
    """Accepts the same filters as `GET /trials`."""
    view = trial_service.TrialView(settings)
    trials = [view.out(trial) for trial in trial_service.all_trials(db)]
    core, custom = trial_filters(request)
    return analytics(trial_service.filter_trials(trials, core, custom, settings), view.unit)
