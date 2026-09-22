"""Regras de Treinadores, Ferramentas, Estádio, Habilidades, efeitos de
ataque, nocaute no banco, escolhas manuais e Morte Súbita."""

from __future__ import annotations

import dataclasses
import random

import pytest

from pokemon_companion.cards_db.models import Ability, Attack, Card, Supertype
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import (
    AttachEnergy,
    EndSetup,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    PlayTrainer,
    PromoteActive,
    Retreat,
    UseAbility,
    UseAttack,
    UseStadium,
)
from pokemon_companion.engine.effects import passives
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay

from .conftest import make_basic_pokemon, make_energy, make_evolution
from .test_rules import build_state


def trainer(name: str, kind: str = "Item", *extra: str) -> Card:
    return Card(id=f"t-{name}", name=name, supertype=Supertype.TRAINER, subtypes=[kind, *extra])


def mon(name: str = "Mon", hp: int = 100, **kwargs: object) -> Card:
    return make_basic_pokemon(name, hp, "Colorless", **kwargs)  # type: ignore[arg-type]


def with_ability(card: Card, name: str) -> Card:
    return dataclasses.replace(card, abilities=[Ability(name=name)])


def with_attack(card: Card, name: str, cost: list[str], damage: str) -> Card:
    return dataclasses.replace(card, attacks=[Attack(name=name, cost=cost, damage=damage)])


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender"))
    s.player.deck = [mon(f"Deck{i}") for i in range(20)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(20)]
    return s


# --------------------------------------------------------------------------
# Apoiadores, Itens, Ferramentas, Estádios


def test_supporter_only_once_per_turn(state):
    state.player.hand = [trainer("Judge", "Supporter"), trainer("Judge", "Supporter")]
    rules.apply_action(state, PlayTrainer(hand_index=0))
    assert len(state.player.hand) == 4  # Judge: embaralha e compra 4
    assert not any(
        isinstance(a, PlayTrainer) and state.player.hand[a.hand_index].name == "Judge"
        for a in rules.legal_actions(state)
    )


def test_no_supporter_on_first_turn_of_starting_player(state):
    state.turn_number = 1
    state.player.hand = [
        trainer("Judge", "Supporter"),
        trainer("Team Rocket's Proton", "Supporter"),
    ]
    names = {
        state.player.hand[a.hand_index].name
        for a in rules.legal_actions(state)
        if isinstance(a, PlayTrainer)
    }
    assert names == {"Team Rocket's Proton"}


def test_boss_orders_offers_each_benched_target(state):
    state.opponent.bench = [PokemonInPlay(card=mon("A")), PokemonInPlay(card=mon("B"))]
    state.player.hand = [trainer("Boss's Orders", "Supporter")]
    boss = [a for a in rules.legal_actions(state) if isinstance(a, PlayTrainer)]
    assert {a.target for a in boss} == {("opp", 0), ("opp", 1)}

    rules.apply_action(state, PlayTrainer(hand_index=0, target=("opp", 1)))

    assert state.opponent.active.card.name == "B"
    assert state.opponent.bench[1].card.name == "Defender"
    assert state.player.discard[-1].name == "Boss's Orders"


def test_ultra_ball_needs_two_other_cards_and_searches(state):
    state.player.hand = [trainer("Ultra Ball"), make_energy("Fire Energy", "Fire")]
    assert not any(isinstance(a, PlayTrainer) for a in rules.legal_actions(state))

    state.player.hand.append(make_energy("Water Energy", "Water"))
    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert len(state.player.hand) == 1 and state.player.hand[0].is_pokemon
    assert len(state.player.discard) == 3  # 2 energias + a Ultra Ball


def test_items_blocked_by_itchy_pollen(state):
    state.player.hand = [trainer("Poké Pad")]
    state.player.items_blocked_turn = state.turn_number
    assert not any(isinstance(a, PlayTrainer) for a in rules.legal_actions(state))


