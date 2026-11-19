# Instruções para agentes (qualquer LLM)

Simulador de Pokémon TCG em Python + PyQt6 contra uma IA. Projeto de fã, não
oficial, licença GPL. Visão geral em `README.md`; estado e decisões em
`PLANO.md`.

## Efeitos de carta

**Antes de implementar ou mudar qualquer efeito de carta, leia
[`EFEITOS.md`](EFEITOS.md) e siga as regras de lá.** Resumo:

- nada de código para uma carta específica no motor compartilhado: só
  mecanismos genéricos (`text_effects.py`, `passive_text.py`, `wording.py`);
- carta nova chega como dado (`effects.json` publicado), nunca como código;
- texto não reconhecido fica sem efeito, nunca com um efeito parecido;
- um teste por mecanismo novo.

## Antes de cada commit

```bash
uv run black --check src tests tools && uv run ruff check src tests tools && uv run mypy src && uv run pytest -q
```

- Commits lógicos, mensagens em português, sem push (a menos que peçam).
- Atualize o `CHANGELOG.md` (seção "Não lançado") e o `PLANO.md`.
- No macOS, edite arquivos com ferramentas de edição ou Python, não com
  `sed -i`.
