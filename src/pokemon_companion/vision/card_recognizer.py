"""Reconhecimento de cartas por perceptual hashing (pHash).

Escolha deliberada (ver PLANO.md): reconhecimento de instância via pHash é
rápido, escala para milhares de cartas e é robusto a pequenas variações de
brilho/rotação após a correção de perspectiva — sem precisar treinar
nenhum modelo. Reconhece apenas contra o pool do deck do jogador (dezenas
de cartas), não o card pool inteiro, o que aumenta precisão e velocidade.

Quando dois candidatos ficam próximos demais em distância de Hamming (ex:
reimpressões com a mesma arte), `is_confident` retorna `False` e a camada
de UI deve pedir confirmação manual (`ui/confirmation_dialog.py`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import imagehash
from PIL import Image

from pokemon_companion.cards_db.models import Card

DEFAULT_HASH_SIZE = 16
DEFAULT_DISTANCE_THRESHOLD = 10
DEFAULT_AMBIGUITY_GAP = 4


@dataclass(frozen=True)
class RecognitionCandidate:
    card: Card
    hamming_distance: int


class CardRecognizer:
    def __init__(self, hash_size: int = DEFAULT_HASH_SIZE) -> None:
        self._hash_size = hash_size
        self._index: dict[str, tuple[Card, imagehash.ImageHash]] = {}

    def build_index(self, cards_with_images: Iterable[tuple[Card, Image.Image]]) -> None:
        self._index = {
            card.id: (card, imagehash.phash(image, hash_size=self._hash_size))
            for card, image in cards_with_images
        }

    def __len__(self) -> int:
        return len(self._index)

    def recognize(
        self, query_image: Image.Image, max_candidates: int = 3
    ) -> list[RecognitionCandidate]:
        query_hash = imagehash.phash(query_image, hash_size=self._hash_size)
        scored = sorted(
            (
                RecognitionCandidate(card, query_hash - reference_hash)
                for card, reference_hash in self._index.values()
            ),
            key=lambda candidate: candidate.hamming_distance,
        )
        return scored[:max_candidates]

    @staticmethod
    def is_confident(
        candidates: list[RecognitionCandidate],
        distance_threshold: int = DEFAULT_DISTANCE_THRESHOLD,
        ambiguity_gap: int = DEFAULT_AMBIGUITY_GAP,
    ) -> bool:
        if not candidates:
            return False
        if candidates[0].hamming_distance > distance_threshold:
            return False
        if len(candidates) > 1:
            gap = candidates[1].hamming_distance - candidates[0].hamming_distance
            if gap < ambiguity_gap:
                return False
        return True