def test_tool_attaches_and_changes_hp_and_retreat(state):
    state.player.hand = [trainer("Hero's Cape", "Tool", "ACE SPEC"), trainer("Air Balloon", "Tool")]
    rules.apply_action(state, PlayTrainer(hand_index=0, target=("own", -1)))
    assert state.player.active.tool.name == "Hero's Cape"
    assert state.player.active.max_hp == 200
    # não cabe outra Ferramenta no mesmo Pokémon
    assert not any(
        isinstance(a, PlayTrainer) and a.target == ("own", -1) for a in rules.legal_actions(state)
    )

    heavy = PokemonInPlay(card=mon("Heavy", retreat_cost=2))
    state.player.bench = [heavy]
    rules.apply_action(state, PlayTrainer(hand_index=0, target=("own", 0)))
    assert passives.retreat_cost(state, PlayerId.PLAYER, heavy) == 0


def test_stadium_replaces_previous_and_is_used_once_per_turn(state):
    state.stadium = trainer("Jamming Tower", "Stadium")
    state.stadium_owner = PlayerId.OPPONENT
    state.player.hand = [trainer("Prism Tower", "Stadium"), mon("X"), mon("Y"), mon("Z")]
    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert state.stadium.name == "Prism Tower"
    assert state.opponent.discard[-1].name == "Jamming Tower"

    assert UseStadium() in rules.legal_actions(state)
    rules.apply_action(state, UseStadium())  # descarta 2, compra 1
    assert len(state.player.hand) == 2
    assert UseStadium() not in rules.legal_actions(state)


# --------------------------------------------------------------------------
# Habilidades


def test_run_errand_only_from_active_and_once_per_turn(state):
    kanga = with_ability(mon("Mega Kangaskhan ex"), "Run Errand")
    state.player.active = PokemonInPlay(card=kanga)
    state.player.bench = [PokemonInPlay(card=kanga)]
    uses = [a for a in rules.legal_actions(state) if isinstance(a, UseAbility)]
    assert uses == [UseAbility(position=-1, ability_name="Run Errand")]

    rules.apply_action(state, uses[0])
    assert len(state.player.hand) == 2
    assert not any(isinstance(a, UseAbility) for a in rules.legal_actions(state))


def test_cursed_blast_knocks_out_user_and_gives_prize(state):
    dusknoir = with_ability(mon("Dusknoir", hp=160), "Cursed Blast")
    state.player.bench = [PokemonInPlay(card=dusknoir)]
    target = PokemonInPlay(card=mon("Victim", hp=130))
    state.opponent.bench = [target]

    rules.apply_action(
        state, UseAbility(position=0, ability_name="Cursed Blast", target=("opp", 0))
    )

    assert state.opponent.bench == []  # 13 contadores: nocauteado
    assert state.player.bench == []  # Dusknoir se nocauteia
    assert len(state.player.prizes) == 5 and len(state.opponent.prizes) == 5


def test_mysterious_rock_inn_blocks_damage_from_ex(state):
    crustle = with_ability(mon("Crustle", hp=150), "Mysterious Rock Inn")
    ex_card = dataclasses.replace(mon("Big ex"), subtypes=["Basic", "ex"])
    state.player.active = PokemonInPlay(card=ex_card, attached_energies=["Colorless"])
    state.opponent.active = PokemonInPlay(card=crustle)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 0


# --------------------------------------------------------------------------
# ataques com efeito


def test_cruel_arrow_targets_any_opponent_pokemon(state):
    archer = with_attack(mon("Fezandipiti ex"), "Cruel Arrow", ["Colorless"], "")
    state.player.active = PokemonInPlay(card=archer, attached_energies=["Colorless"])
    state.opponent.bench = [PokemonInPlay(card=mon("Frail", hp=90))]
    attacks = [a for a in rules.legal_actions(state) if isinstance(a, UseAttack)]
    assert {a.target for a in attacks} == {("opp", -1), ("opp", 0)}

    rules.apply_action(state, UseAttack(attack_index=0, target=("opp", 0)))

    assert state.opponent.bench == []  # 100 de dano no banco: nocaute
    assert len(state.player.prizes) == 5


def test_phantom_dive_counters_knock_out_bench(state):
    dragapult = with_attack(mon("Dragapult ex"), "Phantom Dive", ["Colorless"], "200")
    state.player.active = PokemonInPlay(card=dragapult, attached_energies=["Colorless"])
    state.opponent.active = PokemonInPlay(card=mon("Tank", hp=300))
    state.opponent.bench = [PokemonInPlay(card=mon("Small", hp=60))]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 200
    assert state.opponent.bench == []
    assert len(state.player.prizes) == 5


