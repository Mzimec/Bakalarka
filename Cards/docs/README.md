# Dokumentace Cards

## Základní přehled

- [Struktura projektu](PROJECT_STRUCTURE.md): složky, veřejné příkazy a vlastnictví modulů.
- [Architektura enginu](ARCHITECTURE.md): stav, generování, resolution a pravidlové subsystémy.
- [Pravidla enginu](ENGINE_RULES.md): podporovaná pravidla a jejich kontrakty.
- [Spouštění a ovládání](RUN_CONTROLS.md): příkazy a interaktivní hra.
- [Agenti](AGENTS.md): controllery, heuristiky, LLM a sdílené solvery.
- [Plán projektu](PROJECT_ROADMAP.md), [závislosti](DEPENDENCIES.md) a [AI credits](AI_CREDITS.md).

## AI a MCTS

- [Aktuální architektura AI](AI_SEARCH.md): hráčské informace, světy, strom, cache a runtime I/O.
- [Mapa zdrojových modulů](../game/ai/README.md): kde změnu provést a jaké hranice zachovat.
- [Úklid AI architektury](AI_ARCHITECTURE_CLEANUP_20261110.md) a [inventura všech modulů](generated/AI_MODULES_20261110.md).
- [Tok simulace](MCTS_SEARCH_FLOW_20261106.md): diagram, okolí náročných funkcí a životnost objektů. Časové tabulky jsou označené historické série.
- [Bezeztrátová abstrakce](MCTS_EXACT_ABSTRACTION.md), [kandidáti útoku](MCTS_ATTACK_CANDIDATES.md) a [ekvivalence boje](MCTS_COMBAT_EQUIVALENCE.md).
- [Mapa benchmarků](../benchmarks/README.md): nástroje pro latenci, profilování, inventuru a experimenty.

## Měření a vývojové reporty

[Index experimentů MCTS](experiments/mcts/README.md) uchovává všechny datované
zprávy odděleně od aktuálních návodů. Poslední etapy:

- [Actor příprava: revizní cache, části identity a produkty](experiments/mcts/MCTS_ACTOR_PREPARATION_20261109.md).
- [PIMC: skutečná paměť mimo simulované světy a nový backend](experiments/mcts/MCTS_PIMC_MEMORY_BOUNDARY_20261109.md).
- [Profil stabilního workeru a levnější obnova neměnných hodnot](experiments/mcts/MCTS_POSITION_VALUES_20261108.md).
- [Tři zásahy do runtime MCTS a stabilní bojový worker](experiments/mcts/MCTS_RUNTIME_STEPS_20261108.md).
- [Oddělená actor identita a hodnoticí produkt hranice](experiments/mcts/MCTS_BOUNDARY_PRODUCTS_20261108.md).
- [Společné měření čtyř demand změn](experiments/mcts/MCTS_DEMAND_COMBINED_20261108.md).
- [Čtyři samostatné ablace nákladů trajektorie](experiments/mcts/MCTS_DEMAND_ABLATIONS_20261108.md).
- [Náročnost trajektorie a skutečné použití připravených produktů](experiments/mcts/MCTS_TRAJECTORY_DEMAND_20261108.md).
- [Jednotky výkonu MCTS a další hranice dynamické pozice](experiments/mcts/MCTS_WORK_UNITS_20261108.md).
- [Dynamická prioritní pozice a stabilní pracovní objekty](experiments/mcts/MCTS_PRIORITY_POSITION_20261107.md).
- [XMage a MageZero: kopie, uzly a návrhy pro Cards](experiments/mcts/MCTS_XMAGE_COMPARISON_20261107.md).
- [Omezené skupiny polí a podotisky hráčské paměti](experiments/mcts/MCTS_IDENTITY_CACHE_TRIALS_20261107.md).
- [Detail ceny identity, živého čtení a cache](experiments/mcts/MCTS_IDENTITY_DETAIL_20261107.md).
- [Aktuální rozpad času runtime search](experiments/mcts/MCTS_RUNTIME_PROFILE_20261107.md).
- [Neměnné sdílené definice](experiments/mcts/MCTS_IMMUTABLE_DEFINITIONS_20261107.md).
- [Počty objektů a kopie světů](experiments/mcts/MCTS_OBJECT_INVENTORY_20261107.md).
- [Oddělení identity a runtime stavu karet](experiments/mcts/MCTS_CARD_STATE_SEPARATION_20261107.md).
- [Vratný pracovní svět](experiments/mcts/MCTS_REVERSIBLE_WORLD_20261107.md).

Rozsáhlé [generované tabulky](generated/README.md) mají vlastní složku.
Čísla z různých backendů, rozpočtů a instrumentovaných běhů se nesčítají.
Výsledek posledního úklidu a jeho ověření popisuje
[záznam úklidu](REPOSITORY_CLEANUP_20261107.md).
