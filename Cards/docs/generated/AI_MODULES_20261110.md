# Inventura AI modulů — 2026-11-10

Statická inventura zdrojů a přímých importů. Počty G/B/T znamenají počet
různých importujících souborů v game / benchmarks / tests, nikoli počet
volání, aktivních objektů nebo důkaz zapnutí funkce. Experimentální části
mají vlastní přepínače; jejich import sám o sobě experiment nezapíná.

Celkem 108 implementačních modulů, 21776 řádků.

## agents

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [decision_maker.py](../../game/ai/agents/decision_maker.py) | Typed decisions with one explicit handler per request kind. | `DecisionResult`, `DecisionMaker`, `ModularDecisionMaker` | 23/7/49 |
| [llm_policy.py](../../game/ai/agents/llm_policy.py) | Optional Ollama selector with bounded requests and deterministic fallback. | `OllamaSelector` | 1/0/3 |
| [mcts_agent.py](../../game/ai/agents/mcts_agent.py) | Live search controller with a separate trusted world-construction boundary. | `MCTSAgent` | 1/5/4 |
| [modular_agent.py](../../game/ai/agents/modular_agent.py) | Composable candidate generation and decision policies for bounded game search. | `Candidate`, `HeuristicSelector`, `ActionEvaluator`, `ParameterPolicy`, `CandidateGenerator`, `CombatPolicy`, `ModularAgent` | 2/11/21 |
| [simple_agent.py](../../game/ai/agents/simple_agent.py) | Bounded, deterministic decisions through the engine's public action pipeline. | `DecisionLimitReached`, `SimpleAgent` | 6/10/17 |
| [trivial_agent.py](../../game/ai/agents/trivial_agent.py) | Lazy first-candidate baseline for measuring games with minimal policy work. | `TrivialAgent` | 2/0/1 |

## composition

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [composition.py](../../game/ai/composition.py) | Build one isolated AI object graph from validated application settings. | `SearchAgentBuilder` | 2/0/3 |

## decisions

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [action_hierarchy.py](../../game/ai/decisions/action_hierarchy.py) | PUCT over semantic action prefixes, without executing partial engine actions. | `ActionPath`, `HierarchyNode`, `ActionHierarchy` | 1/0/3 |
| [attack_candidates.py](../../game/ai/decisions/attack_candidates.py) | Visible combat-risk estimates for lazy attack proposals, never rule pruning. | `AttackRisk`, `CombatantFacts`, `AttackRiskAssessment`, `RiskAttackersStrategy`, `PowerAttackersStrategy` | 1/1/1 |
| [candidate_ranking.py](../../game/ai/decisions/candidate_ranking.py) | Bounded, stable proposal ordering; not an exhaustive global top-k sort. | `VisibleCandidateRanker` | 3/0/1 |
| [combat_equivalence.py](../../game/ai/decisions/combat_equivalence.py) | Public, decision-local combat roles and lazy representative declarations. | `CardRoleSignature`, `CombatActionClass`, `BlockerPairs`, `CombatRoles`, `CombatDeclarationEquivalence`, `EquivalentCombatStrategy` | 1/3/2 |
| [combat_strategies.py](../../game/ai/decisions/combat_strategies.py) | Combat proposals and evaluation; selection pipelines own declaration legality. | `AllAttackersStrategy`, `CombatEvaluator`, `ConservativeAttackersStrategy`, `GreedyBlockersStrategy`, `BoundedBlockersStrategy` | 6/0/0 |
| [generation_candidates.py](../../game/ai/decisions/generation_candidates.py) | Deterministic anytime proposals, followed by exhaustive native enumeration. | `CandidateExpansion`, `CandidateFrontier`, `BeamCombatStrategy`, `DeterministicCandidates` | 11/11/9 |
| [generation_policy.py](../../game/ai/decisions/generation_policy.py) | One immutable candidate-generation policy shared by live and replay controllers. | `SearchGenerationPolicy` | 1/14/32 |
| [native_candidates.py](../../game/ai/decisions/native_candidates.py) | Cheap proposal ordering before target products and executable actions exist. | `NativeProposalContext`, `NativeCombatStrategy` | 2/0/0 |
| [search_actions.py](../../game/ai/decisions/search_actions.py) | Player-visible action identities for compatible sampled scenario recipes. | `UnsupportedPlayerAction`, `PlayerAction`, `PlayerActionBuilder` | 16/5/24 |
| [search_candidates.py](../../game/ai/decisions/search_candidates.py) | Deterministic, observation-only progressive widening of a legal menu. | `CandidateOrdering`, `ProgressiveWidening` | 1/2/1 |
| [search_context.py](../../game/ai/decisions/search_context.py) | Reviewed contextual identities; never reflect raw event/operation graphs. | `SearchContextBuilder` | 1/0/1 |
| [search_decision.py](../../game/ai/decisions/search_decision.py) | Shared live/replay boundary between native choices and player information. | `ProjectedDecision`, `DecisionProjector` | 4/19/14 |
| [search_descriptors.py](../../game/ai/decisions/search_descriptors.py) | Detached choices for replay; concrete actions are regenerated in each world. | `UnsupportedDescriptor`, `ChoiceDescriptor` | 5/3/10 |