def test_festival_lead_attacks_twice_with_festival_grounds(state):
    dipplin = with_ability(
        with_attack(mon("Dipplin"), "Beat", ["Colorless"], "20"), "Festival Lead"
    )
    state.player.active = PokemonInPlay(card=dipplin, attached_energies=["Colorless"])
    state.stadium = trainer("Festival Grounds", "Stadium")

    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.active_player == PlayerId.PLAYER  # pode atacar de novo
    assert rules.legal_actions(state) == [UseAttack(attack_index=0), EndTurn()]
    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 40
    assert state.active_player == PlayerId.OPPONENT


def test_cant_attack_next_turn_marker(state):
    latias = with_attack(mon("Latias ex", hp=300), "Eon Blade", ["Colorless"], "200")
    state.player.active = PokemonInPlay(card=latias, attached_energies=["Colorless"])
    state.opponent.active = PokemonInPlay(card=mon("Wall", hp=400))
    rules.apply_action(state, UseAttack(attack_index=0))
    rules.apply_action(state, EndTurn())  # turno do oponente

    assert not any(isinstance(a, UseAttack) for a in rules.legal_actions(state))


# --------------------------------------------------------------------------
# energia especial e prêmios


def test_legacy_energy_provides_any_type_and_reduces_prize_once(state):
    fire_attacker = make_basic_pokemon(
        "Blaze", 100, "Fire", attack_cost=["Fire"], attack_damage="10"
    )
    state.player.active = PokemonInPlay(card=fire_attacker, attached_energies=["Legacy Energy"])
    assert any(isinstance(a, UseAttack) for a in rules.legal_actions(state))

    ex_card = dataclasses.replace(mon("Big ex", hp=10), subtypes=["Basic", "ex"])
    state.opponent.active = PokemonInPlay(card=ex_card, attached_energies=["Legacy Energy"])
    state.opponent.bench = [PokemonInPlay(card=mon("Next"))]
    rules.apply_action(state, UseAttack(attack_index=0))

    assert len(state.player.prizes) == 5  # ex vale 2, Legacy Energy tira 1


def test_rare_candy_skips_stage_one(state):
    basic = mon("Dreepy")
    stage1 = make_evolution("Drakloak", "Dreepy", 90, "Psychic", "70")
    stage2 = dataclasses.replace(
        make_evolution("Dragapult ex", "Drakloak", 320, "Psychic", "200"),
        subtypes=["Stage 2", "ex"],
    )
    state.player.active = PokemonInPlay(card=basic, turn_played=1)
    state.player.hand = [trainer("Rare Candy"), stage2]
    state.player.deck.append(stage1)  # a linha evolutiva é conhecida pelo deck

    candy = [a for a in rules.legal_actions(state) if isinstance(a, PlayTrainer)]
    assert candy == [PlayTrainer(hand_index=0, target=("candy", "Dragapult ex", -1))]
    rules.apply_action(state, candy[0])

    assert state.player.active.card.name == "Dragapult ex"
    assert [c.name for c in state.player.active.prior_cards] == ["Dreepy"]


# --------------------------------------------------------------------------
# escolhas manuais, setup e Morte Súbita


def test_manual_promotion_waits_for_player_then_turn_passes(state):
    state.active_player = PlayerId.OPPONENT
    state.manual_choices = frozenset({PlayerId.PLAYER})
    state.opponent.active = PokemonInPlay(card=mon("Hitter"), attached_energies=["Colorless"])
    state.player.active = PokemonInPlay(card=mon("Frail", hp=10))
    state.player.bench = [PokemonInPlay(card=mon("A")), PokemonInPlay(card=mon("B"))]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.pending_promotion == PlayerId.PLAYER
    assert rules.decision_player(state) == PlayerId.PLAYER
    assert rules.legal_actions(state) == [PromoteActive(0), PromoteActive(1)]
    rules.apply_action(state, PromoteActive(bench_index=1))

    assert state.player.active.card.name == "B"
    assert state.active_player == PlayerId.PLAYER  # o turno do oponente terminou


