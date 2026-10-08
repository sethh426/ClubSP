"""Sensitivity invariants for the actual rental brief; no provider/network requests."""
from itertools import product
from decimal import Decimal, ROUND_FLOOR
from app.acquisition_briefs import DEFAULTS, economics, decision, enrich_card


def test_rental_stress_envelope_preserves_math_and_price_qualification():
    for price, rent, repairs, vacancy, expenses in product(
        (50000,100000,150000,250000,500000), (500,1000,2000,4000),
        (0,10000,50000,100000), (0,8,20,50), (0,30,60,90)):
        assumptions = {**DEFAULTS, 'max_price':1000000,'repair_reserve':repairs,
                       'vacancy_pct':vacancy,'expense_pct':expenses,'min_yield_pct':5}
        model = economics(price,rent,assumptions)
        # Independent Decimal identities rather than replaying app calculations.
        basis = Decimal(price)*Decimal('1.03')+Decimal(repairs)
        annual = Decimal(rent)*12*(1-Decimal(vacancy)/100)*(1-Decimal(expenses)/100)
        assert abs(Decimal(str(model['cash_basis']))-basis) <= Decimal('.005')
        assert abs(Decimal(str(model['annual_operating_income']))-annual) <= Decimal('.005')
        card = enrich_card({'listing':{'asking_price':price},'economics':model},assumptions)
        ceiling = max(0,int(((annual/Decimal('.05')-repairs)/Decimal('1.03')).to_integral_value(rounding=ROUND_FLOOR)))
        assert abs(card['decision']['price_ceiling']-ceiling) <= 1  # Dollar flooring around float boundaries.
        if card['decision']['status']=='review_candidate':
            assert annual/basis >= Decimal('.05')
            assert card['screen']=='meets_assumed_yield'
        assert economics(price,rent/2,assumptions)['yield_pct'] <= model['yield_pct']
        assert economics(price,rent,{**assumptions,'repair_reserve':repairs+10000})['yield_pct'] <= model['yield_pct']
