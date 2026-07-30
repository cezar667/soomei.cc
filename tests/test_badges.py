from api.domain.badges import BADGE_CATALOG, choose_badge_type, get_badge_definition


def test_badge_catalog_contains_supported_distinctions():
    assert {
        "soomei_connector",
        "founding_member",
        "community_ambassador",
        "community_mentor",
        "verified_partner",
    }.issubset(BADGE_CATALOG)
    assert get_badge_definition("founding_member").label == "Associado Fundador"


def test_badge_choice_preserves_selection_and_has_compatible_fallback():
    available = ["soomei_connector", "founding_member"]

    assert choose_badge_type(available, "founding_member") == "founding_member"
    assert choose_badge_type(available, "expired_badge") == "soomei_connector"
    assert choose_badge_type(["founding_member"], None) == "founding_member"
    assert choose_badge_type([], "founding_member") is None
