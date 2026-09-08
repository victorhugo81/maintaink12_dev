"""
Asset Risk Score — PROJECT_PLAN.md's seven factors (condition, age, failure
frequency, maintenance cost, safety impact, operational importance,
warranty status), each mapped to a 0-100 risk contribution and averaged.

PROJECT_PLAN.md requires this to never be an opaque number: calculate_risk()
returns every factor's raw value and its 0-100 contribution alongside the
final score, and the Capital Replacement view (routes.py/templates) renders
that full breakdown, never just the total.

A factor with no underlying data (e.g. no install_date, no safety_impact
set) is EXCLUDED from the average rather than defaulted to 0 or 50 — a
missing safety rating must never silently read as "safe" (0) or "moderate"
(50). The final score is the mean of whichever factors have real data,
labeled with how many of the 7 were actually available, so a score computed
from 2 factors is visibly less complete than one computed from all 7.

This is rule-based arithmetic over columns already on the Asset/WorkOrder
tables — never an inferred or fabricated judgment, per PROJECT_PLAN.md's
Non-Goals.
"""
from datetime import date, timezone, datetime

FAILURE_SOURCES = ('Request', 'Manual', 'Inspection')  # anything that isn't routine PM
MAINTENANCE_COST_RATIO_CAP = 1.0  # maintenance spend >= 100% of purchase cost -> max risk


def _condition_factor(asset):
    if asset.condition_score is None:
        return None
    return 100 - asset.condition_score


def _age_factor(asset, today):
    expected_life = asset.expected_life_years or (asset.asset_type.expected_life_years if asset.asset_type else None)
    if not asset.install_date or not expected_life:
        return None
    age_years = (today - asset.install_date).days / 365.25
    return min(100, max(0, (age_years / expected_life) * 100))


def _failure_frequency_factor(work_orders):
    count = sum(1 for wo in work_orders if wo.source in FAILURE_SOURCES)
    if count == 0:
        return 0
    if count <= 2:
        return 30
    if count <= 5:
        return 60
    return 100


def _maintenance_cost_factor(asset, work_orders):
    if not asset.purchase_cost or asset.purchase_cost <= 0:
        return None
    total_cost = sum(
        (wo.cost_record.total_cost if wo.cost_record else (wo.actual_cost or 0))
        for wo in work_orders
    )
    ratio = float(total_cost) / float(asset.purchase_cost)
    return min(100, (ratio / MAINTENANCE_COST_RATIO_CAP) * 100)


def _safety_impact_factor(asset):
    return asset.safety_impact  # already 0-100, or None


def _operational_importance_factor(asset):
    return asset.operational_importance  # already 0-100, or None


def _warranty_factor(asset, today):
    if not asset.warranty_expiration:
        return None
    return 100 if asset.warranty_expiration < today else 0


def calculate_risk(asset, work_orders=None, today=None):
    """
    work_orders: the asset's WorkOrders (pass explicitly to avoid a lazy-load
    per asset when scoring a whole list — see routes.cost_rollups's eager
    loading for the same reasoning). Defaults to asset.work_orders if omitted.
    Returns {'score': float|None, 'factors': {name: {'value', 'available'}},
    'factor_count': int} — score is the mean of whichever factors have data
    (equal weight, renormalized by count), None only when every factor is
    missing. 'available' distinguishes "this factor scored 0 risk" from
    "this factor has no data" (both render as `value: None` vs a real 0).
    """
    today = today or datetime.now(timezone.utc).date()
    work_orders = asset.work_orders if work_orders is None else work_orders

    raw = {
        'Condition': _condition_factor(asset),
        'Age': _age_factor(asset, today),
        'Failure Frequency': _failure_frequency_factor(work_orders),
        'Maintenance Cost': _maintenance_cost_factor(asset, work_orders),
        'Safety Impact': _safety_impact_factor(asset),
        'Operational Importance': _operational_importance_factor(asset),
        'Warranty Status': _warranty_factor(asset, today),
    }

    factors = {name: {'value': round(value, 1) if value is not None else None, 'available': value is not None}
               for name, value in raw.items()}
    available = [v for v in raw.values() if v is not None]
    score = round(sum(available) / len(available), 1) if available else None

    return {'score': score, 'factors': factors, 'factor_count': len(available)}
