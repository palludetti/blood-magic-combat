-- ============================================================================
-- Migration: 001_initial_schema.sql
-- Description: Core tables, constraints, triggers, indexes, and RLS policies
-- ============================================================================

-- Ensure pgcrypto extension for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ----------------------------------------------------------------------------
-- 1. Table: characters
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.characters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    name text NOT NULL,
    max_hp integer NOT NULL,
    current_hp integer NOT NULL,
    magic_power integer NOT NULL,
    defense integer NOT NULL,
    initiative integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_character_name CHECK (char_length(trim(name)) BETWEEN 1 AND 80),
    CONSTRAINT chk_character_max_hp CHECK (max_hp BETWEEN 1 AND 1000000),
    CONSTRAINT chk_character_current_hp CHECK (current_hp BETWEEN 0 AND max_hp),
    CONSTRAINT chk_character_magic_power CHECK (magic_power BETWEEN 0 AND 1000000),
    CONSTRAINT chk_character_defense CHECK (defense BETWEEN 0 AND 1000000),
    CONSTRAINT chk_character_initiative CHECK (initiative BETWEEN 0 AND 1000000)
);

-- ----------------------------------------------------------------------------
-- 2. Table: spells
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.spells (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    hp_cost_percent integer NOT NULL,
    base_damage integer NOT NULL,
    blood_multiplier_percent integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_spell_code CHECK (char_length(trim(code)) BETWEEN 1 AND 64),
    CONSTRAINT chk_spell_name CHECK (char_length(trim(name)) BETWEEN 1 AND 80),
    CONSTRAINT chk_spell_hp_cost_percent CHECK (hp_cost_percent BETWEEN 1 AND 100),
    CONSTRAINT chk_spell_base_damage CHECK (base_damage BETWEEN 0 AND 1000000),
    CONSTRAINT chk_spell_blood_multiplier_percent CHECK (blood_multiplier_percent BETWEEN 0 AND 10000)
);

-- ----------------------------------------------------------------------------
-- 3. Table: combats (Forward declaration for participants)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.combats (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'active',
    turn_number integer NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 0,
    current_turn_participant_id uuid,
    winner_participant_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    CONSTRAINT chk_combat_status CHECK (status IN ('active', 'finished', 'cancelled')),
    CONSTRAINT chk_combat_turn_number CHECK (turn_number >= 1),
    CONSTRAINT chk_combat_version CHECK (version >= 0),
    CONSTRAINT chk_combat_active_state CHECK (
        (status = 'active' AND current_turn_participant_id IS NOT NULL AND winner_participant_id IS NULL AND ended_at IS NULL) OR
        (status = 'finished' AND current_turn_participant_id IS NULL AND winner_participant_id IS NOT NULL AND ended_at IS NOT NULL) OR
        (status = 'cancelled' AND current_turn_participant_id IS NULL AND winner_participant_id IS NULL AND ended_at IS NOT NULL)
    )
);

-- ----------------------------------------------------------------------------
-- 4. Table: combat_participants
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.combat_participants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    combat_id uuid NOT NULL REFERENCES public.combats(id) ON DELETE RESTRICT,
    character_id uuid NOT NULL REFERENCES public.characters(id) ON DELETE RESTRICT,
    slot smallint NOT NULL,
    max_hp integer NOT NULL,
    current_hp integer NOT NULL,
    magic_power integer NOT NULL,
    defense integer NOT NULL,
    initiative integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_participant_slot CHECK (slot IN (1, 2)),
    CONSTRAINT chk_participant_max_hp CHECK (max_hp BETWEEN 1 AND 1000000),
    CONSTRAINT chk_participant_current_hp CHECK (current_hp BETWEEN 0 AND max_hp),
    CONSTRAINT chk_participant_magic_power CHECK (magic_power BETWEEN 0 AND 1000000),
    CONSTRAINT chk_participant_defense CHECK (defense BETWEEN 0 AND 1000000),
    CONSTRAINT chk_participant_initiative CHECK (initiative BETWEEN 0 AND 1000000),
    CONSTRAINT uq_participant_combat_character UNIQUE (combat_id, character_id),
    CONSTRAINT uq_participant_combat_slot UNIQUE (combat_id, slot),
    CONSTRAINT uq_participant_combat_id UNIQUE (combat_id, id)
);