## evaluation

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [action_priors.py](../../game/ai/evaluation/action_priors.py) | Replaceable action priors whose only input is acting-player information. | `ActionPrior`, `UniformActionPrior`, `EncodedActionPrior`, `HeuristicActionPrior` | 5/15/13 |
| [evaluation_cache.py](../../game/ai/evaluation/evaluation_cache.py) | Bounded RAM-only cache of detached numeric features and pure evaluations. | `CachedEvaluation`, `EvaluationCache` | 3/0/0 |
| [exact_policy_memory.py](../../game/ai/evaluation/exact_policy_memory.py) | Bounded RAM-only memoization of exact built-in per-action ratings. | `PolicyRating`, `PolicyMemoryEntry`, `ExactPolicyMemory` | 1/2/1 |
| [observation_policy.py](../../game/ai/evaluation/observation_policy.py) | Deterministic opponent prior over PlayerInformation, never a sampled State. | `ActionRating`, `ObservationOpponentPolicy`, `_Scorer` | 9/30/46 |
| [prepared_products.py](../../game/ai/evaluation/prepared_products.py) | Detached native ratings and value reuse over exact observation inputs. | `PureRatings` | 1/1/2 |
| [search_abstraction.py](../../game/ai/evaluation/search_abstraction.py) | Exact, consumer-scoped sufficient information for deterministic policy reuse. | `CounterEvidence`, `KnowledgeSummary`, `CanonicalAction`, `CanonicalPlayerView`, `CanonicalPolicyView` | 3/1/1 |
| [search_encoding.py](../../game/ai/evaluation/search_encoding.py) | Versioned, identity-free numerical inputs for future policy/value models. | `EncodedState`, `EncodedAction`, `StateEncoder`, `ActionEncoder`, `EncodingContext` | 5/1/1 |
| [semantic_encoding.py](../../game/ai/evaluation/semantic_encoding.py) | Versioned, data-only rule semantics; never execute effects or predicates. | `SemanticNode`, `CardSemanticEncoder` | 5/0/1 |
| [state_value.py](../../game/ai/evaluation/state_value.py) | Cheap observation-only baseline for future search leaves, not a win model. | `StateValueEvaluator` | 5/9/8 |
| [tactical_evaluation.py](../../game/ai/evaluation/tactical_evaluation.py) | Marginal tactical value; never remove a legal option from the engine. | funkce / kontrakty | 1/0/1 |

