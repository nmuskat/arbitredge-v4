import pytest
from collectors.odds_collector import OddsCollector


def test_valid_over_under():
    valid, reason, data = OddsCollector._validate_total_outcomes([
        {"name": "Over", "point": 44.5, "price": -110},
        {"name": "Under", "point": 44.5, "price": -110},
    ])
    assert valid, reason
    assert data == {
        "total_points": 44.5,
        "total_price_over": -110.0,
        "total_price_under": -110.0,
    }


@pytest.mark.parametrize("outcomes", [
    [{"name": "Over", "point": 44.5, "price": -110}],
    [
        {"name": "Over", "point": 44.5, "price": -110},
        {"name": "Over", "point": 44.5, "price": -110},
    ],
    [
        {"name": "Over", "point": 44.5, "price": -110},
        {"name": "Under", "point": 45.5, "price": -110},
    ],
    [
        {"name": "Over", "point": 44.5, "price": -50},
        {"name": "Under", "point": 44.5, "price": -110},
    ],
    [
        {"name": "Over", "point": None, "price": -110},
        {"name": "Under", "point": None, "price": -110},
    ],
])
def test_invalid_over_under(outcomes):
    valid, _, data = OddsCollector._validate_total_outcomes(outcomes)
    assert not valid
    assert data is None
