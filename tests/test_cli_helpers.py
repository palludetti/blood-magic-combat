from blood_magic.cli import (
    _format_hp_bar,
    _sort_spells_for_menu,
    build_spell_preview_label,
)


class TestCliHelpers:
    def test_format_hp_bar_boundaries(self):
        assert _format_hp_bar(100, 100, width=10) == "[##########]"
        assert _format_hp_bar(0, 100, width=10) == "[..........]"
        assert _format_hp_bar(1, 100, width=10) == "[#.........]"

    def test_sort_spells_places_game_spells_before_test_spells(self):
        spells = [
            {"code": "TEST_CEIL_ROUND_V1", "name": "Test Ceil"},
            {"code": "BLOOD_PIN_V1", "name": "Agulha"},
            {"code": "TEST_OVERKILL_V1", "name": "Test Overkill"},
            {"code": "HAEMATIC_SPEAR_V1", "name": "Lanca"},
        ]
        ordered = _sort_spells_for_menu(spells)
        assert [s["code"] for s in ordered] == [
            "BLOOD_PIN_V1",
            "HAEMATIC_SPEAR_V1",
            "TEST_CEIL_ROUND_V1",
            "TEST_OVERKILL_V1",
        ]

    def test_build_spell_preview_label_safe_and_blocked_cases(self):
        actor = {"max_hp": 100, "current_hp": 50, "magic_power": 5}
        target = {"current_hp": 100, "defense": 4}
        spear = {
            "hp_cost_percent": 12,
            "base_damage": 8,
            "blood_multiplier_percent": 150,
        }
        safe_label = build_spell_preview_label(actor, target, spear)
        assert "Custo: 12 HP (12%)" in safe_label
        assert "Dano est.: 27 (efetivo: 27)" in safe_label
        assert "Seu HP apos: 38" in safe_label

        suicidal_actor = {"max_hp": 100, "current_hp": 12, "magic_power": 5}
        blocked_label = build_spell_preview_label(suicidal_actor, target, spear)
        assert "Custo: 12 HP (12%)" in blocked_label
        assert "[BLOQUEADO: LETHAL_BLOOD_COST]" in blocked_label

