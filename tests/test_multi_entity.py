from src.validators.fact_guard import EntityVerificationGate, EntityAnchor

def test_multi_entity_directory_rejection():
    anchor = EntityAnchor(
        orgnr="923609016",
        legal_name="EQUINOR ASA",
        municipality="STAVANGER",
    )

    # 4 distinct orgnrs
    raw_text = """
    Velkommen til bedriftskatalogen.
    Her finner du Equinor ASA i Stavanger.
    Andre bedrifter:
    Telenor ASA orgnr 982463718
    DNB Bank ASA orgnr 984851006
    Posten Bring AS orgnr 984661185
    Aker ASA orgnr 886581432
    """

    res = EntityVerificationGate.verify_entity_ownership(
        anchor=anchor,
        source_url="https://katalog.no",
        raw_source_text=raw_text
    )

    assert res.is_verified is False
    assert res.rule == "MULTI_ENTITY_DIRECTORY_REJECTION"
    assert "Disqualified for Wrong-Company Bleed" in res.explanation
