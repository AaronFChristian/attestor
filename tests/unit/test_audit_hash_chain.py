from app.services.audit import GENESIS_HASH, _compute_entry_hash


def test_hash_is_deterministic_for_same_inputs():
    h1 = _compute_entry_hash(
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "test"}, "2026-07-31T00:00:00+00:00",
    )
    h2 = _compute_entry_hash(
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "test"}, "2026-07-31T00:00:00+00:00",
    )
    assert h1 == h2


def test_hash_changes_if_any_field_changes():
    base_args = (
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "test"}, "2026-07-31T00:00:00+00:00",
    )
    base_hash = _compute_entry_hash(*base_args)

    tampered_detail = _compute_entry_hash(
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "TAMPERED"}, "2026-07-31T00:00:00+00:00",
    )
    tampered_actor = _compute_entry_hash(
        GENESIS_HASH, "mallory@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "test"}, "2026-07-31T00:00:00+00:00",
    )
    assert base_hash != tampered_detail
    assert base_hash != tampered_actor


def test_chain_breaks_downstream_when_earlier_entry_edited():
    """Simulates what verify_chain() detects: editing entry N's detail after
    the fact changes entry N's hash, which no longer matches what entry N+1
    recorded as prev_hash — this is the actual tamper-evidence mechanism."""
    entry_1_hash = _compute_entry_hash(
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "original"}, "2026-07-31T00:00:00+00:00",
    )
    entry_2_recorded_prev = entry_1_hash  # what entry 2 stored at write time

    # Someone tampers with entry 1's detail after the fact
    entry_1_hash_after_tamper = _compute_entry_hash(
        GENESIS_HASH, "alice@bank.com", "register_model", "governed_model", "abc-123",
        {"name": "tampered"}, "2026-07-31T00:00:00+00:00",
    )

    assert entry_1_hash_after_tamper != entry_2_recorded_prev, (
        "Tampering with entry 1 must be detectable: its recomputed hash no "
        "longer matches what entry 2 recorded as prev_hash."
    )