def test_manual_setup_then_game_starts(charmander, squirtle, fire_energy):
    deck = [charmander] * 30 + [squirtle] * 30  # mão só de Básicos
    state = turn_manager.start_new_game(
        list(deck),
        [charmander] * 20 + [fire_energy] * 40,
        rng=random.Random(3),
        first_player=PlayerId.OPPONENT,
        manual=frozenset({PlayerId.PLAYER}),
    )
    assert state.player.active is None and state.opponent.active is not None
    assert rules.decision_player(state) == PlayerId.PLAYER
    actions = rules.legal_actions(state)
    assert all(isinstance(a, PlayBasicToActive) for a in actions)

    rules.apply_action(state, actions[0])
    bench_moves = [a for a in rules.legal_actions(state) if isinstance(a, PlayBasicToBench)]
    rules.apply_action(state, bench_moves[0])
    rules.apply_action(state, EndSetup())

    assert state.pending_setup == ()
    assert len(state.player.bench) == 1
    assert rules.decision_player(state) == PlayerId.OPPONENT


def test_simultaneous_win_starts_sudden_death(state):
    state.player.prizes = state.player.prizes[:1]
    ex_card = dataclasses.replace(mon("Kamikaze", hp=100), subtypes=["Basic"])
    recoil = with_attack(ex_card, "Wild Press", ["Colorless"], "210")
    state.player.active = PokemonInPlay(card=recoil, attached_energies=["Colorless"])
    state.player.active.damage_counters = 40  # o recuo de 70 nocauteia
    state.opponent.prizes = state.opponent.prizes[:1]
    state.player.deck = [mon(f"D{i}") for i in range(30)]
    state.opponent.deck = [mon(f"O{i}") for i in range(30)]

    messages = rules.apply_action(state, UseAttack(attack_index=0))

    assert any("Morte Súbita" in m for m in messages)
    assert state.sudden_death and state.winner is None
    assert len(state.player.prizes) == 1 and len(state.opponent.prizes) == 1


def test_retreat_discards_least_useful_energy(state):
    attacker = make_basic_pokemon(
        "Sparky", 100, "Lightning", attack_cost=["Lightning"], retreat_cost=1
    )
    state.player.active = PokemonInPlay(card=attacker, attached_energies=["Lightning", "Water"])
    state.player.bench = [PokemonInPlay(card=mon("Bench"))]
    rules.apply_action(state, Retreat(bench_index=0))
    assert state.player.bench[0].attached_energies == ["Lightning"]


def test_team_rockets_energy_only_on_team_rocket_pokemon(state):
    energy = dataclasses.replace(
        make_energy("Team Rocket's Energy", "Colorless"), subtypes=["Special"]
    )
    state.player.hand = [energy]
    state.player.bench = [PokemonInPlay(card=mon("Team Rocket's Murkrow"))]
    attaches = [a for a in rules.legal_actions(state) if isinstance(a, AttachEnergy)]
    assert attaches == [AttachEnergy(hand_index=0, target_is_active=False, bench_index=0)]


def test_enhanced_hammer_discards_only_special_energy(state):
    special = dataclasses.replace(make_energy("Mist Energy", "Colorless"), subtypes=["Special"])
    state.opponent.active.attached_energies = ["Fire", "Mist Energy"]
    state.opponent.active.special_energy_cards = [special]
    state.player.hand = [trainer("Enhanced Hammer")]

    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert state.opponent.active.attached_energies == ["Fire"]
    assert state.opponent.discard[-1].name == "Mist Energy"


def test_strange_timepiece_devolves_one_stage(state):
    basic = make_basic_pokemon("Kadabra", 80, "Psychic")
    evolved = dataclasses.replace(
        make_evolution("Alakazam", "Kadabra", 140, "Psychic", "100"), subtypes=["Stage 2"]
    )
    state.player.active = PokemonInPlay(card=evolved, prior_cards=[basic], turn_played=1)
    state.player.hand = [trainer("Strange Timepiece")]

    options = [a for a in rules.legal_actions(state) if isinstance(a, PlayTrainer)]
    assert options == [PlayTrainer(hand_index=0, target=("own", -1))]
    rules.apply_action(state, options[0])

    assert state.player.active.card.name == "Kadabra"
    assert state.player.hand[-1].name == "Alakazam"
    # não pode evoluir de novo neste turno
    assert not any(isinstance(a, Evolve) for a in rules.legal_actions(state))