-- Add composite foreign keys on combats to combat_participants
ALTER TABLE public.combats
    ADD CONSTRAINT fk_combat_current_turn_participant
    FOREIGN KEY (id, current_turn_participant_id)
    REFERENCES public.combat_participants(combat_id, id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE public.combats
    ADD CONSTRAINT fk_combat_winner_participant
    FOREIGN KEY (id, winner_participant_id)
    REFERENCES public.combat_participants(combat_id, id)
    DEFERRABLE INITIALLY DEFERRED;

-- ----------------------------------------------------------------------------
-- 5. Table: combat_actions
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.combat_actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    combat_id uuid NOT NULL REFERENCES public.combats(id) ON DELETE RESTRICT,
    request_id uuid NOT NULL,
    expected_version bigint NOT NULL,
    turn_number integer NOT NULL,
    actor_participant_id uuid NOT NULL,
    target_participant_id uuid NOT NULL,
    spell_id uuid NOT NULL REFERENCES public.spells(id) ON DELETE RESTRICT,
    hp_cost integer NOT NULL,
    raw_damage bigint NOT NULL,
    calculated_damage bigint NOT NULL,
    effective_damage integer NOT NULL,
    actor_hp_before integer NOT NULL,
    actor_hp_after integer NOT NULL,
    target_hp_before integer NOT NULL,
    target_hp_after integer NOT NULL,
    resulting_version bigint NOT NULL,
    resulting_status text NOT NULL,
    next_turn_participant_id uuid,
    winner_participant_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_action_expected_version CHECK (expected_version >= 0),
    CONSTRAINT chk_action_turn_number CHECK (turn_number >= 1),
    CONSTRAINT chk_action_hp_cost CHECK (hp_cost >= 1),
    CONSTRAINT chk_action_raw_damage CHECK (raw_damage >= 0),
    CONSTRAINT chk_action_calculated_damage CHECK (calculated_damage >= 0),
    CONSTRAINT chk_action_effective_damage CHECK (effective_damage >= 0),
    CONSTRAINT chk_action_resulting_version CHECK (resulting_version = expected_version + 1),
    CONSTRAINT chk_action_resulting_status CHECK (resulting_status IN ('active', 'finished')),
    CONSTRAINT uq_action_combat_request UNIQUE (combat_id, request_id),
    CONSTRAINT uq_action_combat_turn UNIQUE (combat_id, turn_number),
    CONSTRAINT uq_action_combat_version UNIQUE (combat_id, resulting_version),
    CONSTRAINT chk_action_actor_not_target CHECK (actor_participant_id <> target_participant_id),
    CONSTRAINT chk_action_non_suicide CHECK (actor_hp_before > hp_cost),
    CONSTRAINT chk_action_actor_hp_after CHECK (actor_hp_after = actor_hp_before - hp_cost),
    CONSTRAINT chk_action_target_alive_before CHECK (target_hp_before > 0),
    CONSTRAINT chk_action_target_hp_after CHECK (target_hp_after = target_hp_before - effective_damage),
    CONSTRAINT chk_action_effective_damage_formula CHECK (effective_damage = LEAST(target_hp_before, calculated_damage::integer)),
    CONSTRAINT chk_action_actor_hp_after_non_negative CHECK (actor_hp_after >= 0),
    CONSTRAINT chk_action_target_hp_after_non_negative CHECK (target_hp_after >= 0),
    CONSTRAINT chk_action_resulting_state_consistency CHECK (
        (resulting_status = 'active' AND next_turn_participant_id IS NOT NULL AND winner_participant_id IS NULL) OR
        (resulting_status = 'finished' AND next_turn_participant_id IS NULL AND winner_participant_id = actor_participant_id)
    )
);

-- Composite foreign keys for combat_actions
ALTER TABLE public.combat_actions
    ADD CONSTRAINT fk_action_actor_participant
    FOREIGN KEY (combat_id, actor_participant_id)
    REFERENCES public.combat_participants(combat_id, id);

ALTER TABLE public.combat_actions
    ADD CONSTRAINT fk_action_target_participant
    FOREIGN KEY (combat_id, target_participant_id)
    REFERENCES public.combat_participants(combat_id, id);

