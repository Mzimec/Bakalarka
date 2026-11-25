# Úklid AI a dokumentace — 2026-11-07

## Rozdělení dokumentace

- [Hlavní index](README.md) začíná aktuálními návody a architekturou.
- [AI_SEARCH](AI_SEARCH.md) shrnuje dnešní režimy a vlastnictví. Chronologii
  experimentů nahradila tabulka současných kontraktů; staré výsledky testů
  předchozí reorganizace mají vlastní historický záznam.
- **33 datovaných reportů** se přesunulo do [experiments/mcts](experiments/mcts/README.md).
  Výsledky a negativní měření zůstávají zachované.
- **Dvě generované přílohy** jsou v [generated](generated/README.md), oddělené
  od ručně udržovaných návodů. Čísla historických měření se nepřepočítávala.
- [Tok search](MCTS_SEARCH_FLOW_20261106.md) zůstává na původní cestě jako
  přehledový vstup. Relativní odkazy a explicitní cesty reprodukce sledují přesuny.

## AI a nástroje

[Mapa AI](../game/ai/README.md) uvádí vlastníky všech zdejších Python modulů,
hlavní tok rozhodnutí a hranice mezi hráčskou informací, stromem a světem.
[Mapa benchmarků](../benchmarks/README.md) rozlišuje latenci, instrumentované
profily, inventuru kopií a izolované experimenty.

Z pěti AI modulů byly odstraněny nepoužívané importy po kontrole interního
použití i explicitních importů ostatních projektových modulů. Používané
reexporty rozhraní `decision_maker` zůstávají dostupné.

Generátor `mcts_allocation_sites` nyní odvozuje dokumentační i zdrojové odkazy
podle cílové složky `--output`; stdout používá umístění standardní generované
přílohy. Čtyři malé testy ověřují tři cílové složky a výchozí stdout variantu.

## Ověření

**476 passed, 17,16 s.** Cílená sada zahrnovala generátor alokačních tabulek,
selector/player observations, hráčské informace, taktiku search, combat,
konstrukci schopností a automatické předání priority. Úplná sada se kvůli
tomuto organizačnímu úklidu znovu nespouštěla.

Kontrola 55 Markdown dokumentů ověřila **17 957 místních odkazů** včetně
generovaných zdrojových odkazů. Mapa pokrývá všech **72 AI modulů**.
AST kontrola prošla pro **341 Python souborů** v AI, benchmarkech a testech;
prošel také whitespace a `git diff --check`.

Z kořene repozitáře:

```powershell
$env:PYTHONPATH='Cards;Cards/tests'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONHASHSEED='0'
.venv/Scripts/python.exe -B -m pytest Cards/tests/test_allocation_sites.py Cards/tests/test_selector_observation.py Cards/tests/test_player_observation.py Cards/tests/test_search_information.py Cards/tests/test_search_tactics.py Cards/tests/test_combat.py Cards/tests/test_ability_construction.py Cards/tests/test_agent_auto_pass.py -q -p no:cacheprovider
```

Tato etapa nespouští výkonnostní benchmark a nemění algoritmus search,
presety, počet světů ani formát existujících výsledků. Předchozí lokální
změny a vývojové poznámky zůstávají zachované. Bez commitu a pushe.
