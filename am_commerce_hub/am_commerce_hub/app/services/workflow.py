"""??????????

??(ApprovalTask)????????????????????????
- ??????  ? ??????&?? ? CSV/ASN/Invoice?confirm_shipment?
- ???????? ? ?????mark_approved??????? receive ???
- ????      ? ????????? ? Listings???publish_draft?
- ??????  ? ????? ? Ads API????apply_recommendations?

??????????????????? import ???????????????
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalType,
    ListingDraft,
    ListingDraftStatus,
)
from app.integrations.registry import Integrations, get_integrations
from app.logging_config import get_logger
from app.services import (
    advertising_service,
    approval_service,
    catalog_service,
    order_service,
    purchasing_service,
)

log = get_logger("amhub.workflow")


def apply_decision(session: Session, *, task_id: int, approved: bool, by: str,
                   integ: Integrations | None = None) -> dict:
    """?????????????????????????????"""
    integ = get_integrations(integ)
    task = approval_service.decide(session, task_id, approved, by=by)
    log.info("approval decided id=%s type=%s approved=%s by=%s",
             task.id, task.approval_type.value, approved, by)
    result: dict = {"task_id": task.id, "type": task.approval_type.value,
                    "status": task.status.value, "decided_by": task.decided_by,
                    "action": None}

    if not approved:
        if task.approval_type == ApprovalType.AD_BID:
            n = advertising_service.approve_all(session, accept=False)
            result["action"] = {"rejected_recommendations": n}
        return result

    if task.approval_type == ApprovalType.DAILY_SHIPMENT:
        po = order_service.confirm_shipment(session, task.ref_id, integ=integ)
        result["action"] = {"po": po.amazon_po_number, "po_status": po.status.value,
                            "asn": po.asn_id, "invoice": po.invoice_id}

    elif task.approval_type == ApprovalType.SUPPLIER_PO:
        spo = purchasing_service.mark_approved(session, task.ref_id)
        result["action"] = {"supplier_po": spo.id, "status": spo.status.value,
                            "note": "?????????????? /supplier-pos/{id}/receive ????"}

    elif task.approval_type == ApprovalType.LISTING:
        draft = session.get(ListingDraft, task.ref_id)
        draft.status = ListingDraftStatus.APPROVED
        published = catalog_service.publish_draft(session, draft.id, integ=integ)
        result["action"] = {"draft_id": draft.id, "published_asin": published.published_asin}

    elif task.approval_type == ApprovalType.AD_BID:
        approved_n = advertising_service.approve_all(session, accept=True)
        report_date = date.fromordinal(task.ref_id) if task.ref_id else None
        applied = advertising_service.apply_recommendations(
            session, report_date=report_date, integ=integ)
        result["action"] = {"approved_recommendations": approved_n, "applied": applied}

    return result
