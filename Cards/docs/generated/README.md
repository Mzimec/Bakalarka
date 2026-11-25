# Generované diagnostické tabulky

Tyto soubory jsou podpůrné přílohy historických měření. Běžná dokumentace
začíná v [hlavním indexu](../README.md) a [toku MCTS](../MCTS_SEARCH_FLOW_20261106.md).

| Tabulka | Význam a obnova |
| --- | --- |
| [Alokační místa](MCTS_ALLOCATION_SITES_20261106.md) | Statický AST index potenciálních konstrukcí. Není to měření počtu alokací za běhu. Lze jej znovu sestavit příkazem níže. |
| [Volání funkcí a konstruktory](MCTS_FLOW_CALL_COUNTS_20261106.md) | Zaznamenaná série cProfile z 2026-11-06. Ceny a řádky zdrojů patří tehdejší verzi; dnešní zdroje mohou mít jiná čísla řádků. |

Obnova statického indexu z kořene repozitáře:

```powershell
$env:PYTHONPATH='Cards'
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -B -m benchmarks.mcts_allocation_sites --output Cards/docs/generated/MCTS_ALLOCATION_SITES_20261106.md
```

Bez `--output` jde Markdown pouze na stdout. Generátor odvozuje relativní
odkazy podle umístění výstupu. Aktualizace statického indexu není nové
časové měření. Původní tabulky se při úklidu nepřeměřovaly ani nepřepočítávaly.
