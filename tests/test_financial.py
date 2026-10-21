from src.validators.fact_guard import AntiFabricationGuard

def test_norwegian_number_formats():
    guard = AntiFabricationGuard()

    # Format: 1 234 567,89
    assert guard._verify_digits_in_quote(1234567.89, "Resultatet ble 1 234 567,89 kroner.") is True

    # Format: 1.234.567,-
    assert guard._verify_digits_in_quote(1234567, "Prisen var 1.234.567,- NOK.") is True

    # Format: MNOK
    assert guard._verify_digits_in_quote(1500000, "Inntekt var 1,5 MNOK.") is True
    assert guard._verify_digits_in_quote(15000000, "Inntekt var 15 MNOK.") is True

    # Format: mrd. NOK
    assert guard._verify_digits_in_quote(1500000000, "Omsetningen var 1,5 mrd. NOK.") is True
