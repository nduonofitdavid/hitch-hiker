"""Administrator review of contributor activation, catalogue proposals, and reported records."""

import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlmodel import select, or_

from app.api.deps import SessionDep, AdminDep
from app.api.validation import validate_geography, validate_route, validate_place, commit_changes, stage_audit, CONTRIBUTION_TARGETS, FLAG_TARGETS
from app.core.rlimiter import limiter
from app.models import Contributor, ContributorActivation, ContributorRecord, ContributorAction, ContributionStatus, ReviewDecision, Place, PlaceInUser, Stops, StopsIn, Route, RouteIn, User, FlagStatus, get_datetime_utc
from app.utils import generate_contributor_approved_email, notify_by_email

router = APIRouter(prefix="/admin", tags=["moderation"])

def archive(session, target):
  """Stage target deactivation and disable connected routes for an archived stop."""
  target.is_active = False
  if isinstance(target, Stops):
    for route in session.exec(select(Route).where(or_(Route.start == target.id, Route.end == target.id)).order_by(Route.id).with_for_update()).all():
      route.is_active = False
      session.add(route)
  session.add(target)

@router.get("/contributors")
@limiter.limit("10/minute")
def contributors(request: Request, session: SessionDep, admin: AdminDep, is_enabled: bool | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
  """List contributor profiles filtered by activation state in a stable order."""
  query = select(Contributor)
  if is_enabled is not None:
    query = query.where(Contributor.is_enabled == is_enabled)
  return session.exec(query.order_by(Contributor.date_joined, Contributor.id).offset(offset).limit(limit)).all()

@router.put("/contributors/{contributor_id}/activation")
@limiter.limit("10/minute")
def activate_contributor(request: Request, session: SessionDep, admin: AdminDep, contributor_id: uuid.UUID, payload: ContributorActivation):
  """Set a locked profile's activation state and notify newly enabled contributors."""
  contributor = session.exec(select(Contributor).where(Contributor.id == contributor_id).with_for_update()).first()
  if contributor is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contributor not found")
  
  changed = contributor.is_enabled != payload.is_enabled
  contributor.is_enabled = payload.is_enabled
  if payload.is_enabled:
    contributor.approved_by = admin.id
  
  session.add(contributor)
  stage_audit(session, admin, f"Contributor {contributor.id} enabled={payload.is_enabled}")
  commit_changes(session)
  session.refresh(contributor)

  if changed and payload.is_enabled:
    user = session.get(User, contributor.user_id)
    if user:
      notify_by_email(email_to=user.email, prepare=generate_contributor_approved_email)
  return contributor

@router.get("/contributions")
@limiter.limit("10/minute")
def contributions(request: Request, session: SessionDep, admin: AdminDep, contribution_status: ContributionStatus | None = None, contributor_id: uuid.UUID | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
  """List contribution records filtered by status or contributor in submission order."""
  query = select(ContributorRecord)
  if contribution_status is not None:
    query = query.where(ContributorRecord.contribution_status == contribution_status)
  if contributor_id is not None:
    query = query.where(ContributorRecord.contributor_id == contributor_id)
  return session.exec(query.order_by(ContributorRecord.submitted_at, ContributorRecord.id).offset(offset).limit(limit)).all()

def proposal_payload(record):
  """Validate a recorded proposal against its target's input schema."""
  schema = {"stop": StopsIn, "route": RouteIn, "place": PlaceInUser}[record.target_type]
  try:
    return schema.model_validate(record.proposed_changes)
  except ValueError as exc:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contribution proposal needs reconciliation") from exc

def comparable_payload(payload):
  """Serialize a proposal with sorted transport modes for order-independent comparison."""
  data = payload.model_dump(mode="json")
  if "transport_mode" in data:
    data["transport_mode"] = sorted(data["transport_mode"])
  return data

def review_creation(target, proposal, accept: bool):
  """Accept an unchanged unpublished proposal or leave a rejected target inactive."""
  if target.is_active:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contribution target is already active")
  if accept:
    if isinstance(target, Route):
      current = RouteIn(start=target.start, end=target.end, time=target.time, price=target.price, transport_mode=[mode.id for mode in target.transport_modes])
    elif isinstance(target, Place):
      current = PlaceInUser.model_validate(target)
    else:
      current = StopsIn.model_validate(target)
    if comparable_payload(current) != comparable_payload(proposal):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Submission changed since its proposal was recorded; reconcile it before acceptance")
    target.is_active = True

def review_place_edit(record, target, proposal, accept: bool):
  """Apply a canonical place proposal only if its original published data is current."""
  if not isinstance(target, Place) or record.pending_place_id is None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pending place edit is missing")
  if accept:
    current = PlaceInUser.model_validate(target).model_dump(mode="json")
    if record.original_data != current:
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Place changed since this edit was submitted; submit a new edit")
    target.sqlmodel_update(proposal.model_dump())

def finish_review(session, admin, record, target, accept: bool):
  """Commit the decision, target change, audit entry, and optional graph revision together."""
  record.contribution_status = ContributionStatus.ACCEPTED if accept else ContributionStatus.DENIED
  record.reviewed_by = admin.id
  record.reviewed_at = get_datetime_utc()
  session.add(record)
  session.add(target)
  stage_audit(session, admin, f"Contribution {record.id} {record.contribution_status.name.lower()}")
  commit_changes(session, graph_changed=accept and isinstance(target, (Stops, Route)))
  session.refresh(record)
  return record

@router.post("/contributions/{record_id}/review")
@limiter.limit("10/minute")
def review_contribution(request: Request, session: SessionDep, admin: AdminDep, record_id: uuid.UUID, payload: ReviewDecision):
  """Review a pending submission atomically after checking provenance and concurrent edits."""
  record = session.exec(select(ContributorRecord).where(ContributorRecord.id == record_id).with_for_update()).first()
  if record is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
  if record.contribution_status != ContributionStatus.PENDING:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contribution has already been reviewed")
  
  contributor = session.exec(select(Contributor).where(Contributor.id == record.contributor_id).with_for_update()).first()
  if payload.accept and (contributor is None or not contributor.is_enabled):
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Enable this contributor before accepting submissions")
  
  model = CONTRIBUTION_TARGETS.get(record.target_type)
  if model is None or record.target_id is None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Legacy contribution has no linked target; reconcile its provenance before review")
  
  proposal = None
  if payload.accept and record.action_type in {ContributorAction.CREATE, ContributorAction.UPDATE}:
    proposal = proposal_payload(record)
    # All catalogue writers acquire geography/endpoints before their target.
    if isinstance(proposal, RouteIn):
      validate_route(session, proposal)
    elif isinstance(proposal, PlaceInUser):
      validate_place(session, proposal)
    else:
      validate_geography(session, proposal.city, proposal.country)

  target = session.exec(select(model).where(model.id == record.target_id).with_for_update()).first()
  if target is None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contribution target no longer exists")
  
  owner = target.created_by if isinstance(target, Place) else target.contributed_by
  if contributor is None or owner != contributor.user_id:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contribution provenance does not match its target")
  
  if record.action_type == ContributorAction.UPDATE:
    review_place_edit(record, target, proposal, payload.accept)
  elif record.action_type == ContributorAction.CREATE:
    review_creation(target, proposal, payload.accept)
  elif record.action_type == ContributorAction.REQUEST_DELETION:
    if payload.accept:
      archive(session, target)
  else:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported contribution action")
  return finish_review(session, admin, record, target, payload.accept)

@router.get("/flags/{target_type}")
@limiter.limit("10/minute")
def flags(request: Request, session: SessionDep, admin: AdminDep, target_type: Literal["stop", "route", "place"], flag_status: FlagStatus | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
  """List flags for the requested target type and optional status."""
  model, _, _ = FLAG_TARGETS[target_type]
  query = select(model)
  if flag_status is not None:
    query = query.where(model.status == flag_status)
  return session.exec(query.order_by(model.date_flagged, model.id).offset(offset).limit(limit)).all()

@router.post("/flags/{target_type}/{flag_id}/resolve")
@limiter.limit("10/minute")
def resolve_flag(request: Request, session: SessionDep, admin: AdminDep, target_type: Literal["stop", "route", "place"], flag_id: uuid.UUID, payload: ReviewDecision):
  """Resolve a pending flag atomically, archiving its target when accepted."""
  flag_model, target_model, target_field = FLAG_TARGETS[target_type]

  flag = session.exec(select(flag_model).where(flag_model.id == flag_id).with_for_update()).first()
  if flag is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flag not found")
  if flag.status != FlagStatus.PENDING:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Flag has already been resolved")
  if payload.accept:
    target_id = getattr(flag, target_field)
    target = session.exec(select(target_model).where(target_model.id == target_id).with_for_update()).first()
    if target is None:
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Flag target no longer exists")
    archive(session, target)
    
  flag.status = FlagStatus.RESOLVED
  session.add(flag)
  stage_audit(session, admin, f"Flag {target_type}/{flag.id} resolved; accepted={payload.accept}")
  commit_changes(session, graph_changed=payload.accept and target_type in {"stop", "route"})
  session.refresh(flag)
  return flag