## experiments

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [actor_identity_cache.py](../../game/ai/experiments/actor_identity_cache.py) | Opt-in write receipts for a narrow native card identity path. | `CardReceipt` | 3/0/0 |
| [actor_identity_parts.py](../../game/ai/experiments/actor_identity_parts.py) | Named immutable components of the existing canonical observation identity. | `IdentityParts` | 1/0/0 |
| [combat_checkpoints.py](../../game/ai/experiments/combat_checkpoints.py) | Opt-in explicit combat continuations; never clone suspended Python stacks. | funkce / kontrakty | 0/1/1 |
| [compiled_dependencies.py](../../game/ai/experiments/compiled_dependencies.py) | Opt-in fused native attribute checks; mutable containers/permissions stay live. | funkce / kontrakty | 0/1/1 |
| [compiled_rule_descriptions.py](../../game/ai/experiments/compiled_rule_descriptions.py) | Opt-in, bounded projection of verified native structural description versions. | `RuleDescriptionCatalog` | 1/1/1 |
| [direct_selectors.py](../../game/ai/experiments/direct_selectors.py) | Loan-owned native selectors at measured State call sites; opt-in only. | funkce / kontrakty | 1/0/1 |
| [knowledge_groups.py](../../game/ai/experiments/knowledge_groups.py) | Bounded data-only knowledge groups, with live input checks on every read. | `KnowledgeArray`, `KnowledgeProduct`, `KnowledgeGroups` | 0/1/1 |
| [mana_payments.py](../../game/ai/experiments/mana_payments.py) | Bounded exact pool-payment memoization; activation/cost plans stay native. | funkce / kontrakty | 0/1/1 |
| [mutation_masks.py](../../game/ai/experiments/mutation_masks.py) | Explicit register seam for native integer masks; public Query.eval stays live. | `PreparedMaskLoans` | 1/0/2 |
| [mutation_player_rows.py](../../game/ai/experiments/mutation_player_rows.py) | Public player sub-digests guarded by the same revisions as journal undo. | funkce / kontrakty | 1/0/0 |
| [mutation_positions.py](../../game/ai/experiments/mutation_positions.py) | Explicit serial pilot: one mutation ledger drives root undo and public rows. | funkce / kontrakty | 0/2/8 |
| [mutation_queries.py](../../game/ai/experiments/mutation_queries.py) | Explicit native query pilot: reuse owned results, never live index buckets. | funkce / kontrakty | 1/0/1 |
| [mutation_world.py](../../game/ai/experiments/mutation_world.py) | Reviewed native write barriers for the extended, explicitly enabled world pilot. | `WorldAdapters` | 1/0/1 |
| [native_identity_parts.py](../../game/ai/experiments/native_identity_parts.py) | Native identity trials: batching and retained rule receipts; imports enable nothing. | `RuleReceipt` | 0/1/3 |
| [observation_mana.py](../../game/ai/experiments/observation_mana.py) | Experimental, viewer-independent mana projection reuse in one owned state. | funkce / kontrakty | 0/1/1 |
| [persistent_recall.py](../../game/ai/experiments/persistent_recall.py) | Opt-in bounded-tail recall storage with lazy tuple-compatible public exports. | `_Block`, `RecallChunks`, `PersistentRecall` | 0/1/1 |
| [public_players.py](../../game/ai/experiments/public_players.py) | Viewer-independent public player rows guarded by exact live scalar inputs. | funkce / kontrakty | 1/1/1 |
| [runtime_card_fields.py](../../game/ai/experiments/runtime_card_fields.py) | Owned rule fields with write invalidation and bounded pure-data sharing. | `RuleFieldValues`, `RuleFieldGroup` | 3/1/3 |
| [runtime_knowledge.py](../../game/ai/experiments/runtime_knowledge.py) | Small immutable knowledge rows with content-addressed, bounded leaf digests. | `ScalarKnowledgeRow` | 2/0/1 |
| [search_resources.py](../../game/ai/experiments/search_resources.py) | Isolated worker resource and GC experiments; imports activate no controls. | funkce / kontrakty | 0/3/2 |
| [static_catalog.py](../../game/ai/experiments/static_catalog.py) | Explicit reuse of an already published catalog, never of a live position. | `StaticCatalog` | 0/2/1 |
| [weak_fact_views.py](../../game/ai/experiments/weak_fact_views.py) | Opt-in cycle-free reverse edges preserving a live view's original shell. | `_DerivedView` | 0/1/2 |

