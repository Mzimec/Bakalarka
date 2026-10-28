# Náročná partie MCTS: pozice, requesty a cena search — 2026-10-27

**Další experiment (2026-10-28):** [komponenty světa a postupný combat](MCTS_NATIVE_COMPONENTS_20261028.md)
měří celou deklaraci s jedním rozpočtem search. Syntetické blokování zrychlilo
o 24,7 %, útok zpomalil 3,10×. Režim lze zapnout pouze pro blokování; výchozí
presety se nemění. Nová délka celých partií tím prokázaná není.

**Navazující optimalizace (2026-10-28):** [omezení opakované práce](MCTS_WORK_REUSE_20261027.md)
prošlo stejnými čtyřmi kořeny se zachováním celých odpovědí MCTS a logických
průchodů. V pomalých pozicích ubylo 23–26 % sestavení pohledu; časy celého
search jsou smíšené, včetně jednoho zpomalení. Report porovnává také principy
z MageZero. Následující text zachovává původní měření celé partie.

Celá diagnostická partie trvala **32 min 13,06 s**. MCTS white prohrálo proti
Modular red ve 21. tahu. Bylo provedeno 506 herních rozhodnutí, z toho 252
požadavků MCTS: 116 searchů, 133 forced voleb a tři setup policy volby.
Všech 116 searchů dokončilo 128 trajektorií; žádný herní fallback ani chyba.

Úplný [JSONL log](../runs/mcts-game-diagnostics-20261027.jsonl) má 2 741 řádků
a 40 898 362 bajtů. Obsahuje události produkčního match runneru, hráčský pohled
před volbou MCTS, zvolenou akci, časové metriky každého requestu a komponenty
každého search. Řádek `seq=N` je zároveň řádkem N v souboru.

Jde o opakování stejného scénáře: seed 42, začíná Modular, preset
`mcts_play_128_native.json`, 128 iterací, čtyři světy, hloubka 8, extensions 16,
bez časového limitu, jeden worker. Experimentální attack-risk, combat
equivalence a exact-policy cache jsou vypnuté. Zdrojový fingerprint zůstal
během měření shodný. Herní algoritmus ani jeho konfigurace se tímto úkolem
neměnily; přibyl [diagnostický benchmark](../benchmarks/mcts_game_diagnostics.py).

Předchozí běh bez této diagnostiky měl 32:59 a maximum search 60,03 s. Nynější
rozdíl není prokázané zrychlení: přidali jsme měření a logování a nemáme sérii
střídaných měření. Shodují se seed, výsledek, tah konce a počty rozhodnutí i
searchů; nemáme původní úplný trace pro důkaz shody všech odehraných akcí.

## Jak číst časy

`mcts_request.detail.elapsed_ms` je čas rozhodnutí agenta. Obsahuje živé
generování, projekci, snapshot a search. `detail.search.metrics.elapsed_ms`
měří užší interval search. `profile.seconds` navíc zahrnuje obal měření a
commit stromu. Tyto intervaly se nesčítají.

`profile.scopes.*.exclusive_ms` rozděluje pouze obalené funkce na jejich
vzájemně vyloučené časy. Neobalená práce je samostatný zbytek, nikoli
automaticky „provádění pravidel“. `simulation_stages` a čas `advance_ms`
zahrnují některé z těchto funkcí; nepřičítat je znovu.

Obě zachycená přehrání jsou **mimo čas celé partie**. Pomalé přehrání s
cProfile trvalo 120,97 s proti původním 48,53 s. Profiler mění i relativní
cenu funkcí; jeho kumulativní časy a počty volání používáme k lokalizaci
práce, nikoli jako neovlivněný rozpad původních 48,53 s.

## Requesty v této partii

Časy v následující tabulce jsou pouze pro požadavky, které skutečně spustily
search. Forced a setup rozhodnutí nejsou započtena do jejich průměru.

| Request | Všechny požadavky | Se search | Průměr search | Medián | Maximum | Součet search |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PriorityDecisionRequest | 233 | 103 | 16,36 s | 13,53 s | 62,07 s | 1 685,12 s |
| DeclareAttackersRequest | 10 | 8 | 15,88 s | 14,98 s | 42,13 s | 127,08 s |
| DeclareBlockersRequest | 3 | 2 | 23,16 s | 23,16 s | 40,01 s | 46,32 s |
| ReplacementOrderRequest | 3 | 3 | 9,03 s | 4,17 s | 21,86 s | 27,08 s |

