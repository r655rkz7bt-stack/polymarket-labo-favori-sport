"""Domaine de la source de résolution (filtre `sources` des variantes)."""

from polymarket_agent import resolution_sources as rs


def test_domain_parsing_is_robust():
    assert rs.domain_of("WWW.NHL.com/scores") == "nhl.com"
    assert rs.domain_of("https://user@statsapi.mlb.com:443/x") == "statsapi.mlb.com"
    assert rs.domain_of("") == "" and rs.domain_of("   ") == ""
