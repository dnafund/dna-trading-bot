"""
Billing API — subscription management, Stripe Checkout, webhooks.

Public endpoints:
    POST /api/billing/webhook  — Stripe webhook (no auth)

Protected endpoints (JWT required):
    GET  /api/billing/status      — Current subscription + volume info
    GET  /api/billing/tiers       — Available pricing tiers
    POST /api/billing/checkout    — Create Stripe Checkout Session
    POST /api/billing/portal      — Create Stripe Billing Portal Session
    GET  /api/billing/volume      — Volume tracking for current month
    GET  /api/billing/invoices    — Recent invoices from Stripe
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from web.backend.auth import TokenPayload, get_current_user_obj

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ── Request/Response Models ──────────────────────────────────────


class CheckoutRequest(BaseModel):
    tier: str = Field(..., pattern="^(basic|pro)$")
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class SubscriptionStatus(BaseModel):
    tier: str
    tier_name: str
    price_usd: float
    max_symbols: int
    volume_fee_rate: float
    features: list[str]
    stripe_sub_id: Optional[str] = None
    expires_at: Optional[str] = None
    is_active: bool


# ── Public: Webhook ───────────────────────────────────────────────


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint — processes subscription lifecycle events.

    No JWT required — authenticated via Stripe signature verification.
    """
    from src.billing.stripe_service import handle_webhook

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        result = handle_webhook(payload, sig_header)
        return result
    except ValueError as e:
        logger.warning(f"[BILLING] Webhook verification failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[BILLING] Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


# ── Protected: Subscription Status ───────────────────────────────


@router.get("/status")
async def get_subscription_status(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Get current subscription status and tier details."""
    from src.billing.stripe_service import (
        get_active_subscription,
        get_tier_config,
        get_user_tier,
        get_volume_summary,
    )

    user_id = current_user.user_id
    tier = get_user_tier(user_id)
    tier_config = get_tier_config(tier)
    sub = get_active_subscription(user_id)
    volume = get_volume_summary(user_id)

    return {
        "success": True,
        "data": {
            "subscription": {
                "tier": tier,
                "tier_name": tier_config["name"],
                "price_usd": tier_config["price_usd"],
                "max_symbols": tier_config["max_symbols"],
                "volume_fee_rate": tier_config["volume_fee_rate"],
                "features": tier_config["features"],
                "stripe_sub_id": sub.stripe_sub_id if sub else None,
                "expires_at": (
                    sub.expires_at.isoformat() if sub and sub.expires_at else None
                ),
                "is_active": tier != "free",
            },
            "volume": volume,
        },
    }


@router.get("/tiers")
async def get_available_tiers(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Get all available pricing tiers."""
    from src.billing.stripe_service import TIERS, get_user_tier

    user_id = current_user.user_id
    current_tier = get_user_tier(user_id)

    tiers = []
    for tier_key, config in TIERS.items():
        tiers.append({
            "id": tier_key,
            "name": config["name"],
            "price_usd": config["price_usd"],
            "max_symbols": config["max_symbols"],
            "volume_fee_rate": config["volume_fee_rate"],
            "features": config["features"],
            "is_current": tier_key == current_tier,
        })

    return {"success": True, "data": tiers}


# ── Protected: Checkout & Portal ─────────────────────────────────


@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Create a Stripe Checkout Session for subscription purchase.

    Returns a URL to redirect the user to Stripe's hosted checkout page.
    """
    from src.billing.stripe_service import create_checkout_session

    user_id = current_user.user_id

    try:
        result = create_checkout_session(
            user_id=user_id,
            tier=body.tier,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[BILLING] Checkout creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/portal")
async def create_portal(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Create a Stripe Billing Portal session.

    Returns a URL where the user can manage their subscription,
    update payment method, view invoices, or cancel.
    """
    from src.billing.stripe_service import create_billing_portal_session

    user_id = current_user.user_id

    try:
        result = create_billing_portal_session(user_id)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"[BILLING] Portal creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create portal session")


# ── Protected: Volume Tracking ────────────────────────────────────


@router.get("/volume")
async def get_volume(
    month: Optional[str] = None,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Get volume tracking data for the current or specified month."""
    from src.billing.stripe_service import get_volume_summary

    user_id = current_user.user_id
    summary = get_volume_summary(user_id, month)

    return {"success": True, "data": summary}


@router.get("/invoices")
async def get_invoices(
    limit: int = 10,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Get recent invoices from Stripe for the current user."""
    import stripe as stripe_lib

    from src.billing.stripe_service import _get_or_create_stripe_customer

    user_id = current_user.user_id

    try:
        customer_id = _get_or_create_stripe_customer(user_id)
        invoices = stripe_lib.Invoice.list(
            customer=customer_id,
            limit=min(limit, 50),
        )

        invoice_list = []
        for inv in invoices.data:
            invoice_list.append({
                "id": inv.id,
                "number": inv.number,
                "status": inv.status,
                "amount_due": inv.amount_due / 100,  # cents → dollars
                "amount_paid": inv.amount_paid / 100,
                "currency": inv.currency,
                "created": inv.created,
                "period_start": inv.period_start,
                "period_end": inv.period_end,
                "hosted_invoice_url": inv.hosted_invoice_url,
                "invoice_pdf": inv.invoice_pdf,
            })

        return {"success": True, "data": invoice_list}
    except Exception as e:
        logger.error(f"[BILLING] Invoice fetch failed: {e}")
        return {"success": True, "data": []}