**Priority tvoří 89,4 % součtu search času.** Je to podíl z této konkrétní
partie; zejména dva blokovací searchy nejsou reprezentativní statistický vzorek.
Herní fáze není typ requestu: ve fázi `DECLARE_ATTACKERS` jsou také priority
okna a řešení schopností.

## Kde se čas skutečně spotřeboval

Živé MCTS fáze za celou partii: generování **0,394 s**, projekce a fallback
**1,331 s**, příprava snapshotů **20,187 s**, forced kotvy **2,080 s** a search
včetně diagnostického obalu **1 904,316 s**. Součet užších search metrik je
1 885,600 s, přibližně 97,5 % času partie.

Následující rozdělení používá společný interval `profile.seconds`, celkem
1 899,767 s. Časy jsou z celé hry s lehkými hooky, nikoli z cProfile přehrání.

| Oblast | Čas | Podíl tohoto intervalu | Volání |
| --- | ---: | ---: | ---: |
| Kopírování světů a checkpointů | 459,69 s | 24,2 % | 9 078 |
| Stavba hráčských pozorování | 428,10 s | 22,5 % | 77 441 |
| Odebírání/generování kandidátů ve frontách | 144,40 s | 7,6 % | 83 027 |
| Výběr ve stromu včetně přidružené obsluhy | 20,97 s | 1,1 % | 36 043 |
| Načtení a commit uchovaného stromu | 14,04 s | 0,7 % | 232 |
| Hodnocení listů | 1,56 s | 0,1 % | 14 848 |
| Zbytek mimo uvedené hooky | 830,99 s | 43,7 % | — |

Top-k živého kořene nemůže odstranit stovky sekund, když všechno jeho
generování stálo necelou půlsekundu. Generování **uvnitř simulace** už významné
je, ale ani úplné odstranění naměřených kandidátních front by samo neodstranilo
zbývajících přibližně 92 % intervalu.

Celkem bylo 14 848 trajektorií, 136 982 logických přechodů a 46 123 skutečných
volání simulator advance. Přesná transition cache obsloužila 90 859 přechodů;
85 738 přechodů bylo forced. Světy se obnovovaly 8 218krát. Čítač kopírovacích
funkcí je vyšší, protože zahrnuje i další kopie checkpointů. Přenos starých
kořenových návštěv byl nulový; samotné uchování stromu tedy v této hře
neodstranilo práci již provedených trajektorií mezi živými rozhodnutími.

## Konkrétní rychlé a pomalé pozice

Identifikátory níže jsou `search_index`, nikoli čísla tahů ani všech rozhodnutí.
U progressive candidates je počet kořenových akcí nabídnutý prefix po widening;
není to automaticky velikost úplného legálního menu.

| Search | Request a situace | Search | Kořen | Logické kroky | Skutečné advance | Obnovení světa |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | Priorita, 2. tah, hlavní fáze | 1,60 s | 5 | 1 024 | 136 | 21 |
| 2 | Pořadí damage, 3. tah | 1,04 s | 2 | 1 024 | 64 | 12 |
| 23 | Priorita, 12. tah, začátek boje | 25,50 s | 3 | 1 506 | 800 | 136 |
| 87 | Priorita, 19. tah, hlavní fáze soupeře | 48,53 s | 3 | 2 114 | 1 020 | 188 |
| 90 | Blokování, 19. tah | 40,01 s | 46 | 1 270 | 1 036 | 152 |
| 92 | Pořadí tří damage operací, 19. tah | 21,86 s | 6 | 1 037 | 535 | 94 |
| 102 | Útok, 20. tah | 42,13 s | 46 | 1 358 | 1 176 | 155 |
| 104 | Priorita se schopností na stacku, 20. tah | 62,07 s | 3 | 2 165 | 1 298 | 172 |

### Search 1 a 2: levné navzdory 128 iteracím

Na začátku druhého tahu jsou na battlefield pouze dva permanenty, včetně
soupeřova Tin Street Cadeta 1/1. MCTS má v ruce dvě Moorland Inquisitor,
Hallowed Priesta a čtyři Plains. Vybere zahrání Plains. Kopie světů stojí
0,76 s, pozorování 0,30 s a kandidáti 0,12 s.