def test_estimated_damage_uses_board_state():
    from pokemon_companion.cards_db.models import Attack as AttackCard
    from pokemon_companion.engine.effects import attacks as attack_effects

    card = dataclasses.replace(
        mon("Alakazam"),
        attacks=[AttackCard(name="Powerful Hand", cost=["Psychic"], damage="", text="x")],
    )
    s = build_state(player_active=card, opponent_active=mon("Alvo"))
    s.player.hand = [mon(f"C{i}") for i in range(7)]
    active = s.player.active
    assert attack_effects.estimated_damage(s, PlayerId.PLAYER, active, card.attacks[0]) == 140


def test_cost_progress_counts_partially_paid_cost():
    progress = passives.cost_progress
    assert progress(["Fire"], ["Fire", "Colorless"]) == 0.5
    assert progress(["Fire", "Psychic"], ["Fire", "Colorless"]) == 1.0
    assert progress([], ["Fire"]) == 0.0
    assert progress(["Any"], ["Metal"]) == 1.0


def test_tera_pokemon_takes_no_attack_damage_on_bench(state):
    """Regra Tera: no Banco, dano de ataque não atinge (contadores sim)."""
    from pokemon_companion.engine.effects import cardinfo

    tera = dataclasses.replace(mon("Dragapult ex", hp=320), subtypes=["Basic", "ex"])
    assert cardinfo.is_tera(tera)
    state.opponent.bench = [PokemonInPlay(card=tera)]
    sniper = with_attack(mon("Sniper"), "Cruel Arrow", ["Colorless"], "")
    state.player.active = PokemonInPlay(card=sniper, attached_energies=["Colorless"])

    rules.apply_action(state, UseAttack(attack_index=0, target=("opp", 0)))
    assert state.opponent.bench[0].damage_counters == 0

    # o mesmo Pokémon no Ativo recebe dano normalmente
    state.opponent.active, state.opponent.bench = state.opponent.bench[0], []
    state.active_player = PlayerId.PLAYER
    rules.apply_action(state, UseAttack(attack_index=0, target=("opp", -1)))
    assert state.opponent.active.damage_counters == 100


# --------------------------------------------------------------------------
# decks 21–25 do meta: mecanismos novos


def _attacker(state, name: str, damage: str, ability: str | None = None) -> None:
    card = with_attack(mon("Attacker", hp=300), name, ["Colorless"], damage)
    if ability:
        card = with_ability(card, ability)
    state.player.active = PokemonInPlay(card=card, attached_energies=["Colorless"])


def test_retaliation_puts_counters_on_the_next_attacker(state):
    _attacker(state, "Ready to Ram", "40")
    state.opponent.active = PokemonInPlay(
        card=mon("Defender", hp=200), attached_energies=["Colorless"]
    )

    rules.apply_action(state, UseAttack(attack_index=0))
    rules.apply_action(state, UseAttack(attack_index=0))  # oponente revida com Tackle

    assert state.player.active.damage_counters == 20
    assert state.opponent.active.damage_counters == 40 + 60


def test_curly_wall_needs_another_bouffalant(state):
    _attacker(state, "Smash", "100")
    wall = with_ability(mon("Bouffalant", hp=200), "Curly Wall")
    state.opponent.active = PokemonInPlay(card=wall)
    assert passives.static_damage_reduction(state, PlayerId.OPPONENT, state.opponent.active) == 0

    state.opponent.bench = [PokemonInPlay(card=wall)]
    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 40


def test_binding_flame_raises_retreat_and_phantom_maze_scales_with_it(state):
    _attacker(state, "Phantom Maze", "130+", ability="Binding Flame")
    state.opponent.active = PokemonInPlay(card=mon("Defender", hp=400))
    assert passives.retreat_cost(state, PlayerId.OPPONENT, state.opponent.active) == 2

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 130 + 50 * 2


def test_compound_eyes_bonus_only_against_ability_pokemon(state):
    _attacker(state, "Tackle", "20", ability="Compound Eyes")
    state.opponent.active = PokemonInPlay(card=with_ability(mon("Target"), "Anything"))

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 70


