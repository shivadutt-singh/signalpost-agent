"""
Unit & Integration Tests for Official Brreg Kunngjøringer (Announcements) Client.
Verifies table row parsing, date normalization, carryover date handling, and permalink generation.
"""

import pytest
from src.collectors.kunngjoringer_client import (
    KunngjoringerClient,
    KunngjoringItem,
    parse_norwegian_kunngjoring_date,
)
from src.models.schema import SourceTier


SAMPLE_KUNNGJORING_HTML = """
<html>
<body>
<div id="pagecontent">
    <table>
        <tr>
            <th>Dato</th>
            <th>Kunngjøringstype</th>
        </tr>
        <tr>
            <td><p>04.09.2026</p></td>
            <td><p><a href="hent_en.jsp?kid=20260000729287&sokeverdi=923609016&spraak=nb">Styre</a></p></td>
        </tr>
        <tr>
            <td><p>18.08.2026</p></td>
            <td><p><a href="hent_en.jsp?kid=20260000698979&sokeverdi=923609016&spraak=nb">Godkjent årsregnskap</a></p></td>
        </tr>
        <tr>
            <td><p>02.07.2026</p></td>
            <td><p><a href="hent_en.jsp?kid=20260000484191&sokeverdi=923609016&spraak=nb">Kapital</a></p></td>
        </tr>
        <tr>
            <td><p></p></td>
            <td><p><a href="hent_en.jsp?kid=20260000484190&sokeverdi=923609016&spraak=nb">Vedtektsdato</a></p></td>
        </tr>
        <tr>
            <td><p>15.01.2025</p></td>
            <td><p><a href="hent_en.jsp?kid=20250000123456&sokeverdi=923609016&spraak=nb">Endring av daglig leder</a></p></td>
        </tr>
    </table>
</div>
</body>
</html>
"""


class TestKunngjoringerClient:
    """Tests parsing and candidate conversion of official Brreg announcements."""

    def test_parse_norwegian_date(self):
        assert parse_norwegian_kunngjoring_date("04.09.2026") == "2026-09-04"
        assert parse_norwegian_kunngjoring_date("18.08.2026") == "2026-08-18"
        assert parse_norwegian_kunngjoring_date("01.01.2025") == "2025-01-01"
        assert parse_norwegian_kunngjoring_date("invalid-date") is None
        assert parse_norwegian_kunngjoring_date("") is None

    def test_parse_kunngjoringer_table_with_date_carryover(self):
        client = KunngjoringerClient(client=None)
        items = client.parse_kunngjoringer_html(SAMPLE_KUNNGJORING_HTML, clean_orgnr="923609016")

        assert len(items) == 5
        # Item 1
        assert items[0].date == "2026-09-04"
        assert items[0].announcement_type == "Styre"
        assert items[0].kid == "20260000729287"
        assert "hent_en.jsp?kid=20260000729287" in items[0].permalink_url

        # Item 2
        assert items[1].date == "2026-08-18"
        assert items[1].announcement_type == "Godkjent årsregnskap"
        assert items[1].kid == "20260000698979"

        # Item 3 & 4 (Item 4 has blank date, should inherit 2026-07-02 from Item 3)
        assert items[2].date == "2026-07-02"
        assert items[2].announcement_type == "Kapital"
        assert items[3].date == "2026-07-02"
        assert items[3].announcement_type == "Vedtektsdato"
        assert items[3].kid == "20260000484190"

        # Item 5
        assert items[4].date == "2025-01-15"
        assert items[4].announcement_type == "Endring av daglig leder"

    def test_to_fact_candidates_conversion(self):
        client = KunngjoringerClient(client=None)
        items = client.parse_kunngjoringer_html(SAMPLE_KUNNGJORING_HTML, clean_orgnr="923609016")
        candidates = client.to_fact_candidates(items, clean_orgnr="923609016")

        assert len(candidates) == 5
        first = candidates[0]
        assert first.fact_key == "operations.corporate_event.20260000729287"
        assert first.value["title"] == "Styre"
        assert first.value["date"] == "2026-09-04"
        assert first.evidence.source_tier == SourceTier.OFFICIAL_REGISTRY
        assert first.evidence.entity_match_rule == "OFFICIAL_BRREG_KUNNGJORING"
        assert first.evidence.entity_confidence == 1.0
        assert first.evidence.raw_digits_verified is True
        assert "2026-09-04" in first.evidence.verbatim_quote
