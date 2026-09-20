# Rozhodovani a generovani moznosti

Tok je `DecisionRequest -> DecisionOptionSpace(policy) -> generator -> hodnota -> DecisionMaker`.
Request popisuje herni situaci. Policy urcuje rozsah hledani a pruning. DecisionMaker
vybira hodnotu a vraci `DecisionResult`; aplikace rozhodnuti zustava v pravidlech hry.

Novy kod je v `game/game_actions/generation/decision_abstraction`. Puvodni preklep
`decsision_abstraction` zustava kompatibilnim importem.

| Request | Hodnota moznosti |
| --- | --- |
| PriorityDecisionRequest | PassPriorityAction, LandPlayAction nebo AbilityAction; volitelne concede |
| AbilityDecisionRequest | konkretni akce jedne ability vcetne nakladu a targetu |
| DeclareAttackersRequest | nemenne mapovani attacker -> defender |
| DeclareBlockersRequest | nemenne mapovani blocker -> attacker |
| MulliganRequest | bool: True znamena dalsi mulligan |
| MulliganBottomRequest | usporadana n-tice karet, prvni bude tazena prvni |
| DiscardRequest | n-tice zahazovanych karet pri cleanup |
| AbilityResolutionRequest | n-tice objektu vybranych dotcenym hracem behem resoluce |
| ManaGenerationRequest | ManaSolverResult, bez skutecne aktivace zdroju |

```python
from game.ai.decision_maker import DecisionMaker, DecisionResult

class FirstOption(DecisionMaker):
    def _decide(self, request):
        # Pro realneho agenta zde patri vyber policy a hodnoceni moznosti.
        return DecisionResult(next(iter(request.options)))
```

Vlastni policy lze predat `request.option_space(policy)`. PriorityGenerationPolicy
obsahuje samostatny pruning abilities a zemi, policy pro konkretni ability a
prepinace many/concede. AbilityGenerationPolicy umi vlozit celou stavajici
ActionGenerationStrategy, hodnotu X, life payment a pruning hotovych akci.
SelectionGenerationPolicy omezuje deklarace boje a vybery karet. LimitPruning
spotrebuje jen prvnich N moznosti. Nulovy limit abilities nebo zemi neodstrani pass.

Space je opakovatelny, liny pohled na **zivy stav**, nikoliv snapshot. Je nutne ho
spotrebovat pred provedenim akce. Enumerace neplati manu, netapuje, nepresouva karty
a neprovadi deklarace boje. U blokovani se validuje kopie evidence boje, protoze
puvodni validator pri kontrole odstranuje zastarale ucastniky.

Runtime factory AbilityDefinition.to_ability vraci CastSpellAbility, ActivatedAbility
nebo ManaAbility; prvni dve vetve sdileji PriorityActionAbility. TriggerAbility
zustava samostatnou vetvi. Existujici definice karet s flagy zustavaji podporovane.
LandPlayAction ma vlastni pravidla a neni umelou spell ability.

Aktivaci a resoluci nelze zamenit: pri `target player sacrifices a creature` cilovy
hrac vybira az pri resoluci. AbilityResolutionRequest proto nese context efektu,
legalni kandidaty a pozadovany pocet. Neoveruje znovu prioritu ani naklady kouzla.
ChooseMoveOperation pouziva tento request pro discard i sacrifice. Cleanup pouziva
DiscardRequest, protoze nema zdrojovou kartu ani ability.

ExecutionPlanPipeline pouziva ManaGenerationRequest pro planovani platby. Generator
pri prochazeni variant nevola interaktivniho decision makera: policy dodava solver.
Soucasny solver vraci jeden kanonicky plan, nikoliv vsechny mozne zpusoby platby.
Pri pozadavku na strategickou volbu mana zdroju bude dalsim krokem solver vracejici
vic planu. X je explicitni parametr policy (default 0), ne automaticky vsechny hodnoty.

Migrace zachovava ModularDecisionMaker a import DecisionMaker z game_state.player
jako adapter starych hooku. Herni smycka predava modernim controllerum requesty.
SimpleAgent generuje ability pres novy OptionSpace a collector. Modularni scoring,
konzolove zadavani a specialni trigger hooky zustavaji kompatibilni; jejich vlastni
vyberove postupy lze dale migrovat samostatne. Novy kod by mel importovat
DecisionMaker z game.ai.decision_maker.

Navazujici prace: sjednotit bounded candidate generator modularniho agenta s policy,
rozsirit mana solver na vic svedku a pripadne postupne nahradit flagy v definicich
specializovanymi definition tridami. Tyto zmeny nejsou podminkou pouziti noveho API.
