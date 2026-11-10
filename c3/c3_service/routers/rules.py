"""The alert rules routes (Part III, Section 2.2, Table 84), mounted under /api/v1.

    GET    /alert-rules?tenantId         the effective rules: defaults with the tenant's overrides in place
    PUT    /alert-rules/{ruleId}         change a rule (201 when it creates the tenant's override, else 200)
    DELETE /alert-rules/{ruleId}         remove an override (204); a default answers 409

Every route depends on rules_caller (OWNER, RIDER and IOT_ENGINEER get 403). The tenant comes
from the token for a fleet manager; staff and service tokens pass tenantId (a number or a code),
and staff without it see (and SYS_ADMIN edits) the platform defaults. POST /alert-rules, a
brand-new rule, is design only this semester.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Request, Response

from ..auth import Caller
from ..deps import get_session_factory, rules_caller
from ..schemas import RuleChanges, RuleItem
from ..services.rule_service import RuleService

router = APIRouter(tags=["alert-rules"])


def get_rule_service(session_factory: Any = Depends(get_session_factory)) -> RuleService:
    return RuleService(session_factory)


def client_ip(request: Request) -> Optional[str]:
    """For the audit row: the first X-Forwarded-For address (nginx), else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


@router.get("/alert-rules", response_model=List[RuleItem])
async def list_rules(caller: Caller = Depends(rules_caller), service: RuleService = Depends(get_rule_service)):
    return await service.effective_list(caller.tenant_id)


@router.put("/alert-rules/{rule_id}", response_model=RuleItem)
async def put_rule(rule_id: int, body: RuleChanges, request: Request, response: Response,
                   caller: Caller = Depends(rules_caller), service: RuleService = Depends(get_rule_service)):
    """minConfidence 0.40-0.99, minRepeats 1-5, windowSeconds 5-60 (BR-C3-09); who may change what and the
    EMERGENCY / ACCIDENT floor (BR-C3-10): 422 BUSINESS_RULE with details.rule. Audited as RULE_SAVE."""
    item, created = await service.save(caller, rule_id, body.changes(), client_ip(request))
    response.status_code = 201 if created else 200
    return item


@router.delete("/alert-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, request: Request, caller: Caller = Depends(rules_caller),
                      service: RuleService = Depends(get_rule_service)):
    """204 for an override in the caller's scope (the default applies again); 409 for a default. Audited as RULE_DELETE."""
    await service.delete(caller, rule_id, client_ip(request))
    return Response(status_code=204)
