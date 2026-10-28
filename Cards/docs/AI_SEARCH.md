# AI, MCTS a hráčské informace

Aktuální přehled objektů a jejich kontraktů. Pravidla provádí nativní engine;
agent vybírá z nabídek, které engine vytvořil nebo ověřil. Numerické encodery
pro model nejsou náhradou přesného simulovaného stavu.

Nový [bezeztrátový základ abstrakce](MCTS_EXACT_ABSTRACTION.md) odděluje přesné
klíče světa/search od dostatečných vstupů vestavěné heuristiky. Volitelně sdílí
hodnocení a pořadí akcí, včetně konzistentního přejmenování fyzických ID;
automaticky neslučuje návštěvy ani výsledky různých světů.

## Objekty rozhodování

| Objekt | Odpovědnost |
| --- | --- |
| `MCTSAgent` | Živý controller, paměť hráče, příprava search a aplikace vybrané nabídky. |
| `DecisionProjector` | Převod jednoho nativního requestu na hráčské informace a lokální vazby. |
| `ProjectedDecision` | Oddělené portable informace a vazby na konkrétní nativní možnosti. |
| `PlayerInformation` | Aktuální pohled jednajícího hráče, jeho recall a nabídnuté akce. |
| `PlayerAction` | Úplná viditelná identita akce, bez odkazu na nativní svět. |
| `InformationSetSearch` | UCT/PUCT nad informačními uzly a vzorkovanými světy. |
| `InformationSearchBudget` | Neměnné limity trajektorií, světů, hloubky a přechodů. |
| `InformationSearchResult` | Doporučení, statistiky akcí a explicitní stav fallbacku. |

Projektor se používá například jako
`DecisionProjector(request, options, recall, store=value_store).build()`.
`result.information` lze předat policy. `result.bind(action)` vrací možnost
platnou pouze pro původní request. Projekce sama akci neprovádí.

## Tři reprezentace hry

1. Živý `State` obsahuje i skutečná skrytá data obou hráčů.
2. `PlayerInformation` obsahuje pouze podporované informace konkrétního hráče.
3. Vzorkovaný svět je jedna úplná možná realizace neznámých údajů, ve které
   nativní engine může přesně provádět akce, lízání a řešení efektů.

`CurrentPositionWorlds` připravuje sanitizovaný snapshot a vzorkované kořeny.
Deklarované decklisty jsou explicitní předpoklad, nikoli přístup ke skutečné
soupeřově ruce nebo pořadí knihovny. Historicky podmíněné backendy navíc
ověřují podporovanou shodu historie a pokračování. Nepodporovaný stav se
odmítne; skrytá data se nesmějí doplnit čtením skutečné hry.

Jednající policy dostává vlastní pohled i v simulaci. Soupeřova policy tedy
vidí svou vzorkovanou ruku, naše policy ji bez odhalení nevidí. Stromové klíče
zahrnují hráčské informace a recall. Přechodová cache má navíc vlastnictví
konkrétního deterministického světa; shodný veřejný board k jejímu sdílení nestačí.

## Výběr a progressive widening

| Objekt | Odpovědnost |
| --- | --- |
| `ActionPath` | Neměnná sémantická cesta úplné akce: druh, zdroj, parametry, cíle, platba a finální identita. |
| `HierarchyNode` | Součet priorů, dokončené návštěvy/hodnoty a počet nenavštívených listů prefixu. |
| `ActionHierarchy` | Hierarchický PUCT a aktualizace všech prefixů vybrané úplné akce. |
| `CandidateOrdering` | Stabilní pořadí podle prioru nebo čistě hráčské heuristiky. |
| `ProgressiveWidening` | Neměnný plán růstu počtu připuštěných kandidátů podle návštěv. |
| `CandidateFrontier` | Vlastněný, postupně čtený proud nativních legálních možností. |

