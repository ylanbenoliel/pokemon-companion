"""GET com novas tentativas, usado por todas as chamadas de rede fora da
thread da interface (lá, esperar entre tentativas congelaria a tela).

As APIs públicas usadas aqui (TCGdex, pokemontcg.io, Limitless, GitHub) dão
erro passageiro com frequência: 429, 5xx, conexão caída ou timeout. Esses
casos tentam de novo com espera crescente; 4xx (carta que não existe, por
exemplo) falha na hora.
"""

from __future__ import annotations

import time
from typing import Any

import requests


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    tries: int = 3,
    backoff: float = 1.0,
    session: Any = None,
) -> requests.Response:
    """A resposta de sucesso; senão levanta `requests.RequestException`."""
    kwargs: dict[str, Any] = {"params": params, "timeout": timeout}
    if headers is not None:
        kwargs["headers"] = headers
    getter = (session or requests).get
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            response = getter(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if last:
                raise
        else:
            if last or (response.status_code != 429 and response.status_code < 500):
                response.raise_for_status()
                return response
        time.sleep(backoff * 2**attempt)
    raise AssertionError("inalcançável")  # pragma: no cover
