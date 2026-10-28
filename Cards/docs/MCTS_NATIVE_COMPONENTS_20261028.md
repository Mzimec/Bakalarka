# MCTS: komponenty světa a postupný combat — 2026-10-28

Implementace zkouší dva principy z MageZero/XMage: kopírování polí vlastněného
světa a konstrukci deklarace boje dílčími rozhodnutími. Postupný combat je
volitelný a lze ho zapnout samostatně pro blokování. Výchozí presety zůstávají
beze změny. Veškeré pracovní struktury zůstávají v RAM.

## Co jsme převzali jako inspiraci

[MageZero MCTSNode](https://github.com/WillWroble/mage/blob/master/Mage.Server.Plugins/Mage.Player.AIMCTS/src/mage/player/ai/MCTSNode.java)
sdílí jeden `rootGame` v rámci stromu, uchovává logický `GameState` a používá
skripty dílčích voleb. [MCTSPlayer](https://github.com/WillWroble/mage/blob/master/Mage.Server.Plugins/Mage.Player.AIMCTS/src/mage/player/ai/MCTSPlayer.java)
má postupné vybírání útočníků a blokerů.
[GameState](https://github.com/WillWroble/mage/blob/master/Mage/src/main/java/mage/game/GameState.java)
má explicitní kopírovací konstruktor komponent. Z těchto zdrojů nelze odvodit
jejich skutečnou latenci kopie ani zaručený násobek zrychlení pro náš Python.

U nás již existují izolované vzorkované světy, souvislé trajektorie, checkpointy
a specializovaný `SimulationClone`. Nový experiment na tyto kontrakty navazuje;
nesdílí jeden měnitelný svět mezi různými větvemi nebo workery.

## Postupný combat a jeden rozpočet na deklaraci

`CombatDecisionFrame` uchovává uspořádané tvory, jejich legální cíle a zvolený
prefix. `CombatNextRequest` nabízí pouze další přiřazení: zůstat mimo combat,
nebo útočit/blokovat zvolený cíl. Žádný prefix netapuje karty, nemění combat ani
nepublikuje událost. Teprve kompletní deklarace projde původním validátorem a
engine ji následně provede obvyklou cestou.

U deseti útočníků proti jednomu hráči má každý dílčí uzel dvě možnosti místo
teoretických 1024 kompletních podmnožin. **To neznamená, že nativní top‑k v
benchmarku všech 1024 podmnožin skutečně generovalo.** Úplná enumerace se
používá v regresním porovnání všech legálních listů; runtime začíná levnými
návrhy a omezeným beamem a pokračuje podle widening. U blokování má další tvor nejvýše `A + 1` možností,
kde `A` je počet útočníků. Příprava legálních párů může stát `O(A × B)`;
nejde o obecný příslib lineárního času. Úplná množina listů se u podporovaných
pravidel nemění, ale MCTS větvení, priory a průzkum se mění.

Native snapshot obsahuje i rozpracovaný rámec. Obnovení zachovává privátní recall
hráče, vazby na kopie karet a odložené phase triggery. Po dokončení se obnovený
rámec spotřebuje, aby nepřetekl například z deklarace útoku do blokování.

Hloubka search se zvýší až dokončením deklarace. Každá dílčí volba se přesto
počítá do tvrdého limitu advances, rozhodnutí a času. Mezi dílčími volbami v
jedné nativní simulační konstrukci se sdílí neměnný pohled na board; kontext
nového prefixu, akce a recall se vždy sestaví znovu. Tato cache nepřežívá
provedení deklarace ani přechod na jiný rámec.

Živý agent spustí nejvýše **jeden search na celou deklaraci**. Pro pokračování
z něj převezme nejvýše 512 odpojených dvojic klíč informace → akce. Klíč zahrnuje
přesný vlastní pohled, recall a prefix. Návštěvy rozhodují o volbě, průměrná
hodnota rozlišuje shody. Pokud příslušný prefix nebyl ve stromu prozkoumán,
dokončí ho běžná viditelná heuristika. To je explicitní změna strategie,
nikoli důkaz stejně silného agenta. Plán se po deklaraci zahodí.

Log rozlišuje `searched`, `combat_plan` a `combat_plan_policy`. Poslední důvod
znamená heuristické dokončení neprozkoumaného prefixu, nikoli další search ani
strukturální selhání enginu.

Menace, „must be blocked by all“ a vlastní třídy combatu zůstávají u původního
generátoru. Jejich globální požadavky zatím nemají ověřený prefixový adaptér;
samostatně nelegální blok může být součástí legálního multiblocku. Běžný
multiblock postupná varianta podporuje.

Do JSON nastavení MCTS lze přidat:

```json
"sequential_combat": "blockers"
```

`false` zachová původní pipeline, `"attackers"` zapne jen útok a `true` obojí.
Režim zatím vyžaduje jeden search worker. Self-play jej odmítne, protože jeho
trénovací kontrakt zatím pracuje s kompletními finálními akcemi.

## Kopírování polí komponent

Experiment `copy_world(..., scalar_fields=True)` provede mělkou kopii polí
nativního komponentového záznamu a přes společné memo zkopíruje měnitelné
reference. Zachovává aliasy na `__dict__`, zpětné vazby, runtime klíče,
callbacky a rozšíření. Explicitní náhrady primitiv a kořenové dictionary recipes
si ponechávají původní cestu. Odvozené cache se resetují stejně jako dříve.

Výchozí hodnota je **false**: samotná změna průchodu atributy zatím nepřinesla
spolehlivou úsporu na skutečném světě. Pro větší zisk bude třeba omezit rozsah
kopírovaného grafu při zachování vlastnictví; zkrácení kopírovací smyčky samo
o sobě nestačí. Experiment nepřidává ztrátovou aproximaci pravidel ani neprokázané
sdílení definic s měnitelnými efekty.

## Měření a ověření

[Výstupní JSONL log](../runs/mcts-native-components-20261028.jsonl) obsahuje
konfiguraci, fingerprint zdrojů, celé odpovědi a počítadla. Čtyři měření na
variantu, střídavé pořadí, 32 iterací, dva světy, depth 4, extensions 8,
16 384 advances, bez časového limitu. Jde o syntetické již existující combat
pozice a shodný rozpočet, **nikoli o stejnou prozkoumanou práci**.

| Celá živá deklarace — medián | Nativní top‑k | Postupná konstrukce | Výsledek |
| --- | ---: | ---: | --- |
| 10 útočníků | 0,695 s | 2,152 s | 3,10× pomalejší |
| 4 blokující tvorové, 5 útočníků | 2,697 s | 2,032 s | o 24,7 % rychlejší |

CPU mediány jsou 0,695 / 2,148 s pro útok a 2,680 / 2,016 s pro blokování.
Všech 16 searchů dokončilo 32 trajektorií. Každá deklarace použila jen jeden
search. Útok následoval osm uložených prefixových voleb a jedno heuristické
dokončení; blokování jednu uloženou volbu a dvě heuristická dokončení. Zvolený
útok se mezi variantami shodoval, blokování se lišilo. Zrychlení blokování proto
není důkazem zachování síly ani stejného hodnocení kompletních akcí.

Současný nativní generátor již umí levně navrhovat celé útoky a stejnou větev
opakovaně využívat. Dílčí konstrukce zde přidává uzly, kontexty a průzkum dalších
prefixů, které převáží úsporu v menu. To je důvod samostatného přepínače pro
blokování a zachování původního útoku ve výchozím nastavení.

Konkrétně první měřený útok s 32 trajektoriemi měl u nativní varianty 23
zpřístupněných kořenových akcí, tři informační uzly a 32 skutečných posunů
simulace. Postupná varianta měla v kořeni jen dvě akce, ale celkem 28 informačních
uzlů a 259 skutečných posunů. Logické kroky byly 512 / 736 a transition cache
hits 480 / 477. Zmenšení jednoho menu tedy vedlo k většímu množství celkové
práce. Dodatečné opakování stejného benchmarku s čítačem vstupů do
`FullAttackersStrategy.generate` zaznamenalo **0 vstupů a 0 yieldů**: úplný
generátor podmnožin se pro tento běh vůbec nespustil.

Šest opakování 25 kopií stejného vzorkovaného světa z historického kořene 87:
medián 0,879 s původní cestou a 0,850 s přes `scalar_fields`, rozdíl 3,4 %.
Jednotlivé páry mají opačné znaménko; nejde o přesvědčivou stabilní úsporu.
Menší syntetický svět vykázal 10,5 %, což se na skutečný graf nepřenáší.
Proto tento experiment není zapnutý v runtime. Všechny porovnané kopie měly
shodný projekční výstup.

Kontrolní search nad historickými kořeny 1 a 87 zachoval celé odpovědi,
hodnoty/návštěvy i logickou práci (128 dokončených trajektorií; 1024/2114
logických kroků a 136/1020 skutečných simulator advances). Rozběhnuté pořadí
ABBA ale obsahovalo přerušení stroje: jeden wall čas byl 1604 s při zhruba
9 s samotného měřeného kopírování. **Tuto sérii nepoužívám k tvrzení o zrychlení
celého search.**

Benchmark `benchmarks.mcts_native_components` měří celou živou deklaraci,
včetně všech dílčích callbacků, a odděleně 25 kopií stejného vlastněného světa.
Čas generování prvního malého menu se nevydává za čas celého search. Nová délka
celých partií ani stejná herní síla touto etapou prokázané nejsou.

## Ověření správnosti

Celá sada: **3 055 passed, 362,60 s** (`python -B -m pytest -p no:cacheprovider
Cards/tests -q`, spuštěno z kořene repozitáře). Zdroje `game`, `helper` a
`benchmarks` odpovídají fingerprintu v měřicím logu:
`9448a255ac088545035bdedd2f1aec648a6cabfc25adb80cb65491d0d938a1fa`.

Regrese porovnávají všechny 1024/1296 konkrétní deklarace s původní pipeline,
atomické provedení, odmítnutí nelegálních prefixů, fallback globálních omezení,
obnovu každého živého prefixu, odložené phase triggery, izolaci sourozeneckých
větví a neměnnost původního světa. Samostatná ablace projekce ověřuje shodné
pohledy i kontexty všech deseti prefixů s menším počtem sestavení boardu.

Dvě kontroly s reálným `MCTSAgent` a `CurrentPositionWorlds` pro neprázdný útok
a blokování použily vždy jeden search na dva tvory; oba dokončily 16 trajektorií
a navazující volbu převzaly z plánu. Nešlo o zástupný policy fallback.

Osmikolové herní kontroly white/red proti Modular a blue/black proti Simple
skončily na zadaném `turn_limit`, bez chyby a herního fallbacku; 12/12 a 15/15
searchů bylo použitelných. Trvaly 13,08 a 21,87 s při **16 iteracích, dvou
světech a depth 2**. Nejde o celé partie ani srovnání rychlosti se starým během
128/4/8. Tyto krátké partie neobsahovaly neprázdnou dílčí deklaraci blokování;
tu pokrývají cílené nativní kontroly výše. Runtime část kontrol proběhla pod
zákazem filesystem zápisů. Výjimkou je následné explicitní zapsání výstupního
JSONL reportu, nikoli pracovní soubor používaný enginem.

Z adresáře `Cards`, bez pracovních souborů:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_native_components --repeats 4 --iterations 32
python -B -m benchmarks.mcts_native_components --recorded-copy --repeats 6
python -B -m pytest -p no:cacheprovider tests/test_combat_frames.py tests/test_simulation_clone.py -q
```

Benchmark zapisuje jen stdout. Referenční kořen 87 se rekonstruuje z již
existujícího diagnostického logu; změny jeho plného information key se odmítnou.
Pracovní zdroj reference pro kontrolní search se uchovával pouze v RAM.