ALTER TABLE public.combat_actions
    ADD CONSTRAINT fk_action_next_turn_participant
    FOREIGN KEY (combat_id, next_turn_participant_id)
    REFERENCES public.combat_participants(combat_id, id);

ALTER TABLE public.combat_actions
    ADD CONSTRAINT fk_action_winner_participant
    FOREIGN KEY (combat_id, winner_participant_id)
    REFERENCES public.combat_participants(combat_id, id);

-- ----------------------------------------------------------------------------
-- 6. Triggers for Constraints and Business Integrity
-- ----------------------------------------------------------------------------

-- A) Exactly two participants per combat (DEFERRED CONSTRAINT TRIGGER)
CREATE OR REPLACE FUNCTION public.check_combat_participants_count()
RETURNS trigger AS $$
DECLARE
    cnt integer;
    v_target_combat_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_target_combat_id := OLD.combat_id;
    ELSE
        v_target_combat_id := NEW.combat_id;
    END IF;

    SELECT count(*) INTO cnt
    FROM public.combat_participants
    WHERE combat_id = v_target_combat_id;

    IF cnt <> 2 THEN
        RAISE EXCEPTION 'INVALID_PARTICIPANT: Combat % must have exactly 2 participants, found %', v_target_combat_id, cnt;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_combat_participants_count ON public.combat_participants;
CREATE CONSTRAINT TRIGGER trg_combat_participants_count
AFTER INSERT OR UPDATE OR DELETE ON public.combat_participants
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION public.check_combat_participants_count();

-- B) Exclusivity: Character can only participate in AT MOST ONE ACTIVE combat at any time
-- Trigger on combat_participants:
CREATE OR REPLACE FUNCTION public.check_character_active_combat_exclusivity()
RETURNS trigger AS $$
DECLARE
    active_combat_count integer;
    target_character_id uuid;
BEGIN
    target_character_id := NEW.character_id;

    SELECT count(*) INTO active_combat_count
    FROM public.combat_participants cp
    JOIN public.combats c ON c.id = cp.combat_id
    WHERE cp.character_id = target_character_id
      AND c.status = 'active';

    IF active_combat_count > 1 THEN
        RAISE EXCEPTION 'CHARACTER_ALREADY_IN_COMBAT: Character % is already participating in an active combat', target_character_id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_character_active_combat_exclusivity ON public.combat_participants;
CREATE CONSTRAINT TRIGGER trg_character_active_combat_exclusivity
AFTER INSERT OR UPDATE ON public.combat_participants
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION public.check_character_active_combat_exclusivity();

-- Exclusivity trigger on combats: when a combat status changes to 'active'
CREATE OR REPLACE FUNCTION public.check_combat_status_active_exclusivity()
RETURNS trigger AS $$
DECLARE
    v_conflicting_char_id uuid;
BEGIN
    IF NEW.status = 'active' AND (TG_OP = 'INSERT' OR OLD.status <> 'active') THEN
        SELECT cp.character_id INTO v_conflicting_char_id
        FROM public.combat_participants cp
        JOIN public.combats c ON c.id = cp.combat_id
        WHERE cp.character_id IN (
            SELECT character_id FROM public.combat_participants WHERE combat_id = NEW.id
        )
        AND c.status = 'active'
        AND c.id <> NEW.id
        LIMIT 1;

        IF v_conflicting_char_id IS NOT NULL THEN
            RAISE EXCEPTION 'CHARACTER_ALREADY_IN_COMBAT: Character % is already participating in an active combat', v_conflicting_char_id;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_combat_status_active_exclusivity ON public.combats;
CREATE CONSTRAINT TRIGGER trg_combat_status_active_exclusivity
AFTER INSERT OR UPDATE ON public.combats
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION public.check_combat_status_active_exclusivity();

-- C) Automatic updated_at trigger
CREATE OR REPLACE FUNCTION public.update_timestamp_column()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_characters_updated_at ON public.characters;
CREATE TRIGGER trg_characters_updated_at
BEFORE UPDATE ON public.characters
FOR EACH ROW
EXECUTE FUNCTION public.update_timestamp_column();

