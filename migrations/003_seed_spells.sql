-- ============================================================================
-- Migration: 003_seed_spells.sql
-- Description: Immutable catalog of blood magic spells with diverse cost/damage profiles
-- ============================================================================

INSERT INTO public.spells (id, code, name, hp_cost_percent, base_damage, blood_multiplier_percent)
VALUES 
    -- 1. Siphon / Agulha de Sangue: Baixo custo (5%), dano base moderado, baixo multiplicador de sangue
    ('a0000001-0000-0000-0000-000000000001', 'BLOOD_PIN_V1', 'Agulha de Sangue', 5, 15, 80),

    -- 2. Lança Hemática: Feitiço do exemplo obrigatório do documento (custo 12%, base 8, mult 150%)
    ('a0000002-0000-0000-0000-000000000002', 'HAEMATIC_SPEAR_V1', 'Lança Hemática', 12, 8, 150),

    -- 3. Transfusão Carmesim: Custo moderado (20%), alto multiplicador de sangue (200%), dano base equilibrado
    ('a0000003-0000-0000-0000-000000000003', 'CRIMSON_BURST_V1', 'Explosão Carmesim', 20, 25, 200),

    -- 4. Ruptura de Exsangue: Alto custo (40%), dano base massivo (60), multiplicador devastador (300%)
    ('a0000004-0000-0000-0000-000000000004', 'EXSANGUINATION_V1', 'Ruptura de Exsangue', 40, 60, 300)
ON CONFLICT (code) DO NOTHING;