Search 2 vybírá pořadí damage 1 a 2 od Cadeta a Nest Robbera. MCTS má 20 životů
a na stole je pět permanentů. Cache obslouží 960 z 1 024 přechodů, skutečný
simulátor potřebuje jen 64 volání. Stejný typ ordering requestu může být na
pozdním boardu dvacetkrát dražší, přestože počet pořadí vzroste jen ze dvou na šest.

### Search 23: malé priority menu otevírá drahý boj

MCTS má 17 životů, soupeř 20. White má dvě Hallowed Priest 1/1, dvě Moorland
Inquisitor 2/2 a Serra Angel 4/4. Red má Cadeta 1/1, tři Nest Robbery 2/1
a dva Ogre Battledrivery 3/3. Celkem je na battlefield 22 permanentů.

Kořen má jen tři volby; MCTS zvolí pass. Do dalších stavů ale vede 800 nových
volání simulátoru, 1 378 staveb pozorování a 1 502 odběrů kandidátů. Kopie
stojí 6,16 s, pozorování 5,72 s, kandidáti 2,51 s. Je to cena opakované
práce nad budoucím bojem, nikoli vygenerování tří kořenových akcí.

### Search 87 a 90: 14 bytostí a blížící se soupeřův útok

V 19. tahu má white 17 životů a šest bytostí: dvě Hallowed Priest 1/1,
dvě Moorland Inquisitor 2/2 a dva Angel of Vitality 2/2. Red má 6 životů,
Cadeta 1/1, čtyři Nest Robbery 2/1, dva Ogre Battledrivery 3/3 a Volcanic
Dragona 4/4. Na battlefield je 29 permanentů, obě živé ruce jsou prázdné.
Pořadí a identity zbývajících knihoven jsou pro hráče stále skryté.

Tři priority volby v search 87 jsou pass a aktivace first strike jednoho
ze dvou Inquisitorů za {2}{W}. MCTS zvolí pass. V simulaci vznikne 2 218
pozorování a 2 869 odběrů kandidátů. Pozorování stojí 11,24 s, kopie 10,10 s,
kandidáti 5,58 s; výběr ve stromu jen 0,285 s.

V search 90 útočí oba Ogre a Dragon, white vybírá blokování. Z 1 270 přechodů
je 1 036 skutečně nových: cache ušetří podstatně méně než v rychlém search 2.
Pozorování stojí 11,14 s, kopie 7,49 s a kandidáti 4,00 s. MCTS v této partii
nakonec nezablokuje; to není tvrzení, že taková volba je dobrá.

### Search 92: levná nabídka pořadí, drahé její vyhodnocení

Ve stejném boji jde o pořadí tří `DamagePlayerOperation`, za 3, 3 a 4 damage
do white hráče se 17 životy. Šest pořadí se živě připraví za 0,102 ms,
ale search stojí 21,86 s. Snapshot nested pokračování stojí navíc 1,26 s.
Uvnitř search je 535 nových advance a 754 staveb pozorování za 6,86 s.

To je kandidát na samostatný důkaz ekvivalence operací. Stejný součet damage
sám nestačí: záleží také na událostech, triggerech, replacementech, historii
a případné předčasné prohře. Tímto měřením jsme jejich zaměnitelnost nedokázali.

### Search 104: nejpomalejší je opět priorita, nikoli útok

Ve 20. tahu má white 7 životů a red 6. White už deklarovalo pět útočníků:
oba Priesty, jednoho Inquisitora a oba Angely. Na stacku čeká `first_strike`
útočícího Inquisitora c14. Kořen má stále jen tři možnosti a zvolí pass.

Search provede 1 298 skutečných advance, 3 048 staveb pozorování a 2 751
odběrů kandidátů. Pozorování stojí **17,87 s**, kopie **10,63 s**, kandidáti
**4,50 s**; výběr ve stromu **0,269 s**. Živé generování tří možností stálo
3,28 ms. Největší cena tedy přichází až při průchodu světy, stackem a bojem.

Hloubka 8 není tvrdý příkaz zastavit po osmi voláních enginu. Search čeká
na klidný priority bod; při nevyřešeném stacku či combat damage pokračuje
podle extensions a forced pravidel, stále s hard advance budgetem. Konstrukční
mikrovolby také nejsou vždy celý depth krok. Proto může 128 trajektorií
vygenerovat 2 165 logických přechodů. Přesné pravidlo je v
`information_set_search._Run.trajectory_choices` a `search_horizon.quiet_priority`.

