"""
API routes for the L&T Neural Grid Market Portal.
Provides market clearing prices, bid/ask data, and P2P transaction settlement.
"""
import math
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
import src.derms.fleet as fleet

router = APIRouter(prefix="/api/market", tags=["Market"])


@router.get("/status")
async def market_status():
    """Market platform status and current session info."""
    now = datetime.now(timezone.utc)
    ist = now + timedelta(hours=5, minutes=30)
    sessions = ["Morning (06:00-10:00)", "Day (10:00-14:00)", "Afternoon (14:00-18:00)", "Evening (18:00-22:00)", "Night (22:00-06:00)"]
    h = ist.hour
    session = sessions[0] if h < 10 else sessions[1] if h < 14 else sessions[2] if h < 18 else sessions[3] if h < 22 else sessions[4]

    return {
        "status": "Active",
        "session": session,
        "time_ist": ist.strftime("%H:%M IST"),
        "market_price_inr_kwh": round(random.uniform(4.5, 8.5), 2),
        "cleared_volume_kwh": round(random.uniform(50, 200), 1),
        "participants": random.randint(5, 15),
        "platform": "L&T Neural Grid P2P Energy Market",
    }


@router.get("/bids")
async def get_bids():
    """Active buy/sell bids in the market."""
    ders = fleet.get_all_ders()
    solar = [d for d in ders if "Solar" in d.get("der_type", "") and d.get("current_kw", 0) > 0]
    bids = []
    for i, der in enumerate(solar[:6]):
        surplus = der.get("current_kw", 0) * random.uniform(0.3, 0.6)
        bids.append({
            "bid_id": f"BID-{i+1:03d}",
            "type": "SELL",
            "der_id": der["der_id"],
            "location": der.get("location_name", ""),
            "quantity_kw": round(surplus, 2),
            "price_inr_kwh": round(random.uniform(4.2, 7.8), 2),
            "status": random.choice(["Open", "Open", "Matched"]),
        })
    return {"bids": bids, "count": len(bids)}


@router.get("/transactions")
async def get_transactions():
    """Recent P2P energy transactions."""
    ders = fleet.get_all_ders()
    solar = [d for d in ders if "Solar" in d.get("der_type", "") and d.get("current_kw", 0) > 0]
    now = datetime.now(timezone.utc)
    transactions = []
    for i, der in enumerate(solar[:8]):
        kw = der.get("current_kw", 0) * random.uniform(0.2, 0.5)
        rate = random.uniform(4.5, 7.5)
        transactions.append({
            "tx_id": f"TX-{i+100:04d}",
            "seller_der": der["der_id"],
            "seller_location": der.get("location_name", ""),
            "units_kwh": round(kw * 0.25, 3),
            "rate_per_kwh": round(rate, 2),
            "amount_inr": round(kw * 0.25 * rate, 2),
            "status": random.choice(["Settled", "Settled", "Pending"]),
            "timestamp": (now - timedelta(minutes=random.randint(5, 120))).isoformat(),
        })
    settled = [t for t in transactions if t["status"] == "Settled"]
    return {
        "transactions": transactions,
        "summary": {
            "total_volume_kwh": round(sum(t["units_kwh"] for t in settled), 3),
            "total_value_inr": round(sum(t["amount_inr"] for t in settled), 2),
            "settled_count": len(settled),
            "avg_rate_inr_kwh": round(sum(t["rate_per_kwh"] for t in settled) / max(len(settled), 1), 2),
        },
    }