Hierarchie nad hotovým menu sama nešetří jeho generování. Nativní kandidátní
pipeline připravuje návrhy před drahými kombinacemi a později může pokračovat
úplným výčtem. Widening omezuje připuštěný prefix podle
`ceil(c * (visits + 1) ** exponent)`, nejméně jednu možnost. Legálnost se tím
nemění. Neúplný prefix se nesmí vydávat za pokrytí celého legálního menu.

Volitelná `candidate_attack_risk` nad nativní generací řadí návrhy útoků podle
veřejného statického rizika blokování. Nabízí all-in, bezpečnější skupiny,
žádný útok a omezené prefixy před beam/úplným pokračováním. Neprořezává
pravidlově legální akce ani neslučuje návštěvy různých karet. Kontrakt,
omezení a měření jsou v [MCTS_ATTACK_CANDIDATES.md](MCTS_ATTACK_CANDIDATES.md).

`candidate_combat_equivalence` přidává proud reprezentantů aktuálních bojových
rolí před konkrétní výčet. Útoky se generují přes počty bytostí ve skupině,
blokování přes uspořádané balíky počtů blokerů. Původní úplné deklarace
zůstávají později dostupné a statistiky různých ID se neslučují. Podrobnosti
jsou v [MCTS_COMBAT_EQUIVALENCE.md](MCTS_COMBAT_EQUIVALENCE.md).

## Vlastnictví trajektorií

`TrajectoryTraversal` vlastní vstupy jedné trajektorie. Nejprve prochází
uložené přechody a forced řetězce. Při prvním neuloženém přechodu může předat
práci objektu `ContinuationTraversal`, který drží stav souvislého průchodu:
původní hranici, vybranou akci, selection generátor, poslední rodičovský uzel,
dokončený výsledek a případnou odloženou chybu.

Callbacky neprovádějí vlastní kopírování při každém stromovém uzlu. Pokračují
uvnitř vlastněného nativního runtime. Chyba search se propaguje až po jeho
bezpečném rozvinutí, aby ji cost/resolution transakce nemohla zaměnit za
neúspěšnou herní operaci. Do cache patří pouze odpojené neměnné hranice.

`SearchSimulator` je přesný referenční/replay adaptér. `CheckpointSimulator`
navíc umí obnovit podporované priority continuations. Změna organizačních
objektů nemění dosavadní počítání hloubky, logical advances ani minimálního
počtu pokusů. Hloubka znamená rozhodovací kroky, nikoli počet tahů.

## Pozorování a cache

`PlayerObservationBuilder` provádí viditelnost a popis pravidlových údajů.
`Value` a `ValueStore` drží přesné neměnné JSON-shaped hodnoty; interní
`readonly()` čte sdílená pole bez hlubokého rozbalování celého dokumentu.
Veřejné `observation()` a `describe()` vracejí oddělená měnitelná data.

Dočasné záznamy v `observation_projection.py` mají explicitní význam:

- `VisibilityRead`: zkontrolovaný objekt, druh visibility dotazu a jeho výsledek.
- `DescriptionProjection`: hotový popis a jeho tranzitivní visibility závislosti.
- `ProjectedCardRow`: neměnný řádek karty s důkazy potřebnými pro dalšího pozorovatele.

Tyto dočasné záznamy smějí existovat jen během společné projekce neměněného
světa. Obsahují lokální runtime reference a nesmějí se přenést do hráčských
informací ani uchovaného stromu. Další pozorovatel musí reprodukovat i negativní
filtry attachmentů a oprávnění k last-known information.

`CachedEvaluation` pojmenovává výsledek a jeho započtenou velikost.
`EvaluationCache` používá omezený LRU a nespoléhá na procesní RSS. Změna vah
invaliduje závislé výstupy, zatímco příznaky nezávislé na vahách lze zachovat.
`SearchTreeMemory` uchovává souvislou strukturu podle informačních klíčů;
návštěvy vyžadují navíc kompatibilní session, belief, model a horizont.

## Runtime I/O a self-play