def test_damage_per_card_in_opponents_hand(state):
    _attacker(state, "Mind Ruler", "30×")
    state.opponent.active = PokemonInPlay(card=mon("Defender", hp=300))
    state.opponent.hand = [mon("A"), mon("B"), mon("C")]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 90


def test_bubbly_water_energy_cures_and_protects_water_pokemon(state):
    from pokemon_companion.engine.effects import core
    from pokemon_companion.engine.game_state import StatusCondition

    bubbly = dataclasses.replace(make_energy("Bubbly Water Energy", "Water"), subtypes=["Special"])
    water = PokemonInPlay(
        card=make_basic_pokemon("Wet", 300, "Water"), status=StatusCondition.ASLEEP
    )
    core.attach_energy_card(water, bubbly)
    assert water.status == StatusCondition.NONE
    assert passives.provided_energy(state, PlayerId.OPPONENT, water) == ["Water"]

    state.opponent.active = water
    _attacker(state, "Absolute Snow", "150")
    rules.apply_action(state, UseAttack(attack_index=0))

    assert water.damage_counters == 150
    assert water.status == StatusCondition.NONE


def test_bench_damage_amount_comes_from_the_card_text(state):
    text = "This attack also does 50 damage to 1 of your opponent's Benched Pokémon."
    starmie = dataclasses.replace(
        mon("Mega Starmie ex", hp=330),
        attacks=[Attack(name="Jetting Blow", cost=["Colorless"], damage="120", text=text)],
    )
    state.player.active = PokemonInPlay(card=starmie, attached_energies=["Colorless"])
    state.opponent.active = PokemonInPlay(card=mon("Defender", hp=300))
    state.opponent.bench = [PokemonInPlay(card=mon("Frail", hp=50))]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 120
    assert state.opponent.bench == []


def test_az_tranquility_heals_the_ex_sent_to_the_bench(state):
    ex_card = dataclasses.replace(mon("Big ex", hp=250), subtypes=["Basic", "ex"])
    state.player.active = PokemonInPlay(card=ex_card, damage_counters=100)
    state.player.bench = [PokemonInPlay(card=mon("Pivot"))]
    state.player.hand = [trainer("AZ's Tranquility", "Supporter")]

    rules.apply_action(state, PlayTrainer(hand_index=0, target=("own", 0)))

    assert state.player.active.card.name == "Pivot"
    assert state.player.bench[0].damage_counters == 20


def test_blowtorch_costs_a_fire_energy_and_can_discard_the_stadium(state):
    state.stadium = trainer("Prism Tower", "Stadium")
    state.stadium_owner = PlayerId.OPPONENT
    state.player.hand = [trainer("Blowtorch"), make_energy("Fire Energy", "Fire")]
    options = {a.target for a in rules.legal_actions(state) if isinstance(a, PlayTrainer)}
    assert ("stadium",) in options

    rules.apply_action(state, PlayTrainer(hand_index=0, target=("stadium",)))

    assert state.stadium is None
    assert [c.name for c in state.player.hand] == []
    assert "Fire Energy" in [c.name for c in state.player.discard]


def test_grand_tree_evolves_twice_from_the_deck(state):
    stage1 = make_evolution("Middle", "Attacker", 120, "Colorless", "30")
    stage2 = dataclasses.replace(
        make_evolution("Top", "Middle", 200, "Colorless", "90"), subtypes=["Stage 2"]
    )
    state.player.deck = [stage1, stage2, mon("Filler")]
    state.stadium = trainer("Grand Tree", "Stadium", "ACE SPEC")

    rules.apply_action(state, UseStadium())

    assert state.player.active.card.name == "Top"
    assert [c.name for c in state.player.active.prior_cards] == ["Middle", "Attacker"]


def test_salvatore_evolves_a_pokemon_played_this_turn(state):
    state.player.bench = [PokemonInPlay(card=mon("Fresh"), turn_played=state.turn_number)]
    state.player.deck = [make_evolution("Grown", "Fresh", 150, "Colorless", "60")]
    state.player.hand = [trainer("Salvatore", "Supporter")]

    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert state.player.bench[0].card.name == "Grown"
