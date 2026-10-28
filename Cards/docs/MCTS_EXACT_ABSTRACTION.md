# Bezeztrátový základ abstrakce MCTS — 2026-10-27

První vrstva sdílí **čistá hodnocení a pořadí akcí vestavěné heuristiky** mezi
rozhodnutími se stejnými relevantními vstupy. Nezavádí přibližný simulátor ani
nové slučování výsledků trajektorií. Přesné `PlayerInformation.fast_key`, recall,
ověření světů a pravidla uchování návštěv zůstávají zachované.

## Rozsah přesnosti

„Bezeztrátový“ zde znamená stejné skóre, support/reasons, pořadí a následný prior
jako u původní `ObservationOpponentPolicy`. Neznamená to, že normalizované
vstupy určují stejnou hru, stejné přípustné světy nebo stejnou optimální akci.

Například fáze a předchozí passy mohou změnit legální pokračování, přestože
nemění skóre konkrétní již nabídnuté akce v dnešní heuristice. Takové pozice
mohou sdílet toto skóre, ale nikoli automaticky `N/W` ani simulační přechod.
Heuristika sama zůstává stávajícím přibližným hodnocením; nová cache její
výsledek dále neaproximuje.

## Objekty a vlastnictví

| Objekt | Kontrakt |
| --- | --- |
| `KnowledgeSummary` | Index nejnovějšího veřejného pravidlového důkazu podle `(card_id, name)` pro counter-risk větev heuristiky. Záporný důkaz také přepisuje starší kladný. |
| `CounterEvidence` | Pozorovaná identita, jméno a přítomnost counter feature. Neobsahuje skutečnou skrytou kartu. |
| `CanonicalPlayerView` | Vratná normalizace úplného současného hráčského pozorování, včetně neznámých doplňkových polí. `restore()` obnoví původní data i ID. |
| `CanonicalPolicyView` | Verze `exact-policy-v1`: dostatečný vstup přesně pro vestavěný scorer, s volitelným bijektivním přejmenováním karet. |
| `CanonicalAction` | Úplný druh a obsah jedné akce v lokálních jménech; zachovává parametry, cíle, platbu i revize. |
| `ExactPolicyMemory` | LRU kontextů s hodnoceními jednotlivých akcí a jedním započteným memoizovaným menu. Vlastní pouze odpojené hráčské hodnoty. |

Summary se vytvoří až na vyžádání čtenáře historie. Po prvním použití se při
appendu přenáší nebo aktualizuje z nového pozorování. Celý recall dál existuje;
summary nemění jeho rovnost, digest ani exportované informační klíče.
Aktuální `known_locations` rozhodují, zda důkaz ještě smíme použít. Bez známé
polohy v soupeřově ruce se starý counter důkaz neuplatní. Jméno samotné bez
dříve pozorovaných pravidlových dat nestačí.

Úplná `CanonicalPlayerView` slouží také jako referenční datový základ pro další
vrstvy. Na rozdíl od užšího vstupu heuristiky nevynechává žádné současné pole.
Nevytváří se automaticky vedle každé živé projekce: další úplný převod by měl
vlastní cenu. Její klíč neobsahuje úplný recall ani kontrakt belief a nesmí se
použít jako náhrada informačního klíče stromu.

## Dostatečný vstup heuristiky

Scorer dostává `self`, `players`, `hand`, `battlefield`, `stack`, `graveyard`,
`exile`, `pending_abilities`, `combat` a `decision_context`. Zachované řádky
obsahují všechny své původní vlastnosti. Z paměti používá přesně pravdivost
přítomnosti stále známého soupeřova counter zdroje, kterou jeho dnešní risk
větev čte. Nepoužívané top-level údaje, úplný recall a menu nejsou součástí
identity samotného per-action skóre.

Při změně scoreru se musí rozšířit také tento kontrakt a referenční testy.
Vlastní evaluátory a odvozené policy bez tohoto důkazu používají původní cestu
bez cache. Normalizované výsledky nejsou univerzálním vstupem pro libovolný
model, policy nebo world provider.

Při rozšíření menu se mohou použít skóre starších úplných akcí a dopočítat nové.
Softmax a uniformní směs se stále počítají pro aktuálně nabídnuté menu.
Cache nevrací staré pravděpodobnosti pro jiný počet kandidátů.

## Volitelné přejmenování karet

Karty dostanou lokální značky podle pořadí viditelných řádků. Shodně se přepíší
odkazy v kartách, akcích, cílech a explicitních combat mapách. Původní
`zone_revision`/`source_revision`, vlastník, controller, pořadí sekvencí,
attachmenty a pravidlové údaje zůstávají. Neznámé historické reference používají
oddělený jmenný prostor a nemohou náhodně odpovídat viditelné kartě.

Nezměněné zmrazené části pravidlových popisů se sdílejí. Jména karet a schopností
se nemažou: dnešní scorer některé používá ke spárování cast/pending údajů.
Nejde o obecnou kanonizaci grafu ani sloučení všech permutací stejného boardu.
Každé použití výsledku se váže na čerstvé původní akce; původní tie-break nad
jejich obsahem zůstává zachovaný i při změně pořadí menu.

## Konfigurace a paměť

Do stávajícího MCTS JSON lze přidat:

```json
{
  "exact_policy_cache_size": 128,
  "exact_policy_cache_bytes": 16777216,
  "exact_policy_canonical_ids": false
}
```