## information

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [knowledge.py](../../game/ai/information/knowledge.py) | Viewer-filtered evidence and player memory; no hidden-state inference here. | `PlayerEvent`, `PlayerKnowledge` | 10/7/13 |
| [observation.py](../../game/ai/information/observation.py) | Player-visible snapshots, independent of candidate ranking and selectors. | `PlayerObservationBuilder` | 10/19/30 |
| [observation_characteristics.py](../../game/ai/information/observation_characteristics.py) | Reuse six proven native characteristics inside one owned runtime only. | funkce / kontrakty | 2/1/1 |
| [observation_projection.py](../../game/ai/information/observation_projection.py) | Temporary visibility proofs for one synchronous multi-player projection. | `VisibilityRead`, `DescriptionProjection`, `ProjectedCardRow` | 2/3/0 |
| [runtime_digest.py](../../game/ai/information/runtime_digest.py) | World/viewer-owned incremental identities, with the existing typed hash format. | `DigestReader`, `NativeSeed`, `RootDigestSeed`, `DigestSeedBank`, `DigestNode` | 7/6/9 |
| [runtime_field_cache.py](../../game/ai/information/runtime_field_cache.py) | World-owned rule fields with reusable dependency checks and typed identities. | `Dependencies`, `RuntimeFieldCache` | 4/2/5 |
| [runtime_observation.py](../../game/ai/information/runtime_observation.py) | State-owned visible readers and detached native decision products. | `SnapshotUnavailable`, `NativeReadSession`, `ObservationReaders`, `CardReadContract`, `RuntimeObservation`, `IdentityRecallEntry`, `PreparedView`, `PreparedObservation` | 16/8/17 |
| [search_observation.py](../../game/ai/information/search_observation.py) | Player-facing search input, separate from privileged simulator addresses. | `ActingPlayerObservation` | 6/4/7 |
| [search_recall.py](../../game/ai/information/search_recall.py) | Untruncated per-player recall within a supported simulation's lifetime. | `RecallEntry`, `PlayerRecall`, `PlayerInformation` | 11/15/30 |
| [snapshot_effects.py](../../game/ai/information/snapshot_effects.py) | Project only proven source-local hidden static bindings out of a snapshot. | funkce / kontrakty | 1/0/1 |
| [structured_values.py](../../game/ai/information/structured_values.py) | Immutable JSON-shaped facts, shared within one bounded projection store. | `Value`, `LexicalValue`, `ReadOnlyObject`, `ValueStore`, `StructuredJSON`, `DigestReference`, `RecallDigest` | 21/13/33 |
| [visible_effects.py](../../game/ai/information/visible_effects.py) | Reviewed declarative effect features, with no execution or callback inspection. | funkce / kontrakty | 6/0/1 |

## reference

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [perfect_information_search.py](../../game/ai/reference/perfect_information_search.py) | Budgeted two-player minimax for a fixed, explicitly known simulator world. | `SearchBudget`, `RootEvaluation`, `SearchMetrics`, `SearchResult`, `_Interrupted`, `PerfectInformationSearch`, `_SearchRun` | 0/2/2 |
| [prefix_replay_simulator.py](../../game/ai/reference/prefix_replay_simulator.py) | Faster replay of a trusted fixed scenario; no live-state cloning or threads. | `_PrefixReplay`, `PrefixReplaySimulator` | 0/1/5 |
| [sampled_scenario.py](../../game/ai/reference/sampled_scenario.py) | Explicit hidden-zone beliefs -> a fixed, fresh replay scenario. | `HiddenZoneBelief`, `SampledScenario` | 0/3/6 |

