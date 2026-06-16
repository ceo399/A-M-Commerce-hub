?"""??????? v2 API"""
from __future__ import annotations
from datetime import datetime, date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from pydantic import BaseModel
from app.api.deps import get_current_user
from app.db.models import Product, Order, OrderStatus, AdMetricSnapshot
from app.db.session import session_scope

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

class KPIData(BaseModel):
    date: str
    total_sales: float
    gross_profit: float
    gross_margin_percent: float
    total_spend: float
    roas: float
    active_asin_count: int

@router.get("/kpi", response_model=KPIData)
def get_kpi_dashboard(_=Depends(get_current_user)):
    with session_scope() as s:
        today = date.today()
        orders = s.scalars(select(Order).where(Order.status.in_([
            OrderStatus.READY_TO_SHIP, OrderStatus.SHIPPED, 
            OrderStatus.INVOICED, OrderStatus.CLOSED
        ]))).all()
        total_sales = sum(float(o.total_price or 0) for o in orders)
        gross_profit = total_sales * 0.24
        metrics = s.scalars(select(func.sum(AdMetricSnapshot.spend)).where(
            AdMetricSnapshot.report_date == today)).first() or 0
        ad_spend = float(metrics) if metrics else 0
        roas = (total_sales / ad_spend) if ad_spend > 0 else 0
        active_asins = s.scalar(select(func.count(Product.id)).where(
            Product.status == "active")) or 0
        
        return KPIData(
            date=today.isoformat(),
            total_sales=total_sales,
            gross_profit=gross_profit,
            gross_margin_percent=(gross_profit / total_sales * 100 if total_sales > 0 else 0),
            total_spend=ad_spend,
            roas=roas,
            active_asin_count=active_asins,
        )

@router.get("/content-analysis")
def get_content_analysis(limit: int = Query(20, le=100), _=Depends(get_current_user)):
    with session_scope() as s:
        products = s.scalars(select(Product).limit(limit)).all()
        return {"timestamp": datetime.now().isoformat(), "analyses": [
            {"asin": p.asin, "product_name": p.name, "overall_quality_score": 0.65,
             "image_count": p.image_count or 0, "has_video": bool(p.video_link),
             "has_aplus": p.has_aplus or False} for p in products]}

@router.post("/ai-analysis")
def generate_ai_analysis(req_limit: int = 50, _=Depends(get_current_user)):
    with session_scope() as s:
        products = s.scalars(select(Product).limit(5)).all()
        return {"timestamp": datetime.now().isoformat(), "suggestions": [
            {"asin": p.asin, "product_name": p.name, "diagnosis": "????????????",
             "recommended_action": "A+ ??????????????", "priority": (i % 3) + 1}
            for i, p in enumerate(products)]}
