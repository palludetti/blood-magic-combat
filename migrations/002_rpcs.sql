-- ============================================================================
-- Migration: 002_rpcs.sql
-- Description: Transactional RPC functions for character creation, combat start,
--              cast spell (with FOR UPDATE, idempotency, pure math validation),
--              and cancellation.
--
-- GLOBAL LOCK ORDER & CONCURRENCY HIERARCHY:
-- 1. fn_start_combat:
--    Locks characters(id) in ASCENDING UUID order FOR UPDATE -> Inserts combat -> Inserts participants.
-- 2. fn_cast_spell:
--    Locks combats(id) FOR UPDATE (aggregate root serialization) ->
--    Locks combat_participants(id) in ASCENDING UUID order FOR UPDATE ->
--    Locks characters(id) in ASCENDING UUID order FOR UPDATE (HP sync).
-- 3. fn_cancel_combat:
--    Follows the exact same hierarchy as fn_cast_spell.
--
-- LIMITATIONS & CAVEATS:
-- Ordering locks by ascending UUID prevents circular lock-wait deadlocks between
-- concurrent application transactions touching the same pair of records.
-- However, it does NOT eliminate all deadlocks in PostgreSQL:
-- - Concurrent DDL / administrative operations (ALTER TABLE, VACUUM FULL, REINDEX)
-- - Foreign key check locks acquired internally in non-application order
-- - Concurrent B-Tree index page split locks under extreme throughput
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Pure Math SQL Function: fn_calculate_blood_magic
-- Used identically by fn_cast_spell and test validations.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_calculate_blood_magic(
    p_caster_max_hp integer,
    p_caster_current_hp integer,
    p_caster_magic_power integer,
    p_target_current_hp integer,
    p_target_defense integer,
    p_spell_hp_cost_percent integer,
    p_spell_base_damage integer,
    p_spell_blood_multiplier_percent integer
)
RETURNS TABLE (
    hp_cost integer,
    raw_damage bigint,
    calculated_damage bigint,
    effective_damage integer,
    actor_hp_after integer,
    target_hp_after integer
)
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_hp_cost bigint;
    v_raw_damage bigint;
    v_calculated_damage bigint;
    v_effective_damage bigint;
BEGIN
    IF p_caster_current_hp <= 0 THEN
        RAISE EXCEPTION 'CHARACTER_DEAD: Caster is dead and cannot cast spells';
    END IF;

    IF p_target_current_hp <= 0 THEN
        RAISE EXCEPTION 'CHARACTER_DEAD: Target is already dead';
    END IF;

    -- C = max(1, (HP_max * p + 99) / 100)
    v_hp_cost := GREATEST(1::bigint, ((p_caster_max_hp::bigint * p_spell_hp_cost_percent::bigint + 99::bigint) / 100::bigint));

    -- Strict non-suicide policy:
    -- If current_hp < C: INSUFFICIENT_HP
    -- If current_hp == C: LETHAL_BLOOD_COST
    -- Only permit if current_hp > C
    IF p_caster_current_hp < v_hp_cost THEN
        RAISE EXCEPTION 'INSUFFICIENT_HP: Cost is %, but caster HP is %', v_hp_cost, p_caster_current_hp;
    END IF;

    IF p_caster_current_hp = v_hp_cost THEN
        RAISE EXCEPTION 'LETHAL_BLOOD_COST: Cost % equals current HP %. Suicidal cast rejected.', v_hp_cost, p_caster_current_hp;
    END IF;

    -- Raw damage: D_bruto = B + P + floor(C * m / 100)
    v_raw_damage := p_spell_base_damage::bigint + p_caster_magic_power::bigint + ((v_hp_cost * p_spell_blood_multiplier_percent::bigint) / 100::bigint);

    -- Calculated damage: max(0, D_bruto - R)
    v_calculated_damage := GREATEST(0::bigint, v_raw_damage - p_target_defense::bigint);

    -- Effective damage: min(HP_alvo_antes, D_calculado)
    v_effective_damage := LEAST(p_target_current_hp::bigint, v_calculated_damage);

    RETURN QUERY SELECT
        v_hp_cost::integer,
        v_raw_damage,
        v_calculated_damage,
        v_effective_damage::integer,
        (p_caster_current_hp::bigint - v_hp_cost)::integer,
        (p_target_current_hp::bigint - v_effective_damage)::integer;
