# Dokumentace Cards

- [Další body optimalizačního plánu](MCTS_MAINTENANCE_AUDIT_20261028.md): izolované experimenty s registrací a invalidací, srovnání celého search a otevřené kroky.

- [Zachování indexových projekcí při restore](MCTS_INDEX_RESTORE_20261028.md): bezeztrátový přenos ověřených cache řádků a srovnání stejné práce v MCTS.

- [Cena requestů při podobné šířce nabídky](MCTS_REQUEST_WIDTH_20261028.md): aktuální porovnání priority, útoku, blokování a voleb při resolution se 3–4 akcemi.

- [Komponenty světa a postupný combat](MCTS_NATIVE_COMPONENTS_20261028.md): volitelná deklarace přes dílčí uzly, jeden search na deklaraci a měření blokování i útoku.

- [Omezení opakované práce v MCTS](MCTS_WORK_REUSE_20261027.md): projekce při widening, charakteristiky, otisky, kandidáti a vyhodnocování horizontu.

- [Struktura projektu](PROJECT_STRUCTURE.md): složky, veřejné příkazy a vlastnictví modulů.
- [Architektura enginu](ARCHITECTURE.md): stav, generování, resolution a pravidlové subsystémy.
- [AI a MCTS](AI_SEARCH.md): hráčské informace, světy, objekty search, cache a runtime I/O.
- [Časový rozbor náročné MCTS partie](MCTS_GAME_DIAGNOSTICS_20261027.md): úplný log, konkrétní pozice, časy requestů a ověřené profilované přehrání.
- [Bezeztrátový základ abstrakce MCTS](MCTS_EXACT_ABSTRACTION.md): dostatečné vstupy heuristiky, přejmenování karet, opakované hodnocení a jeho cena.
- [Kandidáti útoku podle rizika](MCTS_ATTACK_CANDIDATES.md): terminální světy, hranice symetrie, postupné návrhy útoku a měření experimentu.
- [Ekvivalence deklarací boje](MCTS_COMBAT_EQUIVALENCE.md): počty útočníků, rozdělení blokerů, konkrétní vazby a hranice přesnosti v MCTS.
- [Agenti](AGENTS.md): controller rozhraní, heuristiky, LLM a sdílené solvery.
- [Pravidla enginu](ENGINE_RULES.md): podporovaná pravidla a jejich implementační kontrakty.
- [Spouštění a ovládání](RUN_CONTROLS.md): příkazy a interaktivní hra.
- [Plán projektu](PROJECT_ROADMAP.md): navazující práce.
- [Závislosti](DEPENDENCIES.md): prostředí a knihovny projektu.
- [AI credits](AI_CREDITS.md): popis použití vývojových asistentů.

Podrobné komentáře tříd a metod jsou přímo ve zdrojích ve stylu `data_structs`.