Výchozí `exact_policy_cache_size` je **0**. Přeznačování ID má také výchozí
hodnotu `false`: jeho první použití může být dražší než ušetřené hodnocení.
Limity se vztahují na každý vlastněný policy/worker. Procesový přenos zůstává
v RAM; do worker jobu se přenáší nastavení této cache, nikoli její dřívější
hráčské pohledy. Worker si čistá hodnocení vytváří lokálně během jobu.
Neexistuje souborová cache. Byte limit je konzervativní účtování
vlastněných Python struktur, nikoli limit celkového RSS procesu. Započítává
LRU i poslední memoizované menu. Příliš velké záznamy se neuchovají.

Reset nebo invalidace modelu uvolní výsledky. Invalidace belief nemusí mazat
čistá lokální skóre; žádný výsledek vzorkované trajektorie tato paměť neobsahuje.

Search metriky přidávají `policy_view_hits`, `policy_action_hits`,
`policy_action_misses` a `policy_normalization_ms`. Zásah zde není zásah do
stromu, hotová simulace ani přeskočený engine advance. Normalization čas
nepředstavuje celou režii cache: první použití zahrnuje také skórování, řazení
a započtení paměti.

## Měření

Benchmark `benchmarks.mcts_exact_abstraction` odděluje přípravu prvního
hodnocení od opakovaného použití. Nativní menu se generuje/projektuje před
měřením; tyto časy proto nepředstavují cenu celého rozhodnutí. Čtyři střídající
se opakování, deset následných hodnocení na vzorek, limity cache 128 kontextů /
64 MiB, stejné hodnocení každé akce. „První“ znamená první použití nové policy
cache nad již připravenými a společně zahřátými zmrazenými vstupy:

| Menu | Akcí | Bez cache, opakovaně | Exact cache, opakovaně | První bez cache / exact |
| --- | ---: | ---: | ---: | ---: |
| Priority / cíle | 42 | 3,763 ms | 0,095 ms | 3,773 / 25,668 ms |
| Útočníci | 1 024 | 72,871 ms | 2,868 ms | 69,767 / 155,964 ms |
| Blokování | 1 296 | 110,371 ms | 3,516 ms | 105,750 / 224,472 ms |

Opakované hodnocení je zde přibližně 25–39× levnější, ale prvotní zpracování
je dražší. Varianta s přeznačováním ID měla první hodnocení přibližně
126 / 1 222 / 1 573 ms, proto není výchozí. Její účel je ověřené sdílení
hodnocení při jiných fyzických ID, nikoli levnější první průchod.

Omezená ABBA kontrola celého search používá 128 trajektorií, dva světy a hloubku
4 bez časového limitu. Zachovala návštěvy, hodnoty, doporučení i práci enginu:

| Kořen, medián | Bez cache | Exact cache | Dokončené trajektorie / logické kroky / native calls |
| --- | ---: | ---: | --- |
| Malá priority pozice | 0,1372 s | 0,1504 s | 128 / 513 / 13 |
| Pozice s osmi dalšími bytostmi | 1,6397 s | 1,6430 s | 128 / 513 / 77 |

Každá varianta má čtyři vzorky v pořadí ABBA + ABBA. Všechny searchy byly
použitelné a žádný zde nezasáhl do policy cache: stávající strom/transition
cache a forced komprese již většinu opakované práce vynechaly. Malé kořeny
tedy nezrychlily. U malé pozice měl jeden exact vzorek 0,4542 s; ostatní byly
0,1400–0,1509 s, takže měření je také citlivé na šum. Rozdíl celého search
nepřisuzujeme samotné heuristice ani nepřenášíme na celé partie. Plné hry s
touto volbou nebyly změřené a herní síla se touto kontrolou neporovnávala.

Spuštění z kořene repozitáře, bez výstupních souborů:

```powershell
$env:PYTHONPATH='Cards'
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -B -m benchmarks.mcts_exact_abstraction --mode all --repeats 4 --iterations 128
```

Referenční testy pokrývají číselná skóre a priory, nové lokální vazby po
přeznačení, pořadí menu, rozšiřování kandidátů, cíle a revize, attachmenty,
negativní důkazy, změny známých poloh, vlastní evaluátory, limity RAM a pickle.
Skutečný UCT, PUCT i hierarchický PUCT se porovnávají se stejnými nativními
trajektoriemi bez cache. Běhy používají také zákaz runtime zápisů.

Celá sada před závěrečným rozšířením vratné normalizace prošla **2 953 testy
za 412,31 s**. Finální zdroje poté prošly **212 cílenými testy za 71,33 s**
včetně všech **52 testů nové abstrakce**: roundtrip úplných pohledů, staged
ability/payment cesta, znalosti, všechny tři search varianty, retenční pravidla,
vláknoví i procesoví workeři a self-play. Celá sada se po těchto závěrečných
doplněních znovu nespouštěla. Benchmarky konečných zdrojů běžely se zákazem
zápisů a výstupem pouze na stdout.

## Další, ztrátová vrstva

Následující krok může mapovat podobné, ale rozdílné taktické pozice do skupin
a přenášet mezi nimi doporučení/prior s omezenou vahou. Musí mít jinou verzi
klíče a samostatné měření zkreslení. Nepatří sem přenos starých návštěv jako
nově dokončených trajektorií. Současná implementace k takovému slučování
nedává implicitní oprávnění.