END;
$$;

-- ----------------------------------------------------------------------------
-- RPC 1: fn_create_character
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_create_character(
    p_name text,
    p_max_hp integer,
    p_current_hp integer,
    p_magic_power integer,
    p_defense integer,
    p_initiative integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_user_id uuid;
    v_character public.characters%ROWTYPE;
BEGIN
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'UNAUTHENTICATED: Authentication required';
    END IF;

    IF p_name IS NULL OR char_length(trim(p_name)) NOT BETWEEN 1 AND 80 THEN
        RAISE EXCEPTION 'INVALID_CHARACTER_NAME: Name must be between 1 and 80 characters';
    END IF;

    IF p_max_hp < 1 OR p_max_hp > 1000000 THEN
        RAISE EXCEPTION 'INVALID_ATTRIBUTE: max_hp must be between 1 and 1000000';
    END IF;

    IF p_current_hp < 0 OR p_current_hp > p_max_hp THEN
        RAISE EXCEPTION 'INVALID_ATTRIBUTE: current_hp must be between 0 and max_hp';
    END IF;

    IF p_magic_power < 0 OR p_magic_power > 1000000 THEN
        RAISE EXCEPTION 'INVALID_ATTRIBUTE: magic_power must be between 0 and 1000000';
    END IF;

    IF p_defense < 0 OR p_defense > 1000000 THEN
        RAISE EXCEPTION 'INVALID_ATTRIBUTE: defense must be between 0 and 1000000';
    END IF;

    IF p_initiative < 0 OR p_initiative > 1000000 THEN
        RAISE EXCEPTION 'INVALID_ATTRIBUTE: initiative must be between 0 and 1000000';
    END IF;

    INSERT INTO public.characters (
        owner_id, name, max_hp, current_hp, magic_power, defense, initiative
    ) VALUES (
        v_user_id, trim(p_name), p_max_hp, p_current_hp, p_magic_power, p_defense, p_initiative
    )
    RETURNING * INTO v_character;

    RETURN to_jsonb(v_character);
END;
$$;

-- ----------------------------------------------------------------------------
-- RPC 2: fn_start_combat
-- Lock order: characters (ordered by character_id) -> combats -> combat_participants
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_start_combat(
    p_character_a_id uuid,
    p_character_b_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_user_id uuid;
    v_char_first_id uuid;
    v_char_second_id uuid;
    v_char_a public.characters%ROWTYPE;
    v_char_b public.characters%ROWTYPE;
    v_combat_id uuid;
    v_participant_1_id uuid;
    v_participant_2_id uuid;
    v_starter_participant_id uuid;
    v_conflict_count integer;
    v_res jsonb;
BEGIN
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'UNAUTHENTICATED: Authentication required';
    END IF;

    IF p_character_a_id IS NULL OR p_character_b_id IS NULL OR p_character_a_id = p_character_b_id THEN
        RAISE EXCEPTION 'INVALID_PARTICIPANT: Two distinct characters are required';
    END IF;

    -- Consistent lock ordering to prevent deadlocks across concurrent requests:
    -- Characters are always locked in ascending UUID order
    IF p_character_a_id < p_character_b_id THEN
        v_char_first_id := p_character_a_id;
        v_char_second_id := p_character_b_id;
    ELSE
        v_char_first_id := p_character_b_id;
        v_char_second_id := p_character_a_id;
    END IF;

    -- Lock both characters FOR UPDATE
    PERFORM 1 FROM public.characters WHERE id = v_char_first_id FOR UPDATE;
    PERFORM 1 FROM public.characters WHERE id = v_char_second_id FOR UPDATE;

    SELECT * INTO v_char_a FROM public.characters WHERE id = p_character_a_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'CHARACTER_NOT_FOUND: Character % not found', p_character_a_id;
    END IF;

    SELECT * INTO v_char_b FROM public.characters WHERE id = p_character_b_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'CHARACTER_NOT_FOUND: Character % not found', p_character_b_id;
    END IF;

    -- Ownership check: both characters must belong to the authenticated user
    IF v_char_a.owner_id <> v_user_id OR v_char_b.owner_id <> v_user_id THEN
        RAISE EXCEPTION 'FORBIDDEN: You do not own both characters';
    END IF;

    -- Both characters must be alive
    IF v_char_a.current_hp <= 0 OR v_char_b.current_hp <= 0 THEN
        RAISE EXCEPTION 'CHARACTER_DEAD: Both characters must be alive to start combat';
    END IF;

    -- Check if either character is already in an active combat
    SELECT count(*) INTO v_conflict_count
    FROM public.combat_participants cp
    JOIN public.combats c ON c.id = cp.combat_id
    WHERE cp.character_id IN (p_character_a_id, p_character_b_id)
      AND c.status = 'active';

    IF v_conflict_count > 0 THEN
        RAISE EXCEPTION 'CHARACTER_ALREADY_IN_COMBAT: One or both characters are already in an active combat';
    END IF;

    v_combat_id := gen_random_uuid();
    v_participant_1_id := gen_random_uuid();
    v_participant_2_id := gen_random_uuid();

    -- Determine initiative
    -- Highest initiative begins; if tied, smaller character UUID in textual order begins
    IF v_char_a.initiative > v_char_b.initiative THEN
        v_starter_participant_id := v_participant_1_id;
    ELSIF v_char_b.initiative > v_char_a.initiative THEN
        v_starter_participant_id := v_participant_2_id;
    ELSE
        IF (v_char_a.id::text) < (v_char_b.id::text) THEN
            v_starter_participant_id := v_participant_1_id;
        ELSE
            v_starter_participant_id := v_participant_2_id;
        END IF;
    END IF;

    -- Insert combat row (deferred constraints allow participants to be inserted next)
    INSERT INTO public.combats (
        id, owner_id, status, turn_number, version, current_turn_participant_id, winner_participant_id, ended_at
    ) VALUES (
        v_combat_id, v_user_id, 'active', 1, 0, v_starter_participant_id, NULL, NULL
    );

    -- Insert participant 1 (character A snapshot)
    INSERT INTO public.combat_participants (
        id, combat_id, character_id, slot, max_hp, current_hp, magic_power, defense, initiative
    ) VALUES (
        v_participant_1_id, v_combat_id, v_char_a.id, 1,
        v_char_a.max_hp, v_char_a.current_hp, v_char_a.magic_power, v_char_a.defense, v_char_a.initiative
    );

    -- Insert participant 2 (character B snapshot)
    INSERT INTO public.combat_participants (
        id, combat_id, character_id, slot, max_hp, current_hp, magic_power, defense, initiative
    ) VALUES (
        v_participant_2_id, v_combat_id, v_char_b.id, 2,
        v_char_b.max_hp, v_char_b.current_hp, v_char_b.magic_power, v_char_b.defense, v_char_b.initiative
    );

    SELECT jsonb_build_object(
        'combat_id', v_combat_id,
        'owner_id', v_user_id,
        'status', 'active',
        'turn_number', 1,
        'version', 0,
        'current_turn_participant_id', v_starter_participant_id,
        'participant_1', jsonb_build_object(
            'id', v_participant_1_id,
            'character_id', v_char_a.id,
            'name', v_char_a.name,
            'slot', 1,
            'current_hp', v_char_a.current_hp,
            'max_hp', v_char_a.max_hp
        ),
        'participant_2', jsonb_build_object(
            'id', v_participant_2_id,
            'character_id', v_char_b.id,
            'name', v_char_b.name,
            'slot', 2,
            'current_hp', v_char_b.current_hp,
            'max_hp', v_char_b.max_hp
        )
    ) INTO v_res;

    RETURN v_res;
END;
$$;

-- ----------------------------------------------------------------------------
-- RPC 3: fn_cast_spell
-- Lock order: combats (FOR UPDATE) -> combat_participants (ordered by id) -> characters (ordered by id)
-- Uses: public.fn_calculate_blood_magic
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_cast_spell(
    p_combat_id uuid,
    p_request_id uuid,
    p_expected_version bigint,
    p_actor_participant_id uuid,
    p_target_participant_id uuid,
    p_spell_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_user_id uuid;
    v_combat public.combats%ROWTYPE;
    v_existing_action public.combat_actions%ROWTYPE;
    v_part_first_id uuid;
    v_part_second_id uuid;
    v_actor public.combat_participants%ROWTYPE;
    v_target public.combat_participants%ROWTYPE;
    v_spell public.spells%ROWTYPE;
    v_char_first_id uuid;
    v_char_second_id uuid;

    -- Pure calculation variables from fn_calculate_blood_magic
    v_calc RECORD;

    v_resulting_version bigint;
    v_resulting_status text;
    v_next_turn_participant_id uuid;
    v_winner_participant_id uuid;
    v_action_id uuid;
    v_now timestamptz;
BEGIN
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'UNAUTHENTICATED: Authentication required';
    END IF;

    IF p_combat_id IS NULL OR p_request_id IS NULL OR p_expected_version IS NULL OR
       p_actor_participant_id IS NULL OR p_target_participant_id IS NULL OR p_spell_id IS NULL THEN
        RAISE EXCEPTION 'INVALID_ARGUMENT: All parameters are required';
    END IF;

    -- 1. Lock combat row FOR UPDATE (serialized access per combat)
    SELECT * INTO v_combat
    FROM public.combats
    WHERE id = p_combat_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'COMBAT_NOT_FOUND: Combat % not found', p_combat_id;
    END IF;

    IF v_combat.owner_id <> v_user_id THEN
        RAISE EXCEPTION 'FORBIDDEN: You do not own this combat';
    END IF;

    -- 2. Idempotency Check: check if request_id has already been processed for this combat
    SELECT * INTO v_existing_action
    FROM public.combat_actions
    WHERE combat_id = p_combat_id
      AND request_id = p_request_id;

    IF FOUND THEN
        -- If payload matches exactly, return original action results idempotently,
        -- reconstructed directly from the immutable combat_actions row.
        IF v_existing_action.actor_participant_id = p_actor_participant_id AND
           v_existing_action.target_participant_id = p_target_participant_id AND
           v_existing_action.spell_id = p_spell_id AND
           v_existing_action.expected_version = p_expected_version THEN
            RETURN jsonb_build_object(
                'action_id', v_existing_action.id,
                'hp_cost', v_existing_action.hp_cost,
                'raw_damage', v_existing_action.raw_damage,
                'calculated_damage', v_existing_action.calculated_damage,
                'effective_damage', v_existing_action.effective_damage,
                'actor_hp_before', v_existing_action.actor_hp_before,
                'actor_hp_after', v_existing_action.actor_hp_after,
                'target_hp_before', v_existing_action.target_hp_before,
                'target_hp_after', v_existing_action.target_hp_after,
                'turn_number', v_existing_action.turn_number,
                'resulting_version', v_existing_action.resulting_version,
                'resulting_status', v_existing_action.resulting_status,
                'next_turn_participant_id', v_existing_action.next_turn_participant_id,
                'winner_participant_id', v_existing_action.winner_participant_id,
                'is_idempotent_replay', true
            );
        ELSE
            -- Request ID reused with differing payload
            RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT: request_id % already used with different parameters', p_request_id;
        END IF;
    END IF;

    -- 3. Combat state and version validation for new requests
    IF v_combat.status <> 'active' THEN
        RAISE EXCEPTION 'COMBAT_NOT_ACTIVE: Combat status is %', v_combat.status;
    END IF;

    IF v_combat.version <> p_expected_version THEN
        RAISE EXCEPTION 'STALE_COMBAT_VERSION: Expected version %, but current combat version is %',
            p_expected_version, v_combat.version;
    END IF;

    -- 4. Participant validation
    IF p_actor_participant_id = p_target_participant_id THEN
        RAISE EXCEPTION 'INVALID_PARTICIPANT: Caster and target must be distinct';
    END IF;

    IF v_combat.current_turn_participant_id <> p_actor_participant_id THEN
        RAISE EXCEPTION 'NOT_YOUR_TURN: Current turn belongs to participant %', v_combat.current_turn_participant_id;
    END IF;

    -- Consistent lock ordering on combat_participants to prevent deadlocks:
    IF p_actor_participant_id < p_target_participant_id THEN
        v_part_first_id := p_actor_participant_id;
        v_part_second_id := p_target_participant_id;
    ELSE
        v_part_first_id := p_target_participant_id;
        v_part_second_id := p_actor_participant_id;
    END IF;

    PERFORM 1 FROM public.combat_participants WHERE combat_id = p_combat_id AND id = v_part_first_id FOR UPDATE;
    PERFORM 1 FROM public.combat_participants WHERE combat_id = p_combat_id AND id = v_part_second_id FOR UPDATE;

    SELECT * INTO v_actor
    FROM public.combat_participants
    WHERE combat_id = p_combat_id AND id = p_actor_participant_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'INVALID_PARTICIPANT: Actor participant % not found in combat', p_actor_participant_id;
    END IF;

    SELECT * INTO v_target
    FROM public.combat_participants
    WHERE combat_id = p_combat_id AND id = p_target_participant_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'INVALID_PARTICIPANT: Target participant % not found in combat', p_target_participant_id;
    END IF;

    -- 5. Spell validation
    SELECT * INTO v_spell
    FROM public.spells
    WHERE id = p_spell_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'SPELL_NOT_FOUND: Spell % not found', p_spell_id;
    END IF;

    -- 6. Recalculate blood magic mathematics on the server using public.fn_calculate_blood_magic
    SELECT * INTO v_calc
    FROM public.fn_calculate_blood_magic(
        p_caster_max_hp := v_actor.max_hp,
        p_caster_current_hp := v_actor.current_hp,
        p_caster_magic_power := v_actor.magic_power,
        p_target_current_hp := v_target.current_hp,
        p_target_defense := v_target.defense,
        p_spell_hp_cost_percent := v_spell.hp_cost_percent,
        p_spell_base_damage := v_spell.base_damage,
        p_spell_blood_multiplier_percent := v_spell.blood_multiplier_percent
    );

    v_resulting_version := v_combat.version + 1;
    v_now := now();

    -- Determine resulting state and next turn / winner
    IF v_calc.target_hp_after = 0 THEN
        v_resulting_status := 'finished';
        v_next_turn_participant_id := NULL;
        v_winner_participant_id := v_actor.id;
    ELSE
        v_resulting_status := 'active';
        v_next_turn_participant_id := v_target.id;
        v_winner_participant_id := NULL;
    END IF;

    v_action_id := gen_random_uuid();

    -- Consistent lock ordering on underlying characters table to synchronize HP and avoid deadlocks
    IF v_actor.character_id < v_target.character_id THEN
        v_char_first_id := v_actor.character_id;
        v_char_second_id := v_target.character_id;
    ELSE
        v_char_first_id := v_target.character_id;
        v_char_second_id := v_actor.character_id;
    END IF;

    PERFORM 1 FROM public.characters WHERE id = v_char_first_id FOR UPDATE;
    PERFORM 1 FROM public.characters WHERE id = v_char_second_id FOR UPDATE;

    -- Insert action log record (immutable)
    INSERT INTO public.combat_actions (
        id, combat_id, request_id, expected_version, turn_number,
        actor_participant_id, target_participant_id, spell_id,
        hp_cost, raw_damage, calculated_damage, effective_damage,
        actor_hp_before, actor_hp_after, target_hp_before, target_hp_after,
        resulting_version, resulting_status, next_turn_participant_id, winner_participant_id,
        created_at
    ) VALUES (
        v_action_id, p_combat_id, p_request_id, p_expected_version, v_combat.turn_number,
        v_actor.id, v_target.id, v_spell.id,
        v_calc.hp_cost, v_calc.raw_damage, v_calc.calculated_damage, v_calc.effective_damage,
        v_actor.current_hp, v_calc.actor_hp_after, v_target.current_hp, v_calc.target_hp_after,
        v_resulting_version, v_resulting_status, v_next_turn_participant_id, v_winner_participant_id,
        v_now
    );

    -- Update actor participant snapshot
    UPDATE public.combat_participants
    SET current_hp = v_calc.actor_hp_after
    WHERE id = v_actor.id;

    -- Update target participant snapshot
    UPDATE public.combat_participants
    SET current_hp = v_calc.target_hp_after
    WHERE id = v_target.id;

    -- Synchronize characters table in the exact same transaction
    UPDATE public.characters
    SET current_hp = v_calc.actor_hp_after
    WHERE id = v_actor.character_id;

    UPDATE public.characters
    SET current_hp = v_calc.target_hp_after
    WHERE id = v_target.character_id;

    -- Update combat state
    UPDATE public.combats
    SET version = v_resulting_version,
        turn_number = turn_number + 1,
        status = v_resulting_status,
        current_turn_participant_id = v_next_turn_participant_id,
        winner_participant_id = v_winner_participant_id,
        ended_at = CASE WHEN v_resulting_status = 'finished' THEN v_now ELSE NULL END,
        updated_at = v_now
    WHERE id = p_combat_id;

    RETURN jsonb_build_object(
        'action_id', v_action_id,
        'hp_cost', v_calc.hp_cost,
        'raw_damage', v_calc.raw_damage,
        'calculated_damage', v_calc.calculated_damage,
        'effective_damage', v_calc.effective_damage,
        'actor_hp_before', v_actor.current_hp,
        'actor_hp_after', v_calc.actor_hp_after,
        'target_hp_before', v_target.current_hp,
        'target_hp_after', v_calc.target_hp_after,
        'turn_number', v_combat.turn_number,
        'resulting_version', v_resulting_version,
        'resulting_status', v_resulting_status,
        'next_turn_participant_id', v_next_turn_participant_id,
        'winner_participant_id', v_winner_participant_id,
        'is_idempotent_replay', false
    );
END;
$$;

-- ----------------------------------------------------------------------------
-- RPC 4: fn_cancel_combat
-- Lock order: combats (FOR UPDATE) -> combat_participants (ordered by id) -> characters (ordered by id)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_cancel_combat(
    p_combat_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_user_id uuid;
    v_combat public.combats%ROWTYPE;
    v_part_1 public.combat_participants%ROWTYPE;
    v_part_2 public.combat_participants%ROWTYPE;
    v_char_first_id uuid;
    v_char_second_id uuid;
    v_now timestamptz;
BEGIN
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'UNAUTHENTICATED: Authentication required';
    END IF;

    -- 1. Lock combat row
    SELECT * INTO v_combat
    FROM public.combats
    WHERE id = p_combat_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'COMBAT_NOT_FOUND: Combat % not found', p_combat_id;
    END IF;

    IF v_combat.owner_id <> v_user_id THEN
        RAISE EXCEPTION 'FORBIDDEN: You do not own this combat';
    END IF;

    IF v_combat.status <> 'active' THEN
        RAISE EXCEPTION 'COMBAT_NOT_ACTIVE: Combat status is %, only active combats can be cancelled', v_combat.status;
    END IF;

    -- 2. Fetch and lock participants in slot order
    SELECT * INTO v_part_1
    FROM public.combat_participants
    WHERE combat_id = p_combat_id AND slot = 1
    FOR UPDATE;

    SELECT * INTO v_part_2
    FROM public.combat_participants
    WHERE combat_id = p_combat_id AND slot = 2
    FOR UPDATE;

    -- 3. Lock characters in consistent ascending UUID order
    IF v_part_1.character_id < v_part_2.character_id THEN
        v_char_first_id := v_part_1.character_id;
        v_char_second_id := v_part_2.character_id;
    ELSE
        v_char_first_id := v_part_2.character_id;
        v_char_second_id := v_part_1.character_id;
    END IF;

    PERFORM 1 FROM public.characters WHERE id = v_char_first_id FOR UPDATE;
    PERFORM 1 FROM public.characters WHERE id = v_char_second_id FOR UPDATE;

    -- Synchronize characters with current snapshot HP
    UPDATE public.characters
    SET current_hp = v_part_1.current_hp
    WHERE id = v_part_1.character_id;

    UPDATE public.characters
    SET current_hp = v_part_2.current_hp
    WHERE id = v_part_2.character_id;

    v_now := now();

    -- Update combat to cancelled
    UPDATE public.combats
    SET status = 'cancelled',
        current_turn_participant_id = NULL,
        winner_participant_id = NULL,
        ended_at = v_now,
        updated_at = v_now
    WHERE id = p_combat_id;

    RETURN jsonb_build_object(
        'combat_id', p_combat_id,
        'status', 'cancelled',
        'ended_at', v_now
    );
END;
$$;

-- ----------------------------------------------------------------------------
-- Permissions: Grant EXECUTE to authenticated role only for client-facing RPCs
-- Revoke explicitly from PUBLIC, anon and authenticated for internal helper fn_calculate_blood_magic
-- (RPCs with SECURITY DEFINER can still call it internally)
-- ----------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.fn_calculate_blood_magic FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.fn_create_character FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fn_create_character TO authenticated;

REVOKE ALL ON FUNCTION public.fn_start_combat FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fn_start_combat TO authenticated;

REVOKE ALL ON FUNCTION public.fn_cast_spell FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fn_cast_spell TO authenticated;

REVOKE ALL ON FUNCTION public.fn_cancel_combat FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fn_cancel_combat TO authenticated;
