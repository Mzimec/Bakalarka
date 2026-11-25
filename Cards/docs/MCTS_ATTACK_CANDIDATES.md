# Kandidáti útoku podle rizika — 2026-10-27

Experiment přidává statické rizikové skupiny do nativního proudu kandidátů.
Výchozí hodnota `candidate_attack_risk` je `false`: podrobnější odhad nezrychlil
všechny kontrolované searchy a jeho herní síla zatím není změřená. Dosavadní
presety a plná baseline zachovávají předchozí chování.

## Výhra a symetrie

Terminální svět okamžitě ukončuje aktuální trajektorii. Další akce se v něm
neprovádí. Search pokračuje dalšími trajektoriemi; výhra v jednom vzorkovaném
světě není důkaz výhry v jiných světech ani proti všem reakcím soupeře.
Aktuální implementace nepřenáší „solved win“ z jediné determinization do celého
informačního uzlu.

Obecné generování má `ConservativeEquivalencePolicy` pro podporované
zaměnitelné zdroje/schopnosti. Aktivní boj, stack, trvající efekty, triggery
a další pozorovatelé identity tuto redukci konzervativně vypínají. MCTS navíc
pro priority používá `equivalence=None`, aby skrytá pravidla vzorkované ruky
nemohla ovlivnit hráčovo menu přes globální guard.

Stejné P/T a schopnosti nejsou důkaz zaměnitelnosti herních objektů. Roli mohou
hrát aura, countery, poškození, kontrola od určitého tahu, cílení, incarnation
a historické reference. Nový experiment sdílí pouze výpočet omezeného
statického odhadu; konkrétní akce, ID a N/W zůstávají oddělené.

## Objekty a proud kandidátů

`CombatantFacts` drží veřejné P/T, damage a keywords. `AttackRiskAssessment`
čte tyto hodnoty jednou během neměnné generace a rozlišuje:

| Skupina | Význam v použitém statickém modelu |
| --- | --- |
| UNBLOCKED | Žádná přípustná blokující skupina; respektuje flying/reach a menace. |
| SAFE | Použitý odhad běžného poškození nepředpovídá ztrátu útočníka. |
| MULTIBLOCK | Jednotlivý bloker ho podle odhadu nezabije; součet blokerů může. |
| TRADE | Existuje smrtící jednotlivý blok s opětovaným zabitím. |
| UNFAVOURABLE | Existuje smrtící jednotlivý blok bez zabití blokera. |
| UNKNOWN | Skrytá tvář nebo nepodporované damage mechaniky, například infect/wither. |

Jednotlivá výměna zohledňuje first strike, double strike, deathtouch,
indestructible a již udělené poškození. Součet potenciálních blokerů je
konzervativní horní odhad, ne řešení jejich rozdělení mezi všechny útočníky.
Ani SAFE není pravidlový důkaz: model nesimuluje triky z ruky, aktivace,
replacement efekty ani bojové triggery. Žádná skupina trvale nemaže akce.

`RiskAttackersStrategy` nabízí v tomto pořadí návrhy:

1. Všechny legální útočníky na daného soupeře, včetně nulové síly.
2. UNBLOCKED + SAFE.
3. Žádný útok.
4. Předchozí bezpečnější skupinu + MULTIBLOCK.
5. Předchozí skupinu + TRADE.
6. Omezené prefixy útočníků seřazených podle rizika, síly a stabilního ID.

`NativeCombatStrategy` odstraňuje duplicitní návrhy; nativní pipeline ověřuje
jejich legalitu. Pak pokračuje dosavadní beam a úplný generátor, včetně útoků
na planeswalkery. Widening pouze čte delší prefix. První čtyři možnosti nejsou
vytvořené výčtem a hodnocením všech podmnožin. Test výslovně zakazuje vstup do
beam při přípravě malého prefixu velké nabídky.

Čtení faktů má cenu O(A+B), obecná kontrola přípustných dvojic O(A×B).
Nativní `blocker_error(check_assignment=False)` v aktuálním kontraktu závisí
na útočníkovi pouze přes keywords; v rámci jednoho průchodu se proto sdílí
seznam přípustných konkrétních blokerů pro shodný evasion profil. Stejné
statické matchupy sdílejí rizikové hodnocení. Vlastní odvozený combat backend
toto sdílení nepoužívá. Nejde o univerzální lineární řešení bojů.

## Zapnutí

Do existující platné konfigurace MCTS:

```json
{
  "progressive_candidates": true,
  "candidate_native_ordering": true,
  "candidate_attack_risk": true,
  "widening_constant": 4
}
```

Riziková varianta má odlišnou identitu kandidátního modelu, takže nesdílí
informační uzly a uchovaná menu s předchozím pořadím. Blokování samo tato
volba nepřepisuje. Veškeré pracovní hodnoty jsou lokální RAM data.

## Měření

`benchmarks.mcts_attack_risk` střídá varianty v obou pořadích. Příprava měří
generaci a následnou projekci prvního prefixu čtyř akcí, ne jejich scoring.
Plné menu se připravuje celé. Po zahřátí se použije šest vzorků na variantu.

| Pozice | Úplné menu | Předchozí nativní prefix | Rizikový prefix |
| --- | ---: | ---: | ---: |
| 10 útočníků bez blokerů | 447,08 ms | 8,63 ms | 8,01 ms |
| 10 smíšených útočníků, 2 blokeři | 450,84 ms | 6,94 ms | 7,47 ms |
| 40 útočníků, 40 blokerů | neměřeno | 97,10 ms | 93,11 ms |

U poslední pozice stačilo 40 nativních kontrol párů místo 1 600 při nezávislém
klasifikování každého útočníka. U smíšeného vzorku nastal i 92,8ms outlier;
uváděny jsou mediány. Příprava výše neslibuje stejnou kvalitu omezených menu.

Celý search: 16 trajektorií, 2 světy, hloubka 4, max. extensions 8 a advances
4 096, čtyři střídající se vzorky bez deadline:

| Pozice | Předchozí | Rizikový | Native calls předchozí / rizikový |
| --- | ---: | ---: | ---: |
| Bez blokerů | 0,587 s | 0,560 s | 32 / 32 |
| Smíšená | 0,592 s | 0,650 s | 24 / 25 |

Všechny běhy dokončily 16 trajektorií bez fallbacku, doporučená hodnota byla
+1 v těchto pevných známých světech. U smíšené pozice se změnilo menu a práce.
Nejde o ablační důkaz rychlejšího provádění totožného search ani měření síly
nebo celých her. Proto experiment zůstává výchozím nastavením vypnutý.

Konečné zdroje prošly **634 cílenými testy za 78,39 s**, včetně 31 testů
rizikových návrhů a nového testu terminálních výsledků v různých skrytých
světech. Kontroly zahrnují plnou množinu legálních deklarací, planeswalkery,
skryté ruce/tváře, vlastní combat backend, UCT/PUCT/hierarchický PUCT,
continuations, konfigurace, paralelní search, self-play a přesné policy cache.
Celá projektová sada se v tomto kroku nespouštěla. Finální benchmarky běžely
se zákazem runtime zápisů; výsledky se držely v RAM a tiskly pouze na stdout.

Reprodukce z kořene repozitáře, bez pracovních souborů:

```powershell
$env:PYTHONPATH='Cards'
.venv/Scripts/python.exe -B -m benchmarks.mcts_attack_risk --repeats 6
.venv/Scripts/python.exe -B -m benchmarks.mcts_attack_risk --search --repeats 4
```