DROP TRIGGER IF EXISTS trg_combats_updated_at ON public.combats;
CREATE TRIGGER trg_combats_updated_at
BEFORE UPDATE ON public.combats
FOR EACH ROW
EXECUTE FUNCTION public.update_timestamp_column();

-- D) Immutability of combat_actions (Prohibit UPDATE and DELETE)
CREATE OR REPLACE FUNCTION public.prevent_action_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'FORBIDDEN: Combat action log is immutable and cannot be updated or deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_combat_actions_immutable ON public.combat_actions;
CREATE TRIGGER trg_combat_actions_immutable
BEFORE UPDATE OR DELETE ON public.combat_actions
FOR EACH ROW
EXECUTE FUNCTION public.prevent_action_mutation();

-- E) Immutability of spells (Catalog cannot be updated or deleted)
CREATE OR REPLACE FUNCTION public.prevent_spell_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'FORBIDDEN: Spells catalog is immutable. Create new spell codes for balance adjustments.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_spells_immutable ON public.spells;
CREATE TRIGGER trg_spells_immutable
BEFORE UPDATE OR DELETE ON public.spells
FOR EACH ROW
EXECUTE FUNCTION public.prevent_spell_mutation();

-- ----------------------------------------------------------------------------
-- 7. Indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_characters_owner_id ON public.characters(owner_id);
CREATE INDEX IF NOT EXISTS idx_combats_owner_status ON public.combats(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_combat_participants_character_id ON public.combat_participants(character_id);
CREATE INDEX IF NOT EXISTS idx_combat_participants_combat_id ON public.combat_participants(combat_id);
CREATE INDEX IF NOT EXISTS idx_combat_actions_combat_id ON public.combat_actions(combat_id);

-- ----------------------------------------------------------------------------
-- 8. Explicit Privileges and Revocations
-- ----------------------------------------------------------------------------
-- Explicitly revoke direct write privileges on all domain tables from PUBLIC, anon, and authenticated
REVOKE ALL ON TABLE public.characters FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.spells FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.combats FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.combat_participants FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.combat_actions FROM PUBLIC, anon, authenticated;

-- Grant SELECT only to authenticated users (reads governed by RLS)
GRANT SELECT ON TABLE public.characters TO authenticated;
GRANT SELECT ON TABLE public.spells TO authenticated;
GRANT SELECT ON TABLE public.combats TO authenticated;
GRANT SELECT ON TABLE public.combat_participants TO authenticated;
GRANT SELECT ON TABLE public.combat_actions TO authenticated;

-- ----------------------------------------------------------------------------
-- 9. Row Level Security (RLS)
-- ----------------------------------------------------------------------------
ALTER TABLE public.characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.spells ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.combats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.combat_participants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.combat_actions ENABLE ROW LEVEL SECURITY;

-- Spells: Read-only for authenticated users
DROP POLICY IF EXISTS p_spells_select ON public.spells;
CREATE POLICY p_spells_select ON public.spells
    FOR SELECT TO authenticated
    USING (true);

-- Characters: Authenticated users can read only their own characters
DROP POLICY IF EXISTS p_characters_select ON public.characters;
CREATE POLICY p_characters_select ON public.characters
    FOR SELECT TO authenticated
    USING (owner_id = auth.uid());

-- Combats: Authenticated users can read only their own combats
DROP POLICY IF EXISTS p_combats_select ON public.combats;
CREATE POLICY p_combats_select ON public.combats
    FOR SELECT TO authenticated
    USING (owner_id = auth.uid());

-- Combat Participants: Read allowed if user owns the combat
DROP POLICY IF EXISTS p_combat_participants_select ON public.combat_participants;
CREATE POLICY p_combat_participants_select ON public.combat_participants
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.combats c
            WHERE c.id = combat_participants.combat_id
              AND c.owner_id = auth.uid()
        )
    );

-- Combat Actions: Read allowed if user owns the combat
DROP POLICY IF EXISTS p_combat_actions_select ON public.combat_actions;
CREATE POLICY p_combat_actions_select ON public.combat_actions
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.combats c
            WHERE c.id = combat_actions.combat_id
              AND c.owner_id = auth.uid()
        )
    );
