"""Fare estimates and consistent monetary rounding."""
import math
from decimal import Decimal, ROUND_HALF_UP
from app.core.config import settings

def round_money(value: Decimal | float) -> float:
  """Round a fare to two decimal places using half-up rounding."""
  return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def price_estimator(distance: float) -> float:
  """Estimate NGN fare using started 100-metre bands and the minimum fare."""
  if not math.isfinite(distance) or distance < 0:
    raise ValueError("Distance must be finite and nonnegative")
  if distance == 0:
    return 0.0
  hundreds_count = math.ceil(distance / 100)
  trip_price = max(settings.BASE_PRICE * hundreds_count, settings.STANDARD_PRICE)
  return round_money(trip_price)
