# Sjednocení AI architektury — 2026-11-10

Výchozí `game/ai` obsahovalo 82 implementačních modulů a 17 423 řádků
v jedné ploché složce. Importový audit zahrnul herní zdroje, benchmarky a
testy. Žádný z těchto modulů nebyl bez doloženého použití: některé byly
výhradně referenční nebo aktivované experimentem. Tyto rozdíly byly ve
struktuře málo viditelné.

## Výsledek

Moduly mají deset vlastníků: `agents`, `decisions`, `information`,
`evaluation`, `search`, `simulation`, `sampling`, `training`, `experiments`
a `reference`. Podrobný [diagram, objekty a životnosti](../game/ai/README.md)
doplňuje [inventura všech modulů a tříd](generated/AI_MODULES_20261110.md).
Nová inventura uvádí i počty přímých importujících souborů z game,
benchmarks a tests; nejsou to počty běhových volání.

Jde o rozdělení podle odpovědnosti, nikoli o odstranění podporovaných
backendů podle stáří nebo počtu importů. Referenční minimax a replay
zůstávají dostupné testům; vypnuté prototypy jsou zřetelně v `experiments`.
Výchozí přepínače, herní pravidla a search rozpočty se touto etapou nemění.

## Skutečné změny objektových závislostí

1. `SearchAgentBuilder` je composition root. Drží neměnnou recipe a každým
   `build()` vytvoří samostatný provider, generation policy, observation policy,
   cache a search. Výběr current/history provideru, priory, modely i paralelní
   obal mají pojmenované konstrukční metody. JSON parser pouze validuje a
   deleguje sestavení.
2. `InformationSearchBudget`, `InformationSearchResult`, metriky, odhady a
   sampled-root chyba přešly do `search/contracts.py`. PIMC už pro tyto DTO
   neimportuje konkrétní implementaci IS-MCTS. Názvy DTO zůstávají kvůli
   datovému logovacímu schématu.
3. `SearchGenerationPolicy` a základní generování nabídek přešly z aplikační
   konfigurace do `decisions/generation_policy.py`. Live a replay používají
   stále stejný immutable objekt a stejné kandidáty.
4. PIMC builder vytvoří přímo PIMC. Dříve sestavil IS-MCTS s jeho stromovou
   pamětí a ihned jej nahradil PIMC objektem. Odstranění tohoto zbytečného
   sestavení nemění průběh samotné simulace.

Celkem zůstává 85 implementačních modulů: 82 přemístěných a tři noví
vlastníci (`composition`, `contracts`, `generation_policy`). Plochý kořen
AI obsahuje pouze composition root, lazy package initializer a README.
Neslučují se rozdílné informační smlouvy IS-MCTS/PIMC ani sanitizační
`RuntimeClone` s důvěryhodným `SimulationClone`.

## Importy, komentáře a diagnostika

Callery, testy, patch targety a benchmarky v repozitáři používají nové
module paths. Relativní importy byly převedeny podle původního vlastníka;
přesun nepřidává závislost na pořadí procházení namespace balíčků. Package
initializery mají pouze dokumentaci, žádné eager re-exporty.

Příklady nových importů:

```python
from game.ai.agents.mcts_agent import MCTSAgent
from game.ai.information.runtime_digest import DigestNode
from game.ai.search.contracts import InformationSearchBudget
from game.ai.search.information_set_search import InformationSetSearch
```

Vstupy `python -m game.bin.play/match/tournament/self_play` a JSON nastavení
zůstávají stejné. Privátní historické cloudpickle payloady s plochými
Python module paths je potřeba nově vytvořit. Zápisy strukturálních dat do
JSON se přesunem nemění; historická měření se znovu neinterpretují jako
aktuální výkon.

Komentáře nového builderu, package initializerů a runtime reader/digest
vlastníků popisují odpovědnost a životnost. `DigestNode.freeze` není snapshot
schopný obnovit pravidla. Opraveny jsou také filename filtry profilových
benchmarků a kořen fingerprintu self-play po přesunu souboru hlouběji.
Relativní dokumentační odkazy na přemístěné zdroje jsou převedené; řádkové
kotvy ve starších historických tabulkách nemusí po změně délky zdroje
označovat původní funkci a pro aktuální čísla se mají tabulky regenerovat.

## Ověření

Nové testy hlídají oddělené objektové grafy každého builderu, lazy imports,
samostatnost shared kontraktů, skutečné zdroje ve fingerprintu a statický
audit aliasových/relativních importů. PIMC test zakazuje vytvořit nepoužitý
IS-MCTS objekt během sestavení.

**Kompletní tehdy sesbíraná Cards sada: 3709 passed, 557,85 s (9:17).**
Šest nových architekturních testů vzniklo během tohoto běhu a následně
proběhlo samostatně: **6 passed, 1,98 s**. Nejde tedy o jediný 3715testový
běh. Po celé sadě se u builderu ještě zpřesnila pouze vysvětlující docstring.
Předchozí cílená kontrola importů a konfigurace měla 60 passed.

Z kořene workspace:

```powershell
$env:PYTHONPATH='Cards;Cards/tests'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONHASHSEED='0'
.venv/Scripts/python.exe -B -m pytest Cards/tests -q -x -p no:cacheprovider
.venv/Scripts/python.exe -B -m pytest Cards/tests/test_ai_architecture.py -q -x -p no:cacheprovider
```

AST kontrola zdrojů prošla. Kontrola našla nula starých plochých AI
referencí v Pythonu a nula neplatných AI source odkazů v dokumentaci.
`git diff --check` prošel. Nový výkonový benchmark se v této etapě
nespouštěl; případný přínos samotného uspořádání se časově netvrdí.

Refaktor nepřidává žádný automatický benchmark, export nebo zápis během
hraní. Statická inventura zapisuje soubor pouze při explicitním `--output`.
Nebyl proveden commit ani push; předchozí obsahové změny ve zdrojích zůstaly
zachované na přemístěných místech.
