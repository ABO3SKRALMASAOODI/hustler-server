"""Round 81 — the first-turn model lane is an A/B lever, not a tier.

The first agent turn a free account ever runs is where retention lives or
dies (project 319: two turns, then gone). FIRST_TURN_AGENT_MODEL routes
exactly that turn to a stronger model so the "is it the model?" question can
be answered with a cohort instead of a feeling. It ships OFF; boosted turns
are identifiable in llm_calls by the lane's model id, which is the readout.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                               # noqa: E402
import llm                                                  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_pool(monkeypatch):
    monkeypatch.setattr(llm, "_first_turn_client", None)


def _lane_on(monkeypatch, model="test-strong-model"):
    monkeypatch.setattr(config, "FIRST_TURN_AGENT_MODEL", model)
    monkeypatch.setattr(config, "FIRST_TURN_API_KEY", "sk-first")
    monkeypatch.setattr(config, "FIRST_TURN_BASE_URL", "https://api.test/v1")


def test_the_lane_ships_off(monkeypatch):
    monkeypatch.setattr(config, "FIRST_TURN_AGENT_MODEL", "")
    assert llm.first_turn_available() is False
    _, model = llm.agent_client_for(False, "free", first_turn=True)
    assert model == config.AGENT_MODEL


def test_a_free_first_turn_gets_the_lane(monkeypatch):
    _lane_on(monkeypatch)
    _, model = llm.agent_client_for(False, "free", first_turn=True)
    assert model == "test-strong-model"


def test_every_turn_after_the_first_is_ordinary(monkeypatch):
    _lane_on(monkeypatch)
    _, model = llm.agent_client_for(False, "free", first_turn=False)
    assert model == config.AGENT_MODEL


def test_a_subscriber_is_never_an_experiment(monkeypatch):
    """A paying (or trialling — is_subscribed is true from day zero)
    account's model is a promise its plan resolves; the A/B lane must not
    touch it even on a first turn."""
    _lane_on(monkeypatch)
    _, model = llm.agent_client_for(True, "ai", first_turn=True)
    assert model == config.AGENT_MODEL


def test_frontier_still_outranks_everything(monkeypatch):
    _lane_on(monkeypatch)
    monkeypatch.setattr(llm, "frontier_available", lambda: True)
    monkeypatch.setattr(config, "FRONTIER_AGENT_MODEL", "frontier-model")
    _, model = llm.agent_client_for(True, "ai_max", first_turn=True)
    assert model == "frontier-model"


def test_a_missing_key_degrades_to_the_free_model(monkeypatch):
    """Same contract as every other lane: unconfigured never 401s a turn."""
    monkeypatch.setattr(config, "FIRST_TURN_AGENT_MODEL", "test-strong-model")
    monkeypatch.setattr(config, "FIRST_TURN_API_KEY", "")
    _, model = llm.agent_client_for(False, "free", first_turn=True)
    assert model == config.AGENT_MODEL
