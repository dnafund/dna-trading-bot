"""
Stripe Billing Service — subscription management + volume fee tracking.

Uses Stripe Checkout Sessions (hosted) for payment collection.
Webhooks handle subscription lifecycle events.

Tiers:
    free  — paper trading only, 3 symbols, no volume fee
    basic — $29/mo, 10 symbols, 0.01% volume fee
    pro   — $79/mo, unlimited symbols, 0.005% volume fee

Environment variables required:
    STRIPE_SECRET_KEY      — Stripe API secret key
    STRIPE_WEBHOOK_SECRET  — Webhook signing secret
    STRIPE_PRICE_BASIC     — Stripe Price ID for Basic tier
    STRIPE_PRICE_PRO       — Stripe Price ID for Pro tier
    DASHBOARD_URL          — Frontend URL for redirects (e.g., https://dnatradingbot.com)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import stripe

from src.database.connection import get_session
from src.database.models import Subscription, User, VolumeTracking

logger = logging.getLogger(__name__)

# ── Stripe Configuration ─────────────────────────────────────────

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:5173")

# Stripe Price IDs (created in Stripe Dashboard)
PRICE_IDS = {
    "basic": os.getenv("STRIPE_PRICE_BASIC", ""),
    "pro": os.getenv("STRIPE_PRICE_PRO", ""),
}

# ── Tier Configuration ────────────────────────────────────────────

TIERS = {
    "free": {
        "name": "Free",
        "price_usd": 0,
        "max_symbols": 3,
        "volume_fee_rate": 0.0,
        "features": ["Paper trading only", "3 symbols", "Basic dashboard"],
    },
    "basic": {
        "name": "Basic",
        "price_usd": 29,
        "max_symbols": 10,
        "volume_fee_rate": 0.0001,  # 0.01%
        "features": ["Live trading", "10 symbols", "Telegram alerts", "Email support"],
    },
    "pro": {
        "name": "Pro",
        "price_usd": 79,
        "max_symbols": 999,
        "volume_fee_rate": 0.00005,  # 0.005%
        "features": [
            "Live trading", "Unlimited symbols", "Priority signals",
            "Telegram alerts", "Priority support",
        ],
    },
}


def get_tier_config(tier: str) -> dict:
    """Get tier configuration. Returns free tier if unknown."""
    return TIERS.get(tier, TIERS["free"])


# ── Subscription Queries ──────────────────────────────────────────


def get_active_subscription(user_id: str) -> Optional[Subscription]:
    """Get user's active subscription (non-expired)."""
    with get_session() as session:
        now = datetime.now(timezone.utc)
        sub = (
            session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tier != "free",
            )
            .order_by(Subscription.started_at.desc())
            .first()
        )
        if sub is None:
            return None
        # Check expiration
        if sub.expires_at and sub.expires_at < now:
            return None
        return sub


def get_user_tier(user_id: str) -> str:
    """Get user's current tier (free if no active subscription)."""
    sub = get_active_subscription(user_id)
    if sub is None:
        return "free"
    return sub.tier


def get_symbol_limit(user_id: str) -> int:
    """Get maximum symbols allowed for user's tier."""
    tier = get_user_tier(user_id)
    return get_tier_config(tier)["max_symbols"]


def get_volume_fee_rate(user_id: str) -> float:
    """Get volume fee rate for user's tier."""
    tier = get_user_tier(user_id)
    return get_tier_config(tier)["volume_fee_rate"]


# ── Stripe Checkout ───────────────────────────────────────────────


def create_checkout_session(
    user_id: str,
    tier: str,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> dict:
    """Create a Stripe Checkout Session for subscription.

    Uses Stripe-hosted Checkout page (best security, lowest PCI scope).

    Args:
        user_id: Internal user ID.
        tier: "basic" or "pro".
        success_url: Redirect URL on success.
        cancel_url: Redirect URL on cancel.

    Returns:
        {"checkout_url": str, "session_id": str}

    Raises:
        ValueError: If tier is invalid or Price ID not configured.
    """
    if tier not in ("basic", "pro"):
        raise ValueError(f"Invalid tier: {tier}. Must be 'basic' or 'pro'.")

    price_id = PRICE_IDS.get(tier)
    if not price_id:
        raise ValueError(
            f"Stripe Price ID for '{tier}' not configured. "
            f"Set STRIPE_PRICE_{tier.upper()} environment variable."
        )

    # Get or create Stripe customer
    stripe_customer_id = _get_or_create_stripe_customer(user_id)

    checkout_session = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url or f"{DASHBOARD_URL}/billing?status=success",
        cancel_url=cancel_url or f"{DASHBOARD_URL}/billing?status=cancelled",
        metadata={"user_id": user_id, "tier": tier},
        subscription_data={"metadata": {"user_id": user_id, "tier": tier}},
    )

    logger.info(
        f"[BILLING] Checkout session created: user={user_id} tier={tier} "
        f"session={checkout_session.id}"
    )
    return {
        "checkout_url": checkout_session.url,
        "session_id": checkout_session.id,
    }