## Co ukázal profiler funkcí na search 87

Přesný replay se stejnou odpovědí, odhady a návštěvami akcí, počtem dokončených
trajektorií, přechodů, simulator calls, world restores a checkpoint hits je
v řádku 2 740 logu. Následující počty jsou z tohoto přehrání:

| Místo | Počet volání | Co se opakuje |
| --- | ---: | --- |
| SimulationClone.copy | 3 429 724 | Rekurzivní kopírování grafu a memo lookupy |
| RuntimeClone.copy | 1 815 196 | Obecnější kopírovací cesty |
| PlayerObservationBuilder.build | 2 218 | Nové hráčské projekce |
| PlayerObservationBuilder._describe | 600 469 | Strukturování popisů schopností a pravidel |
| Card.get_stat | 1 698 695 | P/T, typy, keywordy a další charakteristiky |
| ValueStore.freeze | 1 279 013 | Neměnná reprezentace pro klíče a sdílení |
| structural_key.digest | 1 141 407 | Otisky strukturovaných hodnot |
| refresh_continuous_effects | 386 856 | Kontrola a případná aktualizace efektů |
| IndexedRegister.synchronise | 66 008 | Kontrola a synchronizace odvozených indexů |

Volání refresh/synchronise zahrnují i rychlé návraty při čistém stavu;
nejde o 386 856 úplných přepočtů efektů ani 66 008 kompletních rebuildů indexu.
V profilu přesto jejich kumulativní větve patří k drahým cestám. Překrývají
se s pozorováním, kandidáty a prováděním operací; jejich časy nelze přičíst
k předchozí tabulce jako další nezávislé části.

## Směr další optimalizace

1. Omezit opakované stavění stejných popisů karet a čtení nezměněných
   charakteristik: sdílené neměnné definice, oddělená malá runtime pole a
   cache s přesnou revizí a viditelností. Změřit zvlášť `_describe`, stat
   přístupy a `freeze/digest`, ne pouze velikost výsledného JSONu.
2. Zlevnit vlastněné kopie a obnovení pokračování. Je třeba snížit práci
   rekurzivního copieru na dynamické části světa při zachování nezávislosti RNG,
   pamětí, efektů a historických referencí. Neodstraňovat kontroly jen kvůli času.
3. Prověřit opakované synchronizace registrů a continuous-effect kontroly
   uvnitř stejné nezměněné revize. Batching/caching musí zachovat závislosti
   vrstev a chování triggerů; profiler ukázal kandidáty, nikoli hotový důkaz.
4. Teprve vedle toho ladit kandidáty a zavést prokázané ekvivalence konkrétních
   operation batches. Omezení widening mění prozkoumané větve a může měnit
   kvalitu hry; tato prohraná partie není validace jejího zachování.

Čistá úprava UCT/PUCT výběru nebo další zrychlení živého kořenového menu
podle tohoto měření nemá potenciál zásadně zkrátit celou hru. Kompaktnější
přesná reprezentace a méně opakovaného odvozování jsou podloženější první kroky.

## Reprodukce a ověření

Z adresáře Cards s aktivovaným prostředím, s novou cestou výstupu:

```powershell
python -B -m benchmarks.mcts_game_diagnostics --output runs/mcts-diagnostics-NEW.jsonl --profile-replay
```

Bez `--output` benchmark nevytváří soubor; bez `--profile-replay` provede pouze
celou hru s komponentovými časovači. `--capture-index` vybírá search k
podrobnému přehrání, výchozí je 87. Samotné viditelné pohledy v JSONL nejsou
serializované nativní checkpointy; reprodukce celé hry používá seed, decklisty,
konfiguraci a stejné zdroje. Engine log může obsahovat diagnostické setup
informace obou hráčů; nejsou vstupem výběru akcí MCTS.

Krátká kontrola měla čtyři tahy, dvě iterace, čtyři searchy a dvě úspěšná
přehrání; ověřila také posloupnost záznamů a zakončení logu. Celý finální běh
ověřil souvislé sekvenční číslování všech 2 741 JSON řádků, oba přesné replaye,
terminální výsledek bez chyby a nezměněný zdrojový fingerprint. Běžel s `-B`
a auditním zákazem všech filesystem zápisů kromě výslovně určeného JSONL logu.
Žádné dočasné soubory, bytecode ani binární profily se nevytvářely.