Běžné play/match/tournament/self-play bez explicitního výstupu neukládají
pracovní soubory. Světy, continuations, strom, replay i modelové snapshoty
zůstávají v RAM. Self-play používá nezávislé herní actory a procesové pipes;
`PolicyValueModel.to_bytes()` vytváří snapshot v paměti, ne soubor.

Explicitní logy zůstávají volbou volajícího. Play/match mohou zapisovat JSONL
a čitelný TXT log. Tournament s explicitním output vytváří požadovaný výstup.
Self-play může psát jeden explicitní koordinátorový diagnostický log; jednotliví
actoři nevytvářejí vlastní složky. Stávající checkpoint modelu lze načíst.
Samostatné benchmarky mohou mít explicitní exportní režim a nejsou běžným runtime.

Používejte `-B` a `PYTHONDONTWRITEBYTECODE=1`, pokud nechcete ani Python bytecode.
Testy s dočasnými vstupy nebo explicitními výstupy jsou testovací fixtures;
runtime no-write kontroly samostatně používají `deny_writes()`.

## Spuštění a ověření

Z adresáře `Cards` s aktivovaným prostředím projektu:

```powershell
python -B -m game.bin.play --ai-agent mcts --mcts-known-decks --mcts-settings configs/mcts_play_128_native.json
python -B -m game.bin.self_play --config configs/self_play_debug.json
python -B -m pytest tests -q -p no:cacheprovider
```

Hodnoty presetu nejsou důkazem obecné herní síly. Časový limit search je měkký
a nemusí zahrnovat všechny živé kroky před jeho zavoláním. Výchozí jeden worker
zachovává sériový search; více workerů používá nezávislé kořenové stromy se
sloučením statistik, nikoli nové vlákno pro každý stromový uzel.

## Styl a úklid

AI/MCTS používá pojmenované datové záznamy pro skutečné doménové hodnoty
a objekty s explicitním vlastnictvím pro stavové postupy. Krátké čisté funkce
pro matematiku, serializaci nebo interoperabilitu zůstávají funkcemi.
Dokumentační komentáře používají styl `data_structs`: `@brief`, `@var`,
u důležitých rozhraní také `@param`, `@return` a `@throws`.

Odstraněna byla stará funkční rozhraní `action_path`, `candidate_order`,
`widened_candidates`, `run_trajectory` a `project_decision`; interní volající,
benchmarkové adaptéry a testy používají nové objekty. Mana solver patří přímo
do `game.mana.mana_solver`; starý AI importní shim byl odstraněn.

Projektové Markdowny patří do `docs`. Historické reporty mimo tuto složku,
jednorázové Python skripty a kopie zdrojů v `runs` byly odstraněny. Naměřené
JSON/JSONL/TXT výsledky zůstaly zachované. Nainstalované závislosti v `.venv`
jsou mimo úklid zdrojových souborů.

## Ověření refaktoru

Celý běh sady zaznamenal **2 914 passed** a jeden neúspěšný test starého
kompatibilního importu `game.ai.mana_solver`. Tento test ověřoval výhradně
úmyslně odstraněný shim, proto byl také odstraněn. Poté prošla závěrečná sada
**460 cílených testů za 17,77 s**: mana/generation composition, hierarchie,
widening, projekce, traversal, visibility a uchování stromu/modelových příznaků.
Celá sada se po odstranění tohoto zastaralého testu znovu nespouštěla.

Strukturální kontrola ověřila, že v projektových souborech nezůstal Markdown
mimo `docs`, archivovaný Python v `runs` ani bytecode. Zkontrolovala také
veřejné dokumentační komentáře AI a existenci relativních odkazů v dokumentaci.
Nebyly nalezeny chybějící veřejné AI docstringy ani rozbité dokumentační odkazy.

Úklid odstranil 216 projektových Markdownů, 1 614 archivovaných Python
skriptů, 1 072 archivovaných bytecode souborů, 176 cache adresářů a následně
193 prázdných archivních adresářů. Nový refaktor nemá vlastní měření rychlosti
celých her; jeho validace se týká zachování chování a organizačních kontraktů.