def create_billing_portal_session(user_id: str) -> dict:
    """Create a Stripe Billing Portal session for subscription management.

    Users can update payment method, cancel, or view invoices.

    Returns:
        {"portal_url": str}
    """
    stripe_customer_id = _get_or_create_stripe_customer(user_id)

    portal_session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=f"{DASHBOARD_URL}/billing",
    )

    return {"portal_url": portal_session.url}


# ── Webhook Handling ──────────────────────────────────────────────


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process Stripe webhook events.

    Args:
        payload: Raw request body bytes.
        sig_header: Stripe-Signature header value.

    Returns:
        {"status": "ok", "event_type": str} on success.

    Raises:
        ValueError: If signature verification fails.
    """
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        raise ValueError("Invalid webhook signature")

    event_type = event["type"]
    data = event["data"]["object"]

    handler = _WEBHOOK_HANDLERS.get(event_type)
    if handler:
        handler(data)
        logger.info(f"[BILLING] Webhook processed: {event_type}")
    else:
        logger.debug(f"[BILLING] Unhandled webhook event: {event_type}")

    return {"status": "ok", "event_type": event_type}


def _handle_checkout_completed(session_data: dict) -> None:
    """Handle checkout.session.completed — subscription created via Checkout."""
    if session_data.get("mode") != "subscription":
        return

    user_id = session_data.get("metadata", {}).get("user_id")
    tier = session_data.get("metadata", {}).get("tier")
    stripe_sub_id = session_data.get("subscription")

    if not user_id or not tier:
        logger.warning("[BILLING] Checkout completed but missing metadata")
        return

    _upsert_subscription(
        user_id=user_id,
        tier=tier,
        stripe_sub_id=stripe_sub_id,
        price_usd=TIERS.get(tier, {}).get("price_usd", 0),
    )


def _handle_subscription_updated(sub_data: dict) -> None:
    """Handle customer.subscription.updated — plan change, renewal."""
    user_id = sub_data.get("metadata", {}).get("user_id")
    tier = sub_data.get("metadata", {}).get("tier")
    stripe_sub_id = sub_data.get("id")
    status = sub_data.get("status")

    if not user_id:
        logger.warning("[BILLING] Subscription updated but missing user_id metadata")
        return

    if status in ("active", "trialing"):
        current_period_end = sub_data.get("current_period_end")
        expires_at = (
            datetime.fromtimestamp(current_period_end, tz=timezone.utc)
            if current_period_end
            else None
        )
        _upsert_subscription(
            user_id=user_id,
            tier=tier or "basic",
            stripe_sub_id=stripe_sub_id,
            price_usd=TIERS.get(tier or "basic", {}).get("price_usd", 0),
            expires_at=expires_at,
        )
    elif status in ("canceled", "unpaid", "past_due"):
        _deactivate_subscription(user_id, stripe_sub_id)


def _handle_subscription_deleted(sub_data: dict) -> None:
    """Handle customer.subscription.deleted — subscription cancelled."""
    user_id = sub_data.get("metadata", {}).get("user_id")
    stripe_sub_id = sub_data.get("id")

    if user_id:
        _deactivate_subscription(user_id, stripe_sub_id)


def _handle_invoice_paid(invoice_data: dict) -> None:
    """Handle invoice.paid — successful payment (renewal or new)."""
    stripe_sub_id = invoice_data.get("subscription")
    if not stripe_sub_id:
        return

    # Update subscription expiry based on invoice period
    period_end = invoice_data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
    if not period_end:
        return

    with get_session() as session:
        sub = (
            session.query(Subscription)
            .filter(Subscription.stripe_sub_id == stripe_sub_id)
            .first()
        )
        if sub:
            sub.expires_at = datetime.fromtimestamp(period_end, tz=timezone.utc)
            logger.info(
                f"[BILLING] Invoice paid: sub={stripe_sub_id} "
                f"expires={sub.expires_at.isoformat()}"
            )


_WEBHOOK_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.paid": _handle_invoice_paid,
}


# ── Volume Fee Tracking ───────────────────────────────────────────


def record_trade_volume(user_id: str, volume_usd: float) -> dict:
    """Record a trade's volume for fee calculation.

    Called by TradeExecutor after each trade. Updates both Redis (fast)
    and PostgreSQL (durable).

    Args:
        user_id: User who made the trade.
        volume_usd: Trade notional value in USD.

    Returns:
        {"month": str, "total_volume": float, "fee_rate": float, "fee_due": float}
    """
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    fee_rate = get_volume_fee_rate(user_id)

    if fee_rate <= 0:
        return {"month": month, "total_volume": 0, "fee_rate": 0, "fee_due": 0}

    # Update Redis counter (fast, for real-time display)
    try:
        from src.services.redis_client import increment_volume
        increment_volume(user_id, month, volume_usd)
    except Exception as e:
        logger.warning(f"[BILLING] Redis volume update failed: {e}")

    # Update PostgreSQL (durable, for billing)
    with get_session() as session:
        tracking = (
            session.query(VolumeTracking)
            .filter(
                VolumeTracking.user_id == user_id,
                VolumeTracking.month == month,
            )
            .first()
        )

        if tracking is None:
            tracking = VolumeTracking(
                user_id=user_id,
                month=month,
                total_volume=volume_usd,
                fee_rate=fee_rate,
                fee_due=round(volume_usd * fee_rate, 4),
            )
            session.add(tracking)
        else:
            tracking.total_volume = tracking.total_volume + volume_usd
            tracking.fee_rate = fee_rate
            tracking.fee_due = round(tracking.total_volume * fee_rate, 4)

        result = {
            "month": month,
            "total_volume": round(tracking.total_volume, 2),
            "fee_rate": fee_rate,
            "fee_due": round(tracking.fee_due, 4),
        }

    logger.debug(
        f"[BILLING] Volume recorded: user={user_id} "
        f"volume=${volume_usd:.2f} total=${result['total_volume']:.2f}"
    )
    return result


def get_volume_summary(user_id: str, month: Optional[str] = None) -> dict:
    """Get volume tracking summary for a user.

    Args:
        user_id: User ID.
        month: Month string (e.g., "2026-02"). Defaults to current month.

    Returns:
        {"month": str, "total_volume": float, "fee_rate": float,
         "fee_due": float, "fee_paid": bool}
    """
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    with get_session() as session:
        tracking = (
            session.query(VolumeTracking)
            .filter(
                VolumeTracking.user_id == user_id,
                VolumeTracking.month == month,
            )
            .first()
        )

        if tracking is None:
            return {
                "month": month,
                "total_volume": 0,
                "fee_rate": get_volume_fee_rate(user_id),
                "fee_due": 0,
                "fee_paid": False,
            }

        return {
            "month": month,
            "total_volume": round(tracking.total_volume, 2),
            "fee_rate": tracking.fee_rate,
            "fee_due": round(tracking.fee_due, 4),
            "fee_paid": tracking.fee_paid,
        }


# ── Internal Helpers ──────────────────────────────────────────────


def _get_or_create_stripe_customer(user_id: str) -> str:
    """Get or create Stripe Customer for a user.

    Stores stripe_customer_id in user's metadata (data JSONB on User
    would be ideal, but we use Stripe search by metadata instead).
    """
    # Search for existing customer by metadata
    customers = stripe.Customer.search(
        query=f"metadata['user_id']:'{user_id}'",
    )
    if customers.data:
        return customers.data[0].id

    # Create new customer
    with get_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f"User not found: {user_id}")

        customer = stripe.Customer.create(
            email=user.email,
            name=user.name or user.username,
            metadata={"user_id": user_id},
        )
        logger.info(f"[BILLING] Stripe customer created: {customer.id} user={user_id}")
        return customer.id


def _upsert_subscription(
    user_id: str,
    tier: str,
    stripe_sub_id: Optional[str] = None,
    price_usd: float = 0,
    expires_at: Optional[datetime] = None,
) -> None:
    """Create or update subscription record in PostgreSQL."""
    with get_session() as session:
        # Find existing subscription for this Stripe sub ID
        existing = None
        if stripe_sub_id:
            existing = (
                session.query(Subscription)
                .filter(Subscription.stripe_sub_id == stripe_sub_id)
                .first()
            )

        if existing:
            existing.tier = tier
            existing.price_usd = price_usd
            if expires_at:
                existing.expires_at = expires_at
            logger.info(
                f"[BILLING] Subscription updated: user={user_id} "
                f"tier={tier} stripe_sub={stripe_sub_id}"
            )
        else:
            sub = Subscription(
                user_id=user_id,
                tier=tier,
                price_usd=price_usd,
                stripe_sub_id=stripe_sub_id,
                expires_at=expires_at,
            )
            session.add(sub)
            logger.info(
                f"[BILLING] Subscription created: user={user_id} "
                f"tier={tier} stripe_sub={stripe_sub_id}"
            )


def _deactivate_subscription(user_id: str, stripe_sub_id: Optional[str] = None) -> None:
    """Deactivate a subscription (set expires_at to now)."""
    with get_session() as session:
        query = session.query(Subscription).filter(
            Subscription.user_id == user_id,
        )
        if stripe_sub_id:
            query = query.filter(Subscription.stripe_sub_id == stripe_sub_id)

        sub = query.order_by(Subscription.started_at.desc()).first()
        if sub:
            sub.expires_at = datetime.now(timezone.utc)
            sub.tier = "free"
            logger.info(
                f"[BILLING] Subscription deactivated: user={user_id} "
                f"stripe_sub={stripe_sub_id}"
            )