## sampling

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [belief.py](../../game/ai/sampling/belief.py) | Exact-deck baseline over explicit evidence, never a runtime library reader. | `SampledHiddenCards`, `KnownDeckModel`, `HiddenCardPool` | 1/0/1 |
| [current_position_worlds.py](../../game/ai/sampling/current_position_worlds.py) | Trusted bridge: public runtime + declared deck copies -> current-root worlds. | `_Game`, `CurrentPositionWorlds`, `_PreparedWorld`, `_RuleContinuation`, `_Snapshot` | 6/13/23 |
| [draw_prefix_evidence.py](../../game/ai/sampling/draw_prefix_evidence.py) | Conservative evidence about the untouched own post-setup library prefix. | `DrawPrefixEvidence` | 1/0/1 |
| [game_origin.py](../../game/ai/sampling/game_origin.py) | Explicit game recipes and observation-conditioned opening permutations. | `_Game`, `GameOrigin`, `GameOriginWorlds` | 4/7/16 |
| [history_diagnostics.py](../../game/ai/sampling/history_diagnostics.py) | Describe incompatible *observer* evidence without recording card values. | funkce / kontrakty | 1/0/1 |
| [history_world_provider.py](../../game/ai/sampling/history_world_provider.py) | Condition explicit scenario priors on player recall by rejection sampling. | `UniformHistoryPolicy`, `ExploratoryHistoryPolicy`, `HistoryWorldProvider`, `_HistorySample` | 3/3/5 |
| [incremental_world_provider.py](../../game/ai/sampling/incremental_world_provider.py) | Bounded bootstrap population over immutable history-compatible worlds. | `_Particle`, `IncrementalWorldProvider`, `_Cohort`, `_PopulationSample` | 1/3/5 |
| [library_positions.py](../../game/ai/sampling/library_positions.py) | Library positions justified by one player's detached recall. | `LibraryPositions` | 1/0/1 |
| [opening_reveal_evidence.py](../../game/ai/sampling/opening_reveal_evidence.py) | A narrow public constraint on an opponent's untouched opening permutation. | `OpeningRevealEvidence` | 2/0/1 |
| [perfect_information_sampling.py](../../game/ai/sampling/perfect_information_sampling.py) | Trusted bridge from memory-conditioned sampling to memory-free rule worlds. | `PerfectInformationSampler` | 1/0/1 |
| [scenario_world_provider.py](../../game/ai/sampling/scenario_world_provider.py) | Resume explicit scenario recipes using own recall and a fixed opponent. | `ScenarioWorldProvider`, `_ResumedScenario` | 2/3/3 |
| [setup_deal_evidence.py](../../game/ai/sampling/setup_deal_evidence.py) | Extract complete own setup deals from detached, untruncated player recall. | `SetupDealEvidence` | 1/1/2 |

## search

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [contracts.py](../../game/ai/search/contracts.py) | Immutable search inputs/results shared by IS-MCTS and PIMC. | `SampledRootMismatch`, `InformationSearchBudget`, `ActionEstimate`, `InformationSearchMetrics`, `InformationSearchResult` | 9/21/43 |
| [information_set_search.py](../../game/ai/search/information_set_search.py) | Bounded root-player information-set Monte Carlo against a fixed opponent. | `_Interrupted`, `InformationSetSearch`, `_Run` | 4/35/53 |
| [parallel_search.py](../../game/ai/search/parallel_search.py) | Root-parallel MCTS with isolated worlds, trees and deterministic reduction. | `ParallelInformationSetSearch` | 1/2/6 |
| [pimc_search.py](../../game/ai/search/pimc_search.py) | PIMC ensemble over full sampled runtimes, separate from information-set MCTS. | `_Node`, `PIMCMetrics`, `PIMCSearch`, `_SearchRun` | 1/1/2 |
| [puct_selection.py](../../game/ai/search/puct_selection.py) | Flat PUCT selection over completed, actor-information edge statistics. | `PUCTSelector` | 1/0/1 |
| [search_horizon.py](../../game/ai/search/search_horizon.py) | Shared boundary for scoring settled search leaves, not rule execution. | funkce / kontrakty | 2/2/1 |
| [search_statistics.py](../../game/ai/search/search_statistics.py) | Live-only search coverage and latency; sampled replay never records here. | `SearchStatistics` | 1/0/1 |
| [search_trajectory.py](../../game/ai/search/search_trajectory.py) | Object-owned traversal through cached prefixes and native continuations. | `TrajectoryTraversal`, `ContinuationTraversal` | 1/4/3 |
| [search_tree_memory.py](../../game/ai/search/search_tree_memory.py) | Connected, bounded actor-information subtree. No worlds or live bindings. | `EdgeStatistics`, `EdgeTable`, `TreeRow`, `RetainedNode`, `SearchTreeMemory` | 2/6/10 |

