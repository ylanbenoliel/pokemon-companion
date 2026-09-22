from __future__ import annotations

import numpy as np
from PIL import Image

from pokemon_companion.vision.card_recognizer import CardRecognizer, RecognitionCandidate


def _pattern_image(seed: int, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Imagem de ruído determinística — ao contrário de uma cor sólida, o
    pHash (baseado em DCT) de uma imagem uniforme colapsa para o mesmo
    valor independente da cor, então usamos ruído para gerar hashes
    distintos e testáveis."""
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 255, size=(size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(array, mode="RGB")


def test_recognize_finds_exact_match_with_zero_distance(charmander, squirtle):
    recognizer = CardRecognizer()
    recognizer.build_index([(charmander, _pattern_image(1)), (squirtle, _pattern_image(2))])

    candidates = recognizer.recognize(_pattern_image(1))

    assert candidates[0].card is charmander
    assert candidates[0].hamming_distance == 0
    assert CardRecognizer.is_confident(candidates)


def test_recognize_orders_candidates_by_distance(charmander, squirtle):
    recognizer = CardRecognizer()
    recognizer.build_index([(charmander, _pattern_image(1)), (squirtle, _pattern_image(2))])

    candidates = recognizer.recognize(_pattern_image(2))

    assert candidates[0].card is squirtle
    assert candidates[0].hamming_distance <= candidates[1].hamming_distance


def test_is_confident_false_when_top_candidates_are_ambiguous(charmander, squirtle):
    candidates = [
        RecognitionCandidate(charmander, 3),
        RecognitionCandidate(squirtle, 4),  # gap de 1: ambíguo demais
    ]

    assert not CardRecognizer.is_confident(candidates)


def test_is_confident_false_when_distance_too_high(charmander):
    assert not CardRecognizer.is_confident([RecognitionCandidate(charmander, 50)])


def test_is_confident_true_for_clear_match(charmander, squirtle):
    candidates = [
        RecognitionCandidate(charmander, 1),
        RecognitionCandidate(squirtle, 40),
    ]

    assert CardRecognizer.is_confident(candidates)


def test_is_confident_false_for_empty_candidates():
    assert not CardRecognizer.is_confident([])


def test_build_index_length_matches_unique_cards(charmander, squirtle):
    recognizer = CardRecognizer()
    recognizer.build_index([(charmander, _pattern_image(1)), (squirtle, _pattern_image(2))])

    assert len(recognizer) == 2
