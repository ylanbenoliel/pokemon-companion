"""Redação antiga das cartas (Black & White a Sol e Lua, no Expanded) →
a redação atual, que os compiladores de texto (`text_effects`,
`passive_text`) já reconhecem.

Cada regra troca uma forma de frase inteira ou um trecho ("the Defending
Pokémon", "his or her", "Does 30 damage", "times the number of heads"), nunca
o nome de uma carta. `modernize` só é usado quando a frase original não é
reconhecida, então não muda nada nas cartas que já compilam.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_SYMBOL = {
    "Grass": "G",
    "Fire": "R",
    "Water": "W",
    "Lightning": "L",
    "Psychic": "P",
    "Fighting": "F",
    "Darkness": "D",
    "Metal": "M",
    "Fairy": "Y",
    "Dragon": "N",
    "Colorless": "C",
}
_TYPES = "|".join(_SYMBOL)

#: redação antiga (Black & White a Sol e Lua) → a atual, que as frases já
#: reconhecem. Só entra quando a frase original não é reconhecida.
OLD_WORDING: list[tuple[str, str | Callable[[re.Match[str]], str]]] = [
    (r"\bhis or her\b", "their"),
    (r"\bhe or she\b", "they"),
    (r"\bhim or her\b", "them"),
    (r"^Shuffle your deck afterward$", "Then, shuffle your deck"),
    (r"^Does\b", "This attack does"),
    (r"damage times the number of heads", "damage for each heads"),
    (r"\bColorless in (.+?)'s Retreat Cost", r"{C} in \1's Retreat Cost"),
    (rf"\bbasic ({_TYPES}) Energy\b", r"Basic \1 Energy"),
    (rf"\b({_TYPES}) (Energy|Pokémon)\b", lambda m: f"{{{_SYMBOL[m.group(1)]}}} {m.group(2)}"),
    (rf"\b({_TYPES}) (less|more)\b", lambda m: f"{{{_SYMBOL[m.group(1)]}}} {m.group(2)}"),
    (r"(?i)^((?:.*?)discard .*?Energy(?: cards?)?) attached to\b", r"\1 from"),
    (r"Pokémon Tool card attached to it\b", "Pokémon Tool attached"),
    (r"^If there is any Stadium(?: card)? in play\b", "If a Stadium is in play"),
    (r"^Discard any Stadium card in play$", "Discard a Stadium in play"),
    (
        r"^During your opponent's next turn, any damage done to this Pokémon by attacks is "
        r"reduced by (\d+)$",
        r"During your opponent's next turn, this Pokémon takes \1 less damage from attacks",
    ),
    (
        r"^During your opponent's next turn, any damage done by attacks from the Defending "
        r"Pokémon is reduced by (\d+)$",
        r"During your opponent's next turn, attacks used by the Defending Pokémon do \1 less "
        r"damage",
    ),
    (
        r"(?i)^(If heads, )?prevent all effects of attacks, including damage, done to this "
        r"Pokémon during your opponent's next turn$",
        lambda m: f"{m.group(1) or ''}During your opponent's next turn, prevent all damage from "
        "and effects of attacks done to this Pokémon",
    ),
    (
        r"^(?:If the Defending Pokémon tries to attack during your opponent's next turn|During "
        r"your opponent's next turn, if the Defending Pokémon tries to attack), your opponent "
        r"flips a coin$",
        "During your opponent's next turn, if the Defending Pokémon tries to use an attack, your "
        "opponent flips a coin",
    ),
    (r"^If tails, that attack does nothing$", "If tails, that attack doesn't happen"),
    (
        r"^Put (\d+) damage counters instead of (\d+) on that Pokémon between turns$",
        r"During Pokémon Checkup, put \1 damage counters on that Pokémon instead of \2",
    ),
    (
        r"^Your opponent switches the Defending Pokémon with",
        "Your opponent switches their Active Pokémon with",
    ),
    (
        r"([Ss])witch (?:the Defending Pokémon with (\d+) of your opponent's Benched Pokémon|(\d+) "
        r"of your opponent's Benched Pokémon with (?:the Defending Pokémon|your opponent's Active "
        r"Pokémon))$",
        lambda m: f"{m.group(1)}witch in {m.group(2) or m.group(3)} of your opponent's Benched "
        "Pokémon to the Active Spot",
    ),
    (
        r"([Pp])revent all effects of attacks, including damage, done to",
        r"\1revent all damage from and effects of attacks done to",
    ),
    (r"damage times the (?:number|amount) of your (.+)$", r"damage for each of your \1"),
    (
        r"damage times the (?:number|amount) of (.+)$",
        lambda m: "damage for each "
        + re.sub(r"\b(card|counter)s\b", r"\1", m.group(1), count=1)
        + (" in this way" if m.group(1).endswith("you discarded") else ""),
    ),
    (
        r"^This attack does (\d+) damage minus (\d+) damage for each\b",
        r"This attack does \2 less damage for each",
    ),
    (r"\bPokémon Tool cards\b", "Pokémon Tools"),
    (r"\b(discard an? )Pokémon Tool card\b", r"\1Pokémon Tool"),
    (r"(?i)^((?:.*?)discard (?:all |a |an )?Pokémon Tools?) attached to\b", r"\1 from"),
    (r"([Dd])iscard any Stadium card in play", r"\1iscard a Stadium in play"),
    (
        r"^During your next turn, (?:each of )?this Pokémon's attacks (?:do|does) (\d+) more "
        r"damage(?: to your opponent's Active Pokémon)?$",
        r"During your next turn, attacks used by this Pokémon do \1 more damage to your "
        r"opponent's Active Pokémon",
    ),
    (
        r"and all cards attached to it into (your|their) (deck|hand)",
        r"and all attached cards into \1 \2",
    ),
    (r"^Heal all damage from all of your Pokémon$", "Heal all damage from each of your Pokémon"),
    (r"\bthat Stadium card\b", "that Stadium"),
    (
        r"^This damage isn't affected by Weakness, Resistance, or any other effects on that Pokémon$",
        "This damage isn't affected by Weakness or Resistance",
    ),
    (
        r"^(?:You may )?[Hh]ave your opponent switch their Active Pokémon with",
        "Your opponent switches their Active Pokémon with",
    ),
    (r"^Have your opponent reveal their hand$", "Your opponent reveals their hand"),
    (
        r"this Pokémon was on the Bench and became your Active Pokémon this turn",
        "this Pokémon moved from your Bench to the Active Spot this turn",
    ),
    (
        r"^Heal (\d+) damage and remove all Special Conditions from this Pokémon$",
        r"Heal \1 damage from this Pokémon, and this Pokémon recovers from all Special "
        r"Conditions",
    ),
    (
        r"^Remove all Special Conditions from (this|your Active) Pokémon$",
        lambda m: f"{m.group(1).capitalize()} Pokémon recovers from all Special Conditions",
    ),
    (
        r"^This attack does (\d+) damage to (each|each of your|\d+ of your) Benched Pokémon$",
        r"This attack also does \1 damage to \2 Benched Pokémon",
    ),
    # Treinadores: requisito de uso e redações antigas
    (r"^You can play this card only\b", "You can use this card only"),
    (r"Prize cards left than", "Prize cards remaining than"),
    (
        r"\b1 of your Pokémon was Knocked Out during your opponent's last turn",
        "any of your Pokémon were Knocked Out during your opponent's last turn",
    ),
    (r"\bis also (Asleep|Burned|Confused|Paralyzed|Poisoned)\b", r"is now \1"),
    (r"\bbasic Energy\b", "Basic Energy"),
    # lote de formas repetidas (2+ cartas)
    (r"\b1 damage counter on\b", "1 damage counters on"),
    (
        r"by damage from an (?:opponent's )?attack(?: from your opponent's Pokémon)? during "
        r"(?:their|his or her) last turn",
        "by damage from an attack during your opponent's last turn",
    ),
    (
        r"^Return this Pokémon and all cards attached to it to your hand$",
        "Put this Pokémon and all attached cards into your hand",
    ),
    (
        r"^Move an Energy attached to the Defending Pokémon to (\d+) of your opponent's Benched "
        r"Pokémon$",
        r"Move an Energy from your opponent's Active Pokémon to \1 of their Benched Pokémon",
    ),
    (r"the Defending Pokémon's attacks cost", "attacks used by the Defending Pokémon cost"),
    (
        r"any damage done to this Pokémon by attacks is increased by (\d+)$",
        r"this Pokémon takes \1 more damage from attacks",
    ),
    (
        r", and it recovers from all Special Conditions$",
        ", and this Pokémon recovers from all Special Conditions",
    ),
    (
        r"^Both this Pokémon and the Defending Pokémon are now (\w+)$",
        r"This Pokémon is now \1, and the Defending Pokémon is now \1",
    ),
    (
        r"draw a number of cards equal to the number of cards in your opponent's hand",
        "draw a card for each card in your opponent's hand",
    ),
    (r"put them back on top of your deck in any order", "put them back in any order"),
    (r"\bonly on your first turn\b", "only during your first turn"),
    (
        r"(\S+) Energy attached to it can't be affected by any Special Conditions",
        r"\1 Energy attached can't be affected by any Special Conditions",
    ),
    (
        r"^Discard all Energy from both Active Pokémon$",
        "Discard all Energy from this Pokémon, and discard all Energy from your opponent's Active "
        "Pokémon",
    ),
    # passivas antigas
    (
        r"^Prevent all effects of your opponent's attacks, except damage, done to this Pokémon$",
        "Prevent all effects of attacks used by your opponent's Pokémon done to this Pokémon",
    ),
    (
        r"([Pp])revent all damage done to this Pokémon by attacks$",
        r"\1revent all damage done to this Pokémon by attacks from your opponent's Pokémon",
    ),
    (
        r"^Put (\d+) damage counters instead of (\d+) on your opponent's Burned Pokémon between "
        r"turns$",
        lambda m: f"During Pokémon Checkup, put {int(m.group(1)) - int(m.group(2))} more damage "
        "counters on your opponent's Burned Pokémon",
    ),
    (
        r"^If this Pokémon is your Active Pokémon and is damaged by an opponent's attack, ",
        "If this Pokémon is in the Active Spot and is damaged by an attack from your opponent's "
        "Pokémon, ",
    ),
    (
        r"^Prevent all effects of attacks from your opponent's Pokémon done to this Pokémon$",
        "Prevent all effects of attacks used by your opponent's Pokémon done to this Pokémon",
    ),
    (r"^Your (.*?)Pokémon's attacks do", r"Attacks used by your \1Pokémon do"),
    (r"more damage to the Active Pokémon$", "more damage to your opponent's Active Pokémon"),
    (
        r"^As long as this Pokémon is your Active Pokémon,",
        "As long as this Pokémon is in the Active Spot,",
    ),
    (
        r"your opponent's Active Pokémon's attacks do",
        "attacks used by your opponent's Active Pokémon do",
    ),
    (
        r"less damage from your opponent's attacks$",
        "less damage from attacks from your opponent's Pokémon",
    ),
    (r"^Your (.*?)Pokémon take (\d+) less damage", r"All of your \1Pokémon take \2 less damage"),
    (
        r"has no Energy attached to it, this Pokémon has no Retreat Cost",
        "has no Energy attached, it has no Retreat Cost",
    ),
    # Habilidades passivas
    (
        r"^Any damage done to this Pokémon by attacks is reduced by (\d+)$",
        r"This Pokémon takes \1 less damage from attacks",
    ),
    (r"(has any .*?Energy) attached to it\b", r"\1 attached"),
    (r"\bStadium card in play\b", "Stadium in play"),
    # "X can't attack during your opponent's next turn" → "During ..., X can't attack"
    (
        r"^(If heads, |If tails, )?(?!During)(.+?) during (your opponent's|your) next turn$",
        lambda m: f"{m.group(1) or ''}During {m.group(3)} next turn, "
        f"{m.group(2)[0].lower()}{m.group(2)[1:]}",
    ),
]


def modernize(sentence: str, defending: bool = False) -> str:
    """Frase com a redação atual. `defending`: também troca "the Defending
    Pokémon" por "your opponent's Active Pokémon" (o mesmo Pokémon na hora do
    ataque), para as frases que só existem na forma nova."""
    for pattern, replacement in OLD_WORDING:
        sentence = re.sub(pattern, replacement, sentence)
    if defending:
        sentence = re.sub(r"\bthe Defending Pokémon", "your opponent's Active Pokémon", sentence)
        sentence = re.sub(r"\bThe Defending Pokémon", "Your opponent's Active Pokémon", sentence)
    return sentence