## simulation

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [checkpoint_simulator.py](../../game/ai/simulation/checkpoint_simulator.py) | Native sampled-world priority checkpoints, inspired by XMage state restore. | `_Checkpoint`, `_CheckpointReplay`, `CheckpointSimulator` | 2/14/16 |
| [mutation_position.py](../../game/ai/simulation/mutation_position.py) | Hybrid first-write records plus complete fallback preimages for one worker. | `MutationPosition` | 1/0/2 |
| [perfect_information_world.py](../../game/ai/simulation/perfect_information_world.py) | Memory-free, synchronous full-rules execution of one sampled world. | `FinishedTrajectory`, `InterruptedTrajectory`, `RuntimeStateValue`, `NativeMenu`, `NativeDecision`, `PerfectInformationController`, `PerfectInformationWorld` | 2/0/2 |
| [position_copy.py](../../game/ai/simulation/position_copy.py) | Compiled native record copies within one never-executed position template. | `RecordCopyPlan` | 1/0/1 |
| [position_values.py](../../game/ai/simulation/position_values.py) | Narrow immutable values reusable inside one stable-handle position recipe. | funkce / kontrakty | 1/1/1 |
| [priority_position.py](../../game/ai/simulation/priority_position.py) | Reusable dynamic positions over stable handles in one OWNED sampled world. | `PriorityPosition` | 3/5/8 |
| [reversible_world.py](../../game/ai/simulation/reversible_world.py) | Reusable preimages of an owned native simulation graph. | `WorldSavepoint` | 4/1/5 |
| [runtime_clone.py](../../game/ai/simulation/runtime_clone.py) | Private graph copying for native search scenarios, never a policy input. | `IdentityBindings`, `DictionaryCopyPlans`, `UnsupportedSnapshot`, `InaccessibleHistorySource`, `RuntimeClone` | 12/6/30 |
| [search_simulator.py](../../game/ai/simulation/search_simulator.py) | Reference search simulator using deterministic replay through the real engine. | `ReplayDiverged`, `SimulationBudgetExceeded`, `HistorySamplingExhausted`, `SimulationHistoryUnavailable`, `WorldInitializationMetrics`, `DecisionAddress`, `RecordedChoice`, `_Projection`, `SimulationNode`, `_DecisionBoundary`, `ForcedBoundary`, `_ReplayController`, `_Replay`, `SearchSimulator` | 14/35/63 |
| [simulation_clone.py](../../game/ai/simulation/simulation_clone.py) | Fast ownership copy of trusted simulation graphs after visibility filtering. | `SimulationClone` | 3/6/7 |
| [simulation_world.py](../../game/ai/simulation/simulation_world.py) | Owned native-world copies shared by root templates and branch checkpoints. | funkce / kontrakty | 5/14/17 |
| [world_mutation_contracts.py](../../game/ai/simulation/world_mutation_contracts.py) | Reviewed native attribute and container ownership for world mutation loans. | funkce / kontrakty | 2/0/1 |

## training

| Modul | Účel podle zdroje | Třídy | G/B/T |
| --- | --- | --- | ---: |
| [deep_sets_model.py](../../game/ai/training/deep_sets_model.py) | CPU Deep Sets policy/value model with explicit entity/argument relations. | `DeepSetsPolicyValueModel` | 2/0/2 |
| [entity_features.py](../../game/ai/training/entity_features.py) | Schema 2 ragged actor entities, semantic trees and complete action arguments. | `EntityState`, `EntityArgument`, `EntityAction`, `EntityFeatureEncoder` | 2/0/1 |
| [self_play.py](../../game/ai/training/self_play.py) | Synchronous AlphaZero-style actors -> in-memory replay -> learner. | `_ConsoleTail` | 1/0/6 |
| [self_play_config.py](../../game/ai/training/self_play_config.py) | Explicit actor/learner configuration, separate from live search parallelism. | `SelfPlayConfig` | 2/0/6 |
| [self_play_data.py](../../game/ai/training/self_play_data.py) | Terminal-labelled examples, visit targets and bounded learner-owned replay. | `TrainingExample`, `EpisodeRecorder`, `ReplayBuffer` | 1/0/2 |
| [self_play_search.py](../../game/ai/training/self_play_search.py) | Root-only Dirichlet noise; no worker pools or changes at interior nodes. | `SelfPlaySearch` | 1/0/1 |
| [training_features.py](../../game/ai/training/training_features.py) | Versioned pooled baseline over the existing identity-free graph encoders. | funkce / kontrakty | 2/0/3 |
| [training_model.py](../../game/ai/training/training_model.py) | Small CPU policy/value network; optional NumPy dependency, ragged legal menus. | `PolicyValueModel`, `NeuralPolicy`, `NeuralPrior`, `NeuralValue` | 4/1/7 |
