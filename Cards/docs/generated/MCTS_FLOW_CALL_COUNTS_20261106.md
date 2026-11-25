# Počty volání a konstruktorů MCTS — 2026-11-06

Doplňuje [flow search](../MCTS_SEARCH_FLOW_20261106.md). Profil je `cProfile` pouze v RAM; žádný `.prof` soubor nebyl vytvořen. Obě série 128/4/8 zachovaly celou odpověď, deset work counters i počty native/cache práce vůči vlastní neprofilované referenci.

| Kořen | Reference wall / CPU, s | cProfile wall / CPU, s |
| --- | --- | --- |
| 87 | 46,0194 / 45,9531 | 121,3732 / 121,0313 |
| 102 | 45,2723 / 45,1094 | 113,5571 / 112,7031 |

`self` je čas těla funkce bez podvolání; `inclusive` zahrnuje její potomky. Inclusive časy rodičů a dětí se nesčítají. Toto je jiná instrumentovaná série než exkluzivní wall/CPU tabulka v hlavním reportu. Časy cProfile nejsou latencí běžného search. GC zde není oddělená časová kategorie. U generátorů může počet volání obsahovat jejich obnovení po `yield`; není to počet nových instancí generátoru.

## Funkce

Profil obaluje `sample()`: zahrnuje i krátké vytvoření detached planneru a instalaci benchmarkového kontextu. Wall/CPU stopwatch výše měří samotný search. Root capture a tvorba reportu jsou mimo profil. Profiler neměnil logickou/cache práci; počty GC kolekcí se mezi referencí a instrumentací mohou lišit.

Tabulka obsahuje sjednocení nejdražších funkcí podle self/inclusive času a všech zachycených funkcí hlavních modulů search/traversal/runtime/retention. Není to seznam všech funkcí celého interpretu. Uvádí maximálně tři hlavní zaznamenané přímé rodiče z prvního kořene, případně z druhého. Umístění ve zdroji zůstává autoritativní identifikátor.

| Funkce a zdroj | Volání 87 | Volání 102 | Self s 87 / 102 | Inclusive s 87 / 102 | Přímé okolí / rodiče |
| --- | ---: | ---: | ---: | ---: | --- |
| [game/ai/information/structured_values.py:344:structural_digest](../../game/ai/information/structured_values.py#L344) | 4 646 091 | 5 147 969 | 16,6276 / 18,1692 | 58,0758 / 59,8909 | game/ai/information/runtime_observation.py:390:prepared_view (1 995)<br>game/ai/information/structured_values.py:344:structural_digest (4 582 925)<br>game/ai/information/runtime_field_cache.py:26:effect_inputs (59 850) |
| [game/ai/simulation/simulation_clone.py:98:copy](../../game/ai/simulation/simulation_clone.py#L98) | 3 771 148 | 2 595 785 | 7,7868 / 5,7871 | 15,0864 / 10,8267 | game/ai/simulation/simulation_clone.py:98:copy (3 512 195)<br>game/ai/simulation/simulation_world.py:104:copy_world (210)<br>game/ai/simulation/simulation_clone.py:182:&lt;genexpr&gt; (50 400) |
| [game/ai/information/runtime_observation.py:134:__getitem__](../../game/ai/information/runtime_observation.py#L134) | 3 527 587 | 4 105 643 | 2,9063 / 3,3334 | 27,4208 / 27,7235 | game/ai/information/structured_values.py:344:structural_digest (2 102 730)<br>&lt;frozen _collections_abc&gt;:804:get (1 326 606)<br>game/ai/evaluation/state_value.py:33:evaluate (57 049) |
| [game/ai/information/runtime_observation.py:142:read](../../game/ai/information/runtime_observation.py#L142) | 2 102 730 | 2 387 072 | 2,6946 / 3,0366 | 22,1588 / 21,7107 | game/ai/information/runtime_observation.py:134:__getitem__ (2 102 730) |
| [game/ai/information/structured_values.py:399:digest](../../game/ai/information/structured_values.py#L399) | 1 146 218 | 1 588 240 | 1,8716 / 2,5766 | 2,7357 / 3,7433 | game/ai/information/structured_values.py:393:structural_key (10 192)<br>game/ai/information/structured_values.py:399:digest (1 136 026) |
| [game/ai/information/structured_values.py:188:freeze](../../game/ai/information/structured_values.py#L188) | 397 561 | 297 818 | 1,7319 / 1,0329 | 3,2272 / 2,1067 | game/ai/information/structured_values.py:188:freeze (292 495)<br>game/ai/information/structured_values.py:227:&lt;genexpr&gt; (83 997)<br>game/ai/information/runtime_field_cache.py:183:read (10 094) |
| [game/game_state/card.py:708:get_stat](../../game/game_state/card.py#L708) | 1 315 936 | 1 061 666 | 1,4932 / 1,1950 | 7,4878 / 3,9732 | game/game_state/card.py:820:get_controller (263 186)<br>game/game_state/card.py:1273:has_keyword (219 492)<br>game/game_state/card.py:1004:get_types (222 623) |
| [game/ai/evaluation/search_abstraction.py:86:_counter](../../game/ai/evaluation/search_abstraction.py#L86) | 2 094 030 | 2 418 150 | 1,2505 / 1,4665 | 6,9583 / 8,5792 | game/ai/evaluation/search_abstraction.py:66:observe (40 827)<br>game/ai/evaluation/search_abstraction.py:94:&lt;genexpr&gt; (298 959)<br>game/ai/evaluation/search_abstraction.py:92:&lt;genexpr&gt; (1 754 244) |
| [game/ai/information/observation.py:70:knows_identity](../../game/ai/information/observation.py#L70) | 1 764 964 | 1 899 041 | 1,3373 / 1,4300 | 2,8136 / 3,0226 | game/ai/information/runtime_observation.py:134:__getitem__ (1 236 900)<br>game/ai/information/observation.py:362:observe_knowledge (177 568)<br>game/ai/information/runtime_observation.py:127:__iter__ (112 797) |
| [helper/runtime_object.py:72:__hash__](../../helper/runtime_object.py#L72) | 4 417 829 | 3 350 872 | 1,4286 / 1,1632 | 2,4570 / 2,0117 | game/ai/information/observation.py:70:knows_identity (1 764 964)<br>game/ai/decisions/generation_candidates.py:168:&lt;genexpr&gt; (667 160)<br>game/ai/information/runtime_observation.py:222:read (363 090) |
| [game/ai/information/observation.py:107:_describe](../../game/ai/information/observation.py#L107) | 243 034 | 236 232 | 1,0617 / 1,3034 | 3,6977 / 3,5741 | game/ai/information/observation.py:78:describe (243 034) |
| [game/ai/decisions/search_descriptors.py:32:freeze](../../game/ai/decisions/search_descriptors.py#L32) | 456 847 | 546 309 | 0,9357 / 1,2650 | 2,6709 / 3,9048 | game/ai/decisions/search_descriptors.py:121:from_option (12 868)<br>game/ai/decisions/search_descriptors.py:60:encode (443 979) |
| [game/ai/evaluation/search_abstraction.py:92:&lt;genexpr&gt;](../../game/ai/evaluation/search_abstraction.py#L92) | 2 224 413 | 2 568 090 | 0,9077 / 1,1791 | 6,5336 / 8,0865 | &lt;built-in method builtins.any&gt; (2 224 413) |
| [game/ai/information/runtime_field_cache.py:39:matches](../../game/ai/information/runtime_field_cache.py#L39) | 87 661 | 101 950 | 0,8304 / 0,7524 | 3,2572 / 3,4840 | game/ai/information/runtime_field_cache.py:183:read (87 661) |
| [game/game_actions/resolution/cost_transaction.py:97:watch](../../game/game_actions/resolution/cost_transaction.py#L97) | 3 092 | 1 200 | 0,8212 / 0,7527 | 1,6669 / 1,3637 | game/game_actions/resolution/cost_transaction.py:37:__init__ (907)<br>game/game_actions/resolution/cost_transaction.py:303:watch_operations (1 790)<br>game/game_actions/resolution/cost_index_checkpoint.py:20:before_update (287) |
| [game/ai/information/observation.py:78:describe](../../game/ai/information/observation.py#L78) | 406 896 | 400 879 | 0,7520 / 0,7386 | 4,5553 / 4,4679 | game/ai/information/runtime_observation.py:142:read (185 535)<br>game/ai/information/observation.py:107:_describe (176 811)<br>game/ai/information/observation.py:180:_ability (11 124) |
| [game/ai/information/structured_values.py:168:__iter__](../../game/ai/information/structured_values.py#L168) | — | 542 430 | — / 0,6633 | — / 0,7279 | &lt;frozen _collections_abc&gt;:892:__iter__ (542 430) |
| [game/ai/information/knowledge.py:39:event_view](../../game/ai/information/knowledge.py#L39) | 84 691 | — | 0,5872 / — | 0,8779 / — | game/ai/information/knowledge.py:151:view (84 691) |
| [game/game_state/native_modifier_plan.py:130:&lt;genexpr&gt;](../../game/game_state/native_modifier_plan.py#L130) | 72 884 | — | 0,5840 / — | 0,5840 / — | game/game_state/native_modifier_plan.py:127:tracked_native_sources (72 884) |
| [game/game_state/card.py:833:key](../../game/game_state/card.py#L833) | 4 720 386 | 3 445 883 | 0,5781 / 0,4689 | 0,5781 / 0,4689 | helper/runtime_object.py:72:__hash__ (4 249 944)<br>game/game_state/card_snapshot_cache.py:144:_eligible (111 275)<br>game/game_state/native_modifier_plan.py:243:read_planned_value (107 315) |
| [game/ai/information/structured_values.py:158:__getitem__](../../game/ai/information/structured_values.py#L158) | 2 065 310 | 2 343 858 | 0,4582 / 0,5203 | 0,9056 / 0,8624 | &lt;frozen _collections_abc&gt;:892:__iter__ (1 839 849)<br>game/ai/evaluation/observation_policy.py:240:combat (223 556)<br>game/ai/search/search_horizon.py:4:quiet_priority (816) |
| [game/ai/information/runtime_observation.py:216:builder](../../game/ai/information/runtime_observation.py#L216) | 3 986 530 | 4 631 159 | 0,4337 / 0,5153 | 0,4337 / 0,5153 | game/ai/information/runtime_observation.py:134:__getitem__ (3 527 587)<br>game/ai/information/runtime_observation.py:206:ability (105 735)<br>game/ai/information/runtime_observation.py:127:__iter__ (112 797) |
| [game/game_state/native_modifier_plan.py:148:sources_empty](../../game/game_state/native_modifier_plan.py#L148) | 1 265 368 | 1 036 393 | 0,4783 / 0,3947 | 1,4827 / 0,6520 | game/game_state/card.py:708:get_stat (1 265 368) |
| [game/game_state/card_mana_fields.py:34:mana_fields](../../game/game_state/card_mana_fields.py#L34) | 61 845 | 70 208 | 0,4375 / 0,4769 | 1,9831 / 2,0341 | game/game_state/card.py:933:get_mana_fields (61 845) |
| [helper/query_system/object_register.py:119:query](../../helper/query_system/object_register.py#L119) | 559 634 | 520 551 | 0,4551 / 0,4038 | 1,9124 / 1,9369 | game/game_state/registers/indexed_register.py:245:query (514 286)<br>game/game_state/registers/effect_register.py:92:&lt;genexpr&gt; (33 228)<br>game/game_actions/triggers/trigger_registry.py:71:capture (4 602) |
| [game/game_state/ability_definition_cache.py:22:native_ability_definitions](../../game/game_state/ability_definition_cache.py#L22) | 117 297 | 101 330 | 0,4469 / 0,3403 | 1,7952 / 1,3267 | game/game_state/card.py:1043:get_ability_defs (117 297) |
| [game/ai/information/runtime_field_cache.py:132:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L132) | 51 294 | 38 595 | 0,4392 / 0,2226 | 0,4475 / 0,2289 | game/ai/information/runtime_field_cache.py:76:&lt;genexpr&gt; (51 294) |
| [game/ai/evaluation/state_value.py:9:permanent_value](../../game/ai/evaluation/state_value.py#L9) | 106 126 | 131 165 | 0,3574 / 0,4369 | 1,1808 / 1,4520 | game/ai/evaluation/state_value.py:33:evaluate (57 049)<br>game/ai/evaluation/observation_policy.py:128:value (49 077) |
| [game/game_state/card.py:576:state](../../game/game_state/card.py#L576) | 4 239 722 | 4 490 513 | 0,4142 / 0,4365 | 0,4142 / 0,4365 | game/ai/information/runtime_observation.py:142:read (2 102 730)<br>game/ai/information/observation.py:70:knows_identity (1 764 964)<br>game/game_state/card_snapshot_cache.py:144:_eligible (106 253) |
| [game/ai/information/runtime_field_cache.py:183:read](../../game/ai/information/runtime_field_cache.py#L183) | 167 580 | 190 878 | 0,3856 / 0,4347 | 8,9465 / 7,8673 | game/ai/information/runtime_field_cache.py:156:ability (105 735)<br>game/ai/information/runtime_field_cache.py:173:describe (61 845) |
| [game/game_state/modifier.py:1071:only_empty_builtin_sources](../../game/game_state/modifier.py#L1071) | 305 529 | 172 693 | 0,4317 / 0,2798 | 0,5514 / 0,3640 | game/game_state/native_modifier_plan.py:159:live_sources_empty (66 308)<br>game/game_state/stat.py:181:_get_modifier_pipeline (143 113)<br>game/game_state/card.py:906:modifiers_to_attach (61 845) |
| [game/game_state/native_modifier_plan.py:159:live_sources_empty](../../game/game_state/native_modifier_plan.py#L159) | 304 900 | 247 998 | 0,4172 / 0,3680 | 0,7255 / 0,5790 | game/game_state/card_snapshot_cache.py:144:_eligible (110 795)<br>game/game_state/ability_definition_cache.py:22:native_ability_definitions (114 476)<br>game/ai/information/observation_characteristics.py:10:native_characteristics (61 845) |
| [game/game_state/stat.py:181:_get_modifier_pipeline](../../game/game_state/stat.py#L181) | 144 727 | — | 0,3961 / — | 2,0224 / — | game/game_state/stat.py:270:get_stat (144 727) |
| [game/ai/simulation/runtime_clone.py:32:get](../../game/ai/simulation/runtime_clone.py#L32) | 792 984 | 658 286 | 0,3751 / 0,3172 | 1,6460 / 1,0332 | game/ai/simulation/simulation_clone.py:98:copy (789 140)<br>game/ai/simulation/runtime_clone.py:160:copy (3 844) |
| [game/ai/decisions/search_actions.py:151:encode](../../game/ai/decisions/search_actions.py#L151) | — | 190 608 | — / 0,3607 | — / 1,2039 | game/ai/decisions/search_actions.py:144:action (6 714)<br>game/ai/decisions/search_actions.py:151:encode (118 841)<br>game/ai/decisions/search_context.py:136:resolution (17 548) |
| [game/game_state/card_snapshot_cache.py:102:_index_definition_signature](../../game/game_state/card_snapshot_cache.py#L102) | 91 471 | — | 0,3110 / — | 0,9104 / — | game/game_state/card_snapshot_cache.py:157:has_current_index_projection (65 316)<br>game/game_state/layer_index_plan.py:32:modified_stats (22 921)<br>game/game_state/card_snapshot_cache.py:195:_capture (3 234) |
| [game/game_actions/data_structs/ability.py:875:__init__](../../game/game_actions/data_structs/ability.py#L875) | 14 598 | — | 0,3055 / — | 0,4171 / — | game/game_actions/data_structs/ability.py:1248:__init__ (10 476)<br>game/game_actions/data_structs/ability.py:709:to_ability (2 124)<br>game/game_actions/data_structs/ability.py:782:to_ability (1 998) |
| [game/game_state/continuous_rules.py:71:refresh_continuous_effects](../../game/game_state/continuous_rules.py#L71) | 281 140 | — | 0,3036 / — | 5,4026 / — | game/game_state/state.py:182:refresh_continuous_effects (281 140) |
| [game/ai/information/runtime_field_cache.py:45:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L45) | 831 885 | 948 135 | 0,2693 / 0,3009 | 0,4083 / 0,4574 | &lt;built-in method builtins.any&gt; (831 885) |
| [game/game_state/card_snapshot_cache.py:85:definition_index_signature](../../game/game_state/card_snapshot_cache.py#L85) | 181 343 | — | 0,2990 / — | 0,3856 / — | game/game_state/registers/layer_candidate_register.py:56:select (107 062)<br>game/game_state/card_snapshot_cache.py:102:_index_definition_signature (74 281) |
| [game/ai/information/structured_values.py:111:__lt__](../../game/ai/information/structured_values.py#L111) | — | 19 630 | — / 0,2957 | — / 0,5922 | &lt;built-in method builtins.sorted&gt; (19 630) |
| [game/mana/mana_value.py:28:_positive_counts](../../game/mana/mana_value.py#L28) | 50 970 | 40 066 | 0,2917 / 0,2558 | 0,4403 / 0,3587 | game/mana/mana_value.py:169:__post_init__ (10 620)<br>game/mana/mana_value.py:535:__init__ (15 524)<br>game/mana/mana_value.py:567:__new__ (4 300) |
| [game/game_state/modifier.py:887:get_modifiers](../../game/game_state/modifier.py#L887) | 109 810 | — | 0,2873 / — | 0,9022 / — | game/game_state/stat.py:181:_get_modifier_pipeline (109 810) |
| [game/ai/information/structured_values.py:161:get](../../game/ai/information/structured_values.py#L161) | — | 876 431 | — / 0,2853 | — / 0,7063 | game/ai/evaluation/search_abstraction.py:86:_counter (640 920)<br>game/ai/evaluation/state_value.py:27:&lt;genexpr&gt; (184 284)<br>game/ai/evaluation/observation_policy.py:113:card (24 844) |
| [game/game_state/card.py:583:zone](../../game/game_state/card.py#L583) | 1 256 303 | 979 785 | 0,2674 / 0,2130 | 0,2674 / 0,2130 | game/game_state/card.py:590:get_zone (1 245 256)<br>game/game_state/card.py:1081:get_activatable_ability_defs (9 049)<br>game/game_state/card.py:1117:try_find_ability (1 998) |
| [game/game_state/card.py:590:get_zone](../../game/game_state/card.py#L590) | 1 245 256 | 972 225 | 0,2646 / 0,2157 | 0,5294 / 0,4268 | game/ai/information/runtime_observation.py:311:&lt;genexpr&gt; (247 380)<br>game/ai/information/observation.py:376:&lt;genexpr&gt; (355 136)<br>game/ai/information/runtime_observation.py:142:read (123 690) |
| [game/ai/simulation/simulation_clone.py:157:&lt;genexpr&gt;](../../game/ai/simulation/simulation_clone.py#L157) | 463 216 | — | 0,2600 / — | 0,2600 / — | &lt;built-in method builtins.all&gt; (382 110)<br>game/ai/simulation/simulation_clone.py:98:copy (81 106) |
| [game/game_state/stat.py:270:get_stat](../../game/game_state/stat.py#L270) | 144 727 | — | 0,2549 / — | 2,6507 / — | game/game_state/card.py:708:get_stat (144 727) |
| [game/ai/information/runtime_field_cache.py:80:watch](../../game/ai/information/runtime_field_cache.py#L80) | 94 348 | 71 145 | 0,2490 / 0,1898 | 1,4368 / 0,8888 | game/ai/information/runtime_field_cache.py:157:prepare (29 664)<br>game/ai/information/runtime_field_cache.py:80:watch (58 298)<br>game/ai/information/runtime_field_cache.py:176:prepare (6 386) |
| [game/game_actions/resolution/replacement_effects.py:452:&lt;genexpr&gt;](../../game/game_actions/resolution/replacement_effects.py#L452) | — | 3 075 | — / 0,2387 | — / 0,2387 | game/game_actions/resolution/replacement_effects.py:380:replace (3 075) |
| [game/game_actions/generation/target_binding_generator.py:52:generate](../../game/game_actions/generation/target_binding_generator.py#L52) | — | 12 672 | — / 0,2361 | — / 0,2825 | game/game_actions/generation/exec_plan_gen_pipeline.py:65:generate (12 672) |
| [game/ai/information/observation.py:48:_gate](../../game/ai/information/observation.py#L48) | — | 1 942 365 | — / 0,2260 | — / 0,2328 | game/ai/information/observation.py:70:knows_identity (1 899 041)<br>game/ai/information/observation.py:59:reference (43 324) |
| [game/ai/information/visible_effects.py:8:effect_features](../../game/ai/information/visible_effects.py#L8) | — | 78 560 | — / 0,2240 | — / 0,2427 | game/ai/information/runtime_field_cache.py:26:effect_inputs (65 820)<br>game/ai/decisions/search_actions.py:151:encode (6 776)<br>game/ai/information/observation.py:107:_describe (4 904) |
| [game/mana/mana_value.py:666:generate_mana_options_from_req_pool](../../game/mana/mana_value.py#L666) | — | 1 106 | — / 0,2221 | — / 0,2576 | &lt;built-in method builtins.next&gt; (1 106) |
| [game/ai/information/observation_characteristics.py:10:native_characteristics](../../game/ai/information/observation_characteristics.py#L10) | — | 70 208 | — / 0,2214 | — / 1,1935 | game/ai/information/runtime_observation.py:142:read (70 208) |
| [game/game_state/ability_definition_cache.py:61:&lt;genexpr&gt;](../../game/game_state/ability_definition_cache.py#L61) | — | 44 774 | — / 0,2130 | — / 0,2130 | &lt;built-in method builtins.all&gt; (44 774) |
| [game/ai/search/search_tree_memory.py:80:_charge](../../game/ai/search/search_tree_memory.py#L80) | 17 | 43 | 0,1849 / 0,2053 | 0,3901 / 0,4392 | game/ai/search/search_tree_memory.py:186:commit (17) |
| [game/ai/information/runtime_observation.py:311:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L311) | 69 825 | 78 984 | 0,1820 / 0,2014 | 0,5991 / 0,6567 | game/ai/information/runtime_observation.py:293:read_field (69 825) |
| [game/ai/information/runtime_observation.py:132:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L132) | 1 783 320 | 2 311 295 | 0,1355 / 0,1781 | 0,1355 / 0,1781 | &lt;built-in method builtins.sum&gt; (1 783 320) |
| [game/ai/evaluation/search_abstraction.py:94:&lt;genexpr&gt;](../../game/ai/evaluation/search_abstraction.py#L94) | — | 908 460 | — / 0,1726 | — / 8,4751 | &lt;built-in method builtins.any&gt; (908 460) |
| [game/ai/information/runtime_field_cache.py:26:effect_inputs](../../game/ai/information/runtime_field_cache.py#L26) | 59 850 | 65 820 | 0,1359 / 0,1452 | 1,4247 / 1,5409 | game/ai/information/runtime_field_cache.py:39:matches (53 670)<br>game/ai/information/runtime_field_cache.py:80:watch (6 180) |
| [game/ai/information/runtime_observation.py:206:ability](../../game/ai/information/runtime_observation.py#L206) | 105 735 | 120 670 | 0,1200 / 0,1394 | 8,6019 / 7,4713 | game/ai/information/runtime_observation.py:178:&lt;genexpr&gt; (93 765)<br>game/ai/information/runtime_observation.py:179:&lt;genexpr&gt; (11 970) |
| [game/ai/information/runtime_observation.py:222:read](../../game/ai/information/runtime_observation.py#L222) | 3 990 | 4 388 | 0,1259 / 0,1385 | 2,2596 / 2,4917 | &lt;built-in method builtins.next&gt; (3 990) |
| [game/ai/information/runtime_observation.py:127:__iter__](../../game/ai/information/runtime_observation.py#L127) | 112 797 | 136 245 | 0,1146 / 0,1335 | 0,3475 / 0,4048 | game/ai/information/structured_values.py:344:structural_digest (61 845)<br>game/ai/information/runtime_observation.py:131:__len__ (50 952) |
| [game/ai/information/runtime_field_cache.py:156:ability](../../game/ai/information/runtime_field_cache.py#L156) | 105 735 | 120 670 | 0,1090 / 0,1242 | 8,4505 / 7,2955 | game/ai/information/runtime_observation.py:206:ability (105 735) |
| [game/ai/information/runtime_observation.py:293:read_field](../../game/ai/information/runtime_observation.py#L293) | 38 583 | 42 410 | 0,1146 / 0,1192 | 4,0057 / 3,1535 | game/ai/information/runtime_observation.py:285:__getitem__ (38 583) |
| [game/ai/information/runtime_field_cache.py:173:describe](../../game/ai/information/runtime_field_cache.py#L173) | 61 845 | 70 208 | 0,1031 / 0,1182 | 0,8489 / 0,9749 | game/ai/information/runtime_observation.py:211:describe (61 845) |
| [game/ai/information/runtime_observation.py:404:project_decision](../../game/ai/information/runtime_observation.py#L404) | 1 317 | 1 470 | 0,0965 / 0,1067 | 72,2889 / 76,9362 | game/ai/simulation/search_simulator.py:499:_decide_options (1 317) |
| [game/ai/sampling/current_position_worlds.py:303:materialize](../../game/ai/sampling/current_position_worlds.py#L303) | 187 | 155 | 0,1049 / 0,0850 | 15,3560 / 12,9492 | game/ai/sampling/current_position_worlds.py:337:__call__ (187) |
| [game/ai/information/runtime_observation.py:211:describe](../../game/ai/information/runtime_observation.py#L211) | 61 845 | 70 208 | 0,0821 / 0,0949 | 0,9494 / 1,0910 | game/ai/information/runtime_observation.py:142:read (61 845) |
| [game/ai/simulation/search_simulator.py:499:_decide_options](../../game/ai/simulation/search_simulator.py#L499) | 2 864 | 1 615 | 0,0932 / 0,0687 | 79,4953 / 85,2963 | game/ai/simulation/search_simulator.py:437:decide (2 810)<br>game/ai/simulation/search_simulator.py:480:expand_candidates (54) |
| [game/ai/information/runtime_observation.py:269:card](../../game/ai/information/runtime_observation.py#L269) | 61 845 | 70 208 | 0,0712 / 0,0777 | 0,1775 / 0,1934 | game/ai/information/runtime_observation.py:311:&lt;genexpr&gt; (61 845) |
| [game/ai/evaluation/search_abstraction.py:66:observe](../../game/ai/evaluation/search_abstraction.py#L66) | — | 1 470 | — / 0,0761 | — / 8,8248 | game/ai/information/runtime_observation.py:404:project_decision (1 470) |
| [game/ai/information/runtime_observation.py:178:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L178) | 155 610 | 177 714 | 0,0648 / 0,0733 | 7,2568 / 6,5506 | game/ai/information/runtime_observation.py:142:read (155 610) |
| [game/ai/information/runtime_observation.py:285:__getitem__](../../game/ai/information/runtime_observation.py#L285) | 76 143 | 83 581 | 0,0606 / 0,0672 | 4,0762 / 3,2320 | game/ai/information/structured_values.py:344:structural_digest (39 222)<br>&lt;frozen _collections_abc&gt;:804:get (18 327)<br>game/ai/information/runtime_observation.py:390:prepared_view (9 297) |
| [game/ai/simulation/simulation_world.py:104:copy_world](../../game/ai/simulation/simulation_world.py#L104) | 210 | 155 | 0,0590 / 0,0427 | 16,6142 / 11,9573 | game/ai/sampling/current_position_worlds.py:303:materialize (187)<br>game/ai/simulation/checkpoint_simulator.py:62:copy (23) |
| [game/ai/simulation/search_simulator.py:437:decide](../../game/ai/simulation/search_simulator.py#L437) | 2 810 | 1 554 | 0,0581 / 0,0323 | 88,9811 / 91,9816 | game/ai/simulation/checkpoint_simulator.py:97:decide (2 810) |
| [game/ai/information/runtime_field_cache.py:76:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L76) | 95 790 | 72 075 | 0,0553 / 0,0395 | 0,5236 / 0,2838 | game/ai/information/runtime_field_cache.py:75:attrs (95 790) |
| [game/ai/information/runtime_field_cache.py:48:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L48) | 137 753 | 157 003 | 0,0440 / 0,0490 | 0,0440 / 0,0490 | &lt;built-in method builtins.any&gt; (137 753) |
| [game/ai/decisions/generation_candidates.py:266:generate](../../game/ai/decisions/generation_candidates.py#L266) | 11 799 | — | 0,0431 / — | 9,4383 / — | &lt;built-in method builtins.next&gt; (11 129)<br>&lt;method 'close' of 'generator' objects&gt; (670) |
| [game/ai/information/runtime_field_cache.py:75:attrs](../../game/ai/information/runtime_field_cache.py#L75) | 20 188 | 15 190 | 0,0424 / 0,0306 | 0,5696 / 0,3171 | game/ai/information/runtime_field_cache.py:80:watch (12 772)<br>game/ai/information/runtime_field_cache.py:157:prepare (7 416) |
| [game/ai/information/runtime_observation.py:131:__len__](../../game/ai/information/runtime_observation.py#L131) | 50 952 | 66 037 | 0,0320 / 0,0424 | 0,4538 / 0,5942 | game/ai/evaluation/observation_policy.py:128:value (49 077)<br>game/ai/evaluation/observation_policy.py:321:combat_damage (1 125)<br>game/ai/evaluation/observation_policy.py:134:damage (750) |
| [game/ai/information/runtime_observation.py:156:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L156) | 61 845 | 70 208 | 0,0311 / 0,0358 | 0,0859 / 0,0973 | game/ai/information/runtime_observation.py:142:read (61 845) |
| [game/ai/information/runtime_observation.py:390:prepared_view](../../game/ai/information/runtime_observation.py#L390) | 1 995 | 2 194 | 0,0312 / 0,0328 | 59,1116 / 61,0165 | game/ai/information/runtime_observation.py:404:project_decision (1 995) |
| [game/ai/search/information_set_search.py:931:trajectory_choices](../../game/ai/search/information_set_search.py#L931) | 2 528 | 1 700 | 0,0312 / 0,0264 | 0,3997 / 0,4378 | &lt;method 'send' of 'generator' objects&gt; (2 400)<br>&lt;built-in method builtins.next&gt; (128) |
| [game/game_actions/resolution/replacement_effects.py:380:replace](../../game/game_actions/resolution/replacement_effects.py#L380) | 2 214 | 1 205 | 0,0310 / 0,0215 | 18,6359 / 17,3196 | game/game_actions/resolution/resolution_engine.py:457:_apply_replacement_effects (2 214) |
| [game/ai/simulation/simulation_clone.py:182:&lt;genexpr&gt;](../../game/ai/simulation/simulation_clone.py#L182) | 27 720 | 20 460 | 0,0290 / 0,0221 | 11,3790 / 8,1325 | &lt;method 'update' of 'dict' objects&gt; (27 720) |
| [game/ai/information/runtime_field_cache.py:55:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L55) | 64 404 | 73 404 | 0,0183 / 0,0254 | 0,0183 / 0,0254 | &lt;built-in method builtins.any&gt; (64 404) |
| [game/ai/information/runtime_observation.py:303:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L303) | 5 985 | 6 582 | 0,0235 / 0,0252 | 0,0558 / 0,0596 | game/ai/information/runtime_observation.py:293:read_field (5 985) |
| [game/ai/information/runtime_field_cache.py:157:prepare](../../game/ai/information/runtime_field_cache.py#L157) | 3 708 | 2 790 | 0,0242 / 0,0182 | 1,4536 / 0,8991 | game/ai/information/runtime_field_cache.py:183:read (3 708) |
| [game/game_actions/generation/decision_abstraction/option_space.py:20:__iter__](../../game/game_actions/generation/decision_abstraction/option_space.py#L20) | 17 379 | — | 0,0231 / — | 9,3213 / — | game/ai/decisions/candidate_ranking.py:52:ranked_batches (11 129)<br>game/game_actions/generation/decision_abstraction/action_pipelines.py:64:generate (2 790)<br>&lt;built-in method builtins.next&gt; (2 124) |
| [game/ai/agents/decision_maker.py:76:_validated_decision](../../game/ai/agents/decision_maker.py#L76) | 2 810 | 1 554 | 0,0228 / 0,0139 | 89,5218 / 92,0216 | game/ai/simulation/search_simulator.py:343:decide (2 810) |
| [game/ai/information/runtime_observation.py:179:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L179) | 73 815 | 83 372 | 0,0195 / 0,0215 | 1,4295 / 1,0156 | game/ai/information/runtime_observation.py:142:read (73 815) |
| [game/ai/decisions/generation_candidates.py:97:take](../../game/ai/decisions/generation_candidates.py#L97) | 2 864 | — | 0,0212 / — | 9,4656 / — | game/ai/simulation/search_simulator.py:437:decide (2 810)<br>game/ai/simulation/search_simulator.py:480:expand_candidates (54) |
| [game/game_loop/game_loop.py:137:_get_player_action](../../game/game_loop/game_loop.py#L137) | 2 044 | 1 009 | 0,0191 / 0,0107 | 41,0787 / 51,8254 | game/game_loop/game_loop.py:106:run (2 044) |
| [game/ai/simulation/checkpoint_simulator.py:97:decide](../../game/ai/simulation/checkpoint_simulator.py#L97) | 2 810 | 1 554 | 0,0178 / 0,0127 | 89,4810 / 91,9965 | game/ai/simulation/search_simulator.py:340:_decide (2 810) |
| [game/ai/search/information_set_search.py:827:tree_action](../../game/ai/search/information_set_search.py#L827) | 377 | 267 | 0,0147 / 0,0176 | 0,2781 / 0,3299 | game/ai/search/information_set_search.py:931:trajectory_choices (377) |
| [game/game_loop/game_loop.py:106:run](../../game/game_loop/game_loop.py#L106) | 830 | 421 | 0,0139 / 0,0075 | 49,6786 / 55,7565 | game/game_loop/game_loop.py:283:_run (624)<br>game/game_loop/game_loop.py:885:resume_priority (206) |
| [game/ai/simulation/checkpoint_simulator.py:62:copy](../../game/ai/simulation/checkpoint_simulator.py#L62) | 23 | — | 0,0128 / — | 2,1150 / — | game/ai/simulation/checkpoint_simulator.py:76:build (19)<br>game/ai/simulation/checkpoint_simulator.py:68:freeze (4) |
| [game/game_actions/generation/decision_abstraction/measurement.py:108:measure_options](../../game/game_actions/generation/decision_abstraction/measurement.py#L108) | 17 379 | — | 0,0123 / — | 9,3065 / — | game/game_actions/generation/decision_abstraction/option_space.py:20:__iter__ (16 043)<br>&lt;method 'close' of 'generator' objects&gt; (670)<br>game/game_actions/generation/exec_plan_gen_pipeline.py:88:prepare (666) |
| [game/ai/information/runtime_observation.py:98:player_view](../../game/ai/information/runtime_observation.py#L98) | 1 995 | 2 194 | 0,0107 / 0,0114 | 0,0203 / 0,0211 | game/ai/information/runtime_observation.py:404:project_decision (1 995) |
| [game/ai/decisions/candidate_ranking.py:52:ranked_batches](../../game/ai/decisions/candidate_ranking.py#L52) | 11 799 | — | 0,0114 / — | 9,3359 / — | game/ai/decisions/generation_candidates.py:266:generate (11 129)<br>&lt;method 'close' of 'generator' objects&gt; (670) |
| [game/game_state/player.py:360:get_action](../../game/game_state/player.py#L360) | 2 044 | 1 009 | 0,0110 / 0,0062 | 41,0514 / 51,8101 | game/game_loop/game_loop.py:137:_get_player_action (2 044) |
| [game/ai/information/runtime_field_cache.py:114:&lt;genexpr&gt;](../../game/ai/information/runtime_field_cache.py#L114) | 15 862 | 11 935 | 0,0085 / 0,0105 | 0,0168 / 0,0184 | &lt;built-in method builtins.any&gt; (15 862) |
| [game/ai/information/runtime_observation.py:428:&lt;lambda&gt;](../../game/ai/information/runtime_observation.py#L428) | 5 445 | 6 714 | 0,0040 / 0,0093 | 0,0063 / 0,0120 | &lt;built-in method builtins.sorted&gt; (5 445) |
| [game/game_loop/game_loop.py:283:_run](../../game/game_loop/game_loop.py#L283) | 700 | 457 | 0,0087 / 0,0066 | 88,6216 / 98,9413 | game/game_loop/game_loop.py:273:run (700) |
| [game/ai/search/information_set_search.py:768:store_transition](../../game/ai/search/information_set_search.py#L768) | 1 020 | 1 176 | 0,0077 / 0,0067 | 0,0883 / 0,0758 | game/ai/search/search_trajectory.py:114:receive (1 020) |
| [game/game_actions/resolution/resolution_engine.py:457:_apply_replacement_effects](../../game/game_actions/resolution/resolution_engine.py#L457) | 2 214 | 1 205 | 0,0076 / 0,0044 | 18,6449 / 17,3248 | game/game_actions/resolution/resolution_engine.py:344:resolve_simultaneous (168)<br>game/game_actions/resolution/sba_resolver.py:42:resolve (77)<br>game/game_actions/resolution/resolution_engine.py:475:_execute_operations (1 969) |
| [game/ai/information/runtime_field_cache.py:176:prepare](../../game/ai/information/runtime_field_cache.py#L176) | 6 386 | 4 960 | 0,0075 / 0,0059 | 0,0757 / 0,0583 | game/ai/information/runtime_field_cache.py:183:read (6 386) |
| [game/ai/search/search_trajectory.py:36:execute](../../game/ai/search/search_trajectory.py#L36) | 128 | 128 | 0,0063 / 0,0032 | 118,8793 / 111,3530 | game/ai/search/information_set_search.py:923:trajectory (128) |
| [game/ai/information/runtime_field_cache.py:35:__init__](../../game/ai/information/runtime_field_cache.py#L35) | 10 094 | 7 750 | 0,0063 / 0,0055 | 0,0063 / 0,0055 | game/ai/information/runtime_field_cache.py:176:prepare (6 386)<br>game/ai/information/runtime_field_cache.py:157:prepare (3 708) |
| [game/ai/information/runtime_observation.py:427:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L427) | 6 762 | 8 184 | 0,0052 / 0,0062 | 1,4577 / 1,8543 | &lt;built-in method builtins.sorted&gt; (6 762) |
| [game/ai/information/runtime_observation.py:164:&lt;lambda&gt;](../../game/ai/information/runtime_observation.py#L164) | 9 256 | 6 325 | 0,0062 / 0,0040 | 0,7423 / 0,3691 | game/ai/information/observation_characteristics.py:10:native_characteristics (9 256) |
| [game/ai/simulation/search_simulator.py:851:expand_candidates](../../game/ai/simulation/search_simulator.py#L851) | 286 | 214 | 0,0061 / 0,0049 | 25,7157 / 14,6137 | game/ai/search/search_trajectory.py:36:execute (232)<br>game/ai/search/search_trajectory.py:114:receive (54) |
| [game/ai/simulation/search_simulator.py:937:_run_replay](../../game/ai/simulation/search_simulator.py#L937) | 206 | 155 | 0,0056 / 0,0040 | 120,0326 / 112,2618 | game/ai/simulation/checkpoint_simulator.py:226:_replay (206) |
| [game/game_loop/game_loop.py:651:execute_with_processor](../../game/game_loop/game_loop.py#L651) | 700 | 454 | 0,0055 / 0,0043 | 44,9404 / 36,8585 | game/game_loop/game_loop.py:283:_run (700) |
| [game/ai/search/information_set_search.py:913:score](../../game/ai/search/information_set_search.py#L913) | 2 378 | 4 352 | 0,0028 / 0,0052 | 0,0188 / 0,0424 | game/ai/search/information_set_search.py:827:tree_action (2 378) |
| [game/ai/simulation/search_simulator.py:340:_decide](../../game/ai/simulation/search_simulator.py#L340) | 2 810 | 1 554 | 0,0052 / 0,0033 | 89,4862 / 91,9998 | game/ai/agents/decision_maker.py:76:_validated_decision (2 810) |
| [game/ai/search/search_trajectory.py:114:receive](../../game/ai/search/search_trajectory.py#L114) | 1 020 | 1 176 | 0,0046 / 0,0051 | 3,5272 / 4,1494 | game/ai/search/search_trajectory.py:140:choose (1 020) |
| [game/ai/search/search_trajectory.py:140:choose](../../game/ai/search/search_trajectory.py#L140) | 1 146 | 1 295 | 0,0047 / 0,0050 | 3,5496 / 4,1733 | game/ai/simulation/search_simulator.py:662:_select (1 146) |
| [game/ai/information/runtime_observation.py:278:__iter__](../../game/ai/information/runtime_observation.py#L278) | 1 995 | 2 194 | 0,0041 / 0,0044 | 0,0049 / 0,0052 | game/ai/information/structured_values.py:344:structural_digest (1 995) |
| [game/ai/information/runtime_observation.py:321:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L321) | 6 836 | 9 708 | 0,0023 / 0,0039 | 0,0023 / 0,0039 | game/ai/information/runtime_observation.py:293:read_field (6 836) |
| [game/game_loop/game_loop.py:857:step](../../game/game_loop/game_loop.py#L857) | 700 | 302 | 0,0039 / 0,0018 | 88,7069 / 57,3391 | game/ai/simulation/search_simulator.py:937:_run_replay (700) |
| [game/ai/search/information_set_search.py:748:transition_key](../../game/ai/search/information_set_search.py#L748) | 1 679 | 1 577 | 0,0039 / 0,0038 | 0,0424 / 0,0460 | game/ai/search/search_trajectory.py:36:execute (453)<br>game/ai/search/information_set_search.py:768:store_transition (1 020)<br>game/ai/search/information_set_search.py:780:cached_forced_chain (167) |
| [game/ai/information/runtime_field_cache.py:181:&lt;lambda&gt;](../../game/ai/information/runtime_field_cache.py#L181) | 6 386 | 4 960 | 0,0035 / 0,0027 | 0,0919 / 0,2828 | game/ai/information/runtime_field_cache.py:183:read (6 386) |
| [game/game_loop/game_loop.py:613:_choose_declaration](../../game/game_loop/game_loop.py#L613) | 349 | 296 | 0,0032 / 0,0028 | 22,4668 / 23,3163 | game/game_loop/game_loop.py:651:execute_with_processor (349) |
| [game/ai/simulation/search_simulator.py:343:decide](../../game/ai/simulation/search_simulator.py#L343) | 2 810 | 1 554 | 0,0031 / 0,0021 | 89,5249 / 92,0237 | game/game_state/player.py:360:get_action (2 044)<br>game/game_loop/game_loop.py:613:_choose_declaration (349)<br>game/game_actions/resolution/replacement_effects.py:380:replace (292) |
| [game/ai/information/runtime_observation.py:123:__init__](../../game/ai/information/runtime_observation.py#L123) | 11 501 | 9 472 | 0,0031 / 0,0018 | 0,0031 / 0,0018 | game/ai/information/runtime_observation.py:269:card (11 501) |
| [game/ai/information/runtime_field_cache.py:171:&lt;lambda&gt;](../../game/ai/information/runtime_field_cache.py#L171) | 3 708 | 2 790 | 0,0030 / 0,0023 | 1,2586 / 1,0682 | game/ai/information/runtime_field_cache.py:183:read (3 708) |
| [game/ai/search/information_set_search.py:780:cached_forced_chain](../../game/ai/search/information_set_search.py#L780) | 327 | 139 | 0,0029 / 0,0013 | 0,0147 / 0,0062 | game/ai/search/search_trajectory.py:36:execute (327) |
| [game/ai/information/runtime_observation.py:99:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L99) | 4 668 | 5 112 | 0,0025 / 0,0028 | 0,0025 / 0,0028 | game/ai/information/runtime_observation.py:98:player_view (1 995)<br>&lt;built-in method builtins.any&gt; (2 673) |
| [game/ai/information/runtime_observation.py:301:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L301) | 4 260 | 4 712 | 0,0028 / 0,0019 | 0,0034 / 0,0025 | &lt;built-in method builtins.sorted&gt; (4 260) |
| [game/ai/simulation/checkpoint_simulator.py:226:_replay](../../game/ai/simulation/checkpoint_simulator.py#L226) | 206 | 155 | 0,0027 / 0,0017 | 120,0456 / 112,2662 | game/ai/simulation/search_simulator.py:772:follow_actions (126)<br>game/ai/simulation/search_simulator.py:851:expand_candidates (76)<br>game/ai/simulation/search_simulator.py:766:start (4) |
| [game/ai/search/information_set_search.py:430:_search](../../game/ai/search/information_set_search.py#L430) | 1 | 1 | 0,0019 / 0,0023 | 121,3732 / 113,5570 | game/ai/search/information_set_search.py:417:search (1) |
| [game/ai/information/runtime_observation.py:62:enabled](../../game/ai/information/runtime_observation.py#L62) | 2 864 | 1 615 | 0,0023 / 0,0014 | 0,0029 / 0,0018 | game/ai/simulation/search_simulator.py:499:_decide_options (2 864) |
| [game/ai/search/information_set_search.py:550:search](../../game/ai/search/information_set_search.py#L550) | 1 | 1 | 0,0023 / 0,0020 | 120,9808 / 113,1149 | game/ai/search/information_set_search.py:430:_search (1) |
| [game/game_loop/game_loop.py:897:resume_combat_declaration](../../game/game_loop/game_loop.py#L897) | — | 155 | — / 0,0020 | — / 41,6598 | game/ai/sampling/current_position_worlds.py:619:resume (155) |
| [game/game_loop/game_loop.py:885:resume_priority](../../game/game_loop/game_loop.py#L885) | 206 | — | 0,0020 / — | 13,0324 / — | game/ai/sampling/current_position_worlds.py:619:resume (187)<br>game/ai/simulation/checkpoint_simulator.py:76:build (19) |
| [game/game_actions/resolution/resolution_engine.py:344:resolve_simultaneous](../../game/game_actions/resolution/resolution_engine.py#L344) | 168 | 150 | 0,0018 / 0,0016 | 13,6657 / 11,9789 | game/game_loop/game_loop.py:629:_resolve_damage_batch (168) |
| [game/ai/search/information_set_search.py:987:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L987) | 4 035 | 8 367 | 0,0010 / 0,0018 | 0,0010 / 0,0018 | &lt;built-in method builtins.sum&gt; (4 035) |
| [game/game_loop/game_loop.py:629:_resolve_damage_batch](../../game/game_loop/game_loop.py#L629) | 168 | 150 | 0,0017 / 0,0014 | 13,6679 / 11,9806 | game/game_loop/game_loop.py:651:execute_with_processor (168) |
| [game/ai/information/runtime_observation.py:393:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L393) | 5 985 | 6 582 | 0,0014 / 0,0015 | 0,0014 / 0,0015 | game/ai/information/runtime_observation.py:390:prepared_view (5 985) |
| [game/ai/information/runtime_observation.py:336:__eq__](../../game/ai/information/runtime_observation.py#L336) | 442 | 164 | 0,0015 / 0,0006 | 0,0018 / 0,0007 | &lt;string&gt;:2:__eq__ (442) |
| [game/game_loop/game_loop.py:273:run](../../game/game_loop/game_loop.py#L273) | 700 | 302 | 0,0014 / 0,0006 | 88,6230 / 57,2974 | game/game_loop/game_loop.py:857:step (700) |
| [game/ai/information/runtime_observation.py:430:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L430) | 6 762 | 8 184 | 0,0010 / 0,0013 | 0,0010 / 0,0013 | game/ai/information/runtime_observation.py:404:project_decision (6 762) |
| [game/ai/information/runtime_observation.py:82:__init__](../../game/ai/information/runtime_observation.py#L82) | 206 | 155 | 0,0011 / 0,0008 | 0,0027 / 0,0019 | game/ai/information/runtime_observation.py:98:player_view (206) |
| [game/ai/information/runtime_observation.py:324:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L324) | 4 726 | 4 348 | 0,0009 / 0,0010 | 0,0009 / 0,0010 | &lt;built-in method builtins.sorted&gt; (4 726) |
| [game/ai/simulation/search_simulator.py:772:follow_actions](../../game/ai/simulation/search_simulator.py#L772) | 126 | 119 | 0,0010 / 0,0006 | 96,1392 / 100,3281 | game/ai/search/search_trajectory.py:173:execute (126) |
| [game/ai/sampling/current_position_worlds.py:337:__call__](../../game/ai/sampling/current_position_worlds.py#L337) | 187 | 155 | 0,0009 / 0,0008 | 25,8541 / 54,6104 | game/ai/simulation/search_simulator.py:937:_run_replay (187) |
| [game/ai/search/information_set_search.py:923:trajectory](../../game/ai/search/information_set_search.py#L923) | 128 | 128 | 0,0009 / 0,0009 | 118,8802 / 111,3539 | game/ai/search/information_set_search.py:550:search (128) |
| [game/ai/simulation/checkpoint_simulator.py:202:note_boundary](../../game/ai/simulation/checkpoint_simulator.py#L202) | 190 | 69 | 0,0008 / 0,0003 | 0,0066 / 0,0012 | game/ai/simulation/checkpoint_simulator.py:226:_replay (190) |
| [game/ai/search/search_trajectory.py:173:execute](../../game/ai/search/search_trajectory.py#L173) | 126 | 119 | 0,0008 / 0,0007 | 96,1401 / 100,3289 | game/ai/search/search_trajectory.py:36:execute (126) |
| [game/ai/information/runtime_observation.py:370:readonly](../../game/ai/information/runtime_observation.py#L370) | 688 | 284 | 0,0007 / 0,0003 | 0,0029 / 0,0023 | game/ai/search/information_set_search.py:931:trajectory_choices (688) |
| [game/ai/search/search_tree_memory.py:27:record](../../game/ai/search/search_tree_memory.py#L27) | 377 | 267 | 0,0007 / 0,0005 | 0,0037 / 0,0033 | game/ai/search/information_set_search.py:550:search (377) |
| [game/ai/information/runtime_field_cache.py:143:__init__](../../game/ai/information/runtime_field_cache.py#L143) | 206 | 155 | 0,0007 / 0,0005 | 0,0016 / 0,0011 | game/ai/information/runtime_observation.py:82:__init__ (206) |
| [game/ai/search/information_set_search.py:711:time_exhausted](../../game/ai/search/information_set_search.py#L711) | 2 532 | 1 704 | 0,0006 / 0,0005 | 0,0006 / 0,0005 | game/ai/search/information_set_search.py:931:trajectory_choices (2 400)<br>game/ai/search/information_set_search.py:550:search (132) |
| [game/ai/sampling/current_position_worlds.py:619:resume](../../game/ai/sampling/current_position_worlds.py#L619) | 187 | 155 | 0,0005 / 0,0005 | 10,4971 / 41,6603 | game/ai/sampling/current_position_worlds.py:337:__call__ (187) |
| [game/ai/search/information_set_search.py:919:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L919) | 2 755 | 4 619 | 0,0004 / 0,0005 | 0,0004 / 0,0005 | &lt;built-in method builtins.max&gt; (2 755) |
| [game/ai/simulation/checkpoint_simulator.py:223:_make_replay](../../game/ai/simulation/checkpoint_simulator.py#L223) | 206 | 155 | 0,0004 / 0,0003 | 0,0018 / 0,0013 | game/ai/simulation/checkpoint_simulator.py:226:_replay (206) |
| [game/ai/search/information_set_search.py:810:advance](../../game/ai/search/information_set_search.py#L810) | 39 | 99 | 0,0002 / 0,0004 | 0,0008 / 0,0089 | game/ai/search/search_trajectory.py:36:execute (39) |
| [game/ai/search/search_tree_memory.py:23:__init__](../../game/ai/search/search_tree_memory.py#L23) | 17 | 52 | 0,0002 / 0,0004 | 0,0004 / 0,0009 | game/ai/search/information_set_search.py:827:tree_action (17) |
| [game/ai/information/runtime_observation.py:309:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L309) | 1 995 | 2 194 | 0,0004 / 0,0004 | 0,0004 / 0,0004 | game/ai/information/runtime_observation.py:293:read_field (1 995) |
| [game/ai/information/runtime_observation.py:366:identity](../../game/ai/information/runtime_observation.py#L366) | 1 817 | 1 730 | 0,0003 / 0,0003 | 0,0003 / 0,0003 | game/ai/information/search_recall.py:117:fast_key (1 225)<br>game/ai/simulation/search_simulator.py:499:_decide_options (592) |
| [game/ai/simulation/checkpoint_simulator.py:76:build](../../game/ai/simulation/checkpoint_simulator.py#L76) | 19 | — | 0,0003 / — | 4,1980 / — | game/ai/simulation/checkpoint_simulator.py:249:restore (19) |
| [game/ai/search/search_tree_memory.py:186:commit](../../game/ai/search/search_tree_memory.py#L186) | 1 | 1 | 0,0001 / 0,0002 | 0,3903 / 0,4395 | game/ai/search/information_set_search.py:430:_search (1) |
| [game/ai/search/information_set_search.py:626:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L626) | 4 | 47 | 0,0000 / 0,0002 | 0,0014 / 0,0025 | game/ai/search/information_set_search.py:550:search (4) |
| [game/ai/search/information_set_search.py:862:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L862) | 109 | 208 | 0,0001 / 0,0002 | 0,0001 / 0,0002 | game/ai/search/search_tree_memory.py:23:__init__ (109) |
| [game/ai/information/runtime_observation.py:198:__init__](../../game/ai/information/runtime_observation.py#L198) | 371 | 296 | 0,0002 / 0,0001 | 0,0002 / 0,0001 | game/ai/information/runtime_observation.py:98:player_view (371) |
| [game/ai/simulation/checkpoint_simulator.py:68:freeze](../../game/ai/simulation/checkpoint_simulator.py#L68) | 4 | — | 0,0001 / — | 0,4556 / — | game/ai/simulation/checkpoint_simulator.py:97:decide (4) |
| [game/ai/search/search_trajectory.py:102:__init__](../../game/ai/search/search_trajectory.py#L102) | 126 | 119 | 0,0001 / 0,0001 | 0,0001 / 0,0001 | game/ai/search/search_trajectory.py:36:execute (126) |
| [game/ai/simulation/checkpoint_simulator.py:39:capture](../../game/ai/simulation/checkpoint_simulator.py#L39) | 4 | — | 0,0001 / — | 0,0182 / — | game/ai/simulation/checkpoint_simulator.py:97:decide (4) |
| [game/ai/simulation/checkpoint_simulator.py:249:restore](../../game/ai/simulation/checkpoint_simulator.py#L249) | 19 | — | 0,0001 / — | 4,1980 / — | game/ai/simulation/search_simulator.py:937:_run_replay (19) |
| [game/ai/simulation/checkpoint_simulator.py:174:__init__](../../game/ai/simulation/checkpoint_simulator.py#L174) | 4 | 4 | 0,0000 / 0,0000 | 0,0001 / 0,0001 | benchmarks/mcts_projection_audit.py:202:initialize (4) |
| [game/ai/search/search_trajectory.py:30:__init__](../../game/ai/search/search_trajectory.py#L30) | 128 | 128 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/information_set_search.py:923:trajectory (128) |
| [game/ai/search/search_tree_memory.py:25:&lt;genexpr&gt;](../../game/ai/search/search_tree_memory.py#L25) | 109 | 208 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (109) |
| [game/ai/information/runtime_observation.py:380:__eq__](../../game/ai/information/runtime_observation.py#L380) | 4 | 4 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;string&gt;:2:__eq__ (4) |
| [game/ai/search/information_set_search.py:510:__init__](../../game/ai/search/information_set_search.py#L510) | 1 | 1 | 0,0000 / 0,0000 | 0,0001 / 0,0001 | game/ai/search/information_set_search.py:430:_search (1) |
| [game/ai/information/runtime_observation.py:38:native_reads](../../game/ai/information/runtime_observation.py#L38) | 2 | 2 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.next&gt; (2) |
| [game/ai/simulation/checkpoint_simulator.py:212:remember_checkpoint](../../game/ai/simulation/checkpoint_simulator.py#L212) | 4 | — | 0,0000 / — | 0,0001 / — | game/ai/simulation/checkpoint_simulator.py:97:decide (4) |
| [game/ai/search/information_set_search.py:417:search](../../game/ai/search/information_set_search.py#L417) | 1 | 1 | 0,0000 / 0,0000 | 121,3732 / 113,5571 | benchmarks/mcts_runtime_observations.py:16:sample (1) |
| [game/ai/search/information_set_search.py:719:evaluation_counters](../../game/ai/search/information_set_search.py#L719) | 2 | 2 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/information_set_search.py:550:search (2) |
| [game/ai/simulation/checkpoint_simulator.py:124:&lt;genexpr&gt;](../../game/ai/simulation/checkpoint_simulator.py#L124) | 12 | 7 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.max&gt; (12) |
| [game/ai/search/information_set_search.py:639:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L639) | 4 | 47 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.max&gt; (4) |
| [game/ai/search/information_set_search.py:639:&lt;lambda&gt;](../../game/ai/search/information_set_search.py#L639) | 3 | 46 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.max&gt; (3) |
| [game/ai/search/information_set_search.py:661:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L661) | 4 | 47 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (4) |
| [game/ai/simulation/checkpoint_simulator.py:50:&lt;genexpr&gt;](../../game/ai/simulation/checkpoint_simulator.py#L50) | 12 | — | 0,0000 / — | 0,0000 / — | game/ai/simulation/checkpoint_simulator.py:39:capture (12) |
| [game/ai/search/information_set_search.py:411:_memory_signature](../../game/ai/search/information_set_search.py#L411) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/information_set_search.py:430:_search (1) |
| [game/ai/search/search_tree_memory.py:142:load](../../game/ai/search/search_tree_memory.py#L142) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/information_set_search.py:430:_search (1) |
| [game/ai/information/runtime_observation.py:52:&lt;genexpr&gt;](../../game/ai/information/runtime_observation.py#L52) | 9 | 9 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.any&gt; (9) |
| [game/ai/search/information_set_search.py:50:__post_init__](../../game/ai/search/information_set_search.py#L50) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;string&gt;:2:__init__ (1) |
| [game/ai/search/information_set_search.py:662:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L662) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:698:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L698) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:668:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L668) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:634:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L634) | 2 | 2 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/information_set_search.py:550:search (1)<br>&lt;built-in method builtins.any&gt; (1) |
| [game/ai/search/information_set_search.py:694:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L694) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:685:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L685) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/search_tree_memory.py:113:clear](../../game/ai/search/search_tree_memory.py#L113) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | game/ai/search/search_tree_memory.py:142:load (1) |
| [game/ai/search/information_set_search.py:673:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L673) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:665:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L665) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:671:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L671) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:692:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L692) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:696:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L696) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:669:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L669) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:630:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L630) | 4 | — | 0,0000 / — | 0,0000 / — | &lt;built-in method builtins.all&gt; (4) |
| [game/ai/search/information_set_search.py:663:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L663) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:684:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L684) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:664:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L664) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:666:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L666) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:667:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L667) | 5 | 5 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (5) |
| [game/ai/search/information_set_search.py:636:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L636) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.any&gt; (1) |
| [game/ai/search/information_set_search.py:471:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L471) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (1) |
| [game/ai/search/information_set_search.py:676:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L676) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (1) |
| [game/ai/search/information_set_search.py:677:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L677) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (1) |
| [game/ai/search/information_set_search.py:469:&lt;genexpr&gt;](../../game/ai/search/information_set_search.py#L469) | 1 | 1 | 0,0000 / 0,0000 | 0,0000 / 0,0000 | &lt;built-in method builtins.sum&gt; (1) |

## Konstruktory a post-init

Počet `__init__` a `__post_init__` se u téže třídy **nesčítá na počet objektů**: jsou to dvě fáze vytvoření stejné instance. Kopie přes `object.__new__` nebo redukční protokol mohou `__init__` obejít. `__new__` immutable wrapperu může vrátit už existující hodnotu. Pokud jeden kód konstruktoru dědí více tříd, tabulka uvádí možné sdílející třídy a společný počet; nerozděluje ho na vymyšlené počty jednotlivých podtříd. Seznam tříd není důkaz, že všechny byly v této sérii instanciované.

| Třída/metoda sdílející kód | Zdroj kódu | Volání 87 | Volání 102 | Self s 87 / 102 |
| --- | --- | ---: | ---: | ---: |
| helper.query_system.object_register.BitSet.__init__ | helper/query_system/object_register.py:237:__init__ | 277 239 | 201 339 | 0,1210 / 0,0876 |
| game.ai.information.observation_projection.DescriptionProjection.__init__ | &lt;string&gt;:2:__init__ | 246 742 | 239 022 | 0,2035 / 0,2012 |
| game.ai.information.structured_values.Value.__post_init__ | game/ai/information/structured_values.py:31:__post_init__ | 212 387 | 136 848 | 0,1535 / 0,0938 |
| game.ai.information.structured_values.Value.__init__ | &lt;string&gt;:2:__init__ | 212 387 | 136 848 | 0,2024 / 0,1318 |
| game.game_actions.data_structs.action_node.ImmutableEffectToSlotMap.__new__<br>game.target.target_resolver.ImmutableTargetBinding.__new__<br>helper.mutability_objs.ImmutableDict.__new__<br>helper.runtime_object.ImmutableKeyedCollection.__new__ | C:/Users/stepa/Documents/Bakalarka/Bakalarka/.venv/Lib/site-packages/immutabledict/__init__.py:69:__new__ | 84 485 | 78 214 | 0,1217 / 0,1103 |
| helper.query_system.query.QueryContext.__init__ | helper/query_system/query.py:18:__init__ | 55 757 | 44 711 | 0,0099 / 0,0078 |
| helper.query_system.query.EqQuery.__init__ | &lt;string&gt;:2:__init__ | 54 731 | 43 760 | 0,0382 / 0,0282 |
| game.game_actions.data_structs.game_action.ResolutionContext.__init__ | &lt;string&gt;:2:__init__ | 45 625 | 30 299 | 0,1338 / 0,0907 |
| game.mana.mana_value.GenericSymbol.__init__ | &lt;string&gt;:2:__init__ | 34 060 | 36 089 | 0,0038 / 0,0038 |
| game.ai.information.structured_values.ReadOnlyObject.__init__ | game/ai/information/structured_values.py:144:__init__ | 35 364 | 27 248 | 0,0726 / 0,0557 |
| game.game_actions.resolution.event_bus.GameEvent.__init__ | &lt;string&gt;:2:__init__ | 29 219 | 19 472 | 0,0485 / 0,0319 |
| game.game_actions.resolution.event_bus.GameEvent.__post_init__ | game/game_actions/resolution/event_bus.py:104:__post_init__ | 29 219 | 19 472 | 0,2234 / 0,0225 |
| game.abilities.parameter_context.ParameterContext.__init__ | &lt;string&gt;:2:__init__ | 28 230 | 23 210 | 0,0143 / 0,0107 |
| game.game_state.modifier.TimeStampedModifier.__init__ | &lt;string&gt;:2:__init__ | 24 894 | 4 707 | 0,0108 / 0,0025 |
| game.game_state.card_snapshot_cache._Entry.__init__ | &lt;string&gt;:2:__init__ | 22 467 | 9 385 | 0,0356 / 0,0150 |
| game.game_actions.resolution.event_bus.CardLastKnownInformation.__init__ | &lt;string&gt;:2:__init__ | 21 839 | 8 624 | 0,0748 / 0,0301 |
| game.game_actions.resolution.event_bus.CardLastKnownInformation.__post_init__ | game/game_actions/resolution/event_bus.py:68:__post_init__ | 21 839 | 8 624 | 0,0586 / 0,0236 |
| game.game_state.stat.ModifiablePrimitiveStat.__init__<br>game.game_state.stat.ModifiableReferenceStat.__init__<br>game.game_state.stat.ModifiableStatBase.__init__<br>game.game_state.stat.Stat.__init__<br>game.game_state.stat.StatBase.__init__ | &lt;string&gt;:2:__init__ | 19 985 | 21 218 | 0,0112 / 0,0112 |
| helper.query_system.query.AndQuery.__init__ | &lt;string&gt;:2:__init__ | 19 789 | 14 753 | 0,0085 / 0,0065 |
| helper.query_system.object_register.IndexUpdate.__init__ | &lt;string&gt;:2:__init__ | 19 166 | 14 857 | 0,0134 / 0,0079 |
| helper.query_system.query.InQuery.__init__ | &lt;string&gt;:2:__init__ | 18 523 | 13 684 | 0,0133 / 0,0104 |
| helper.dict_helper.MutableCounterMapping.__init__<br>helper.dict_helper.MutablePositiveCounterMapping.__init__ | helper/dict_helper.py:41:__init__ | 16 006 | 13 407 | 0,0124 / 0,0102 |
| game.game_actions.data_structs.ability.Ability.__init__<br>game.game_actions.data_structs.ability.ActivatedAbility.__init__<br>game.game_actions.data_structs.ability.CastSpellAbility.__init__<br>game.game_actions.data_structs.ability.ManaAbility.__init__<br>game.game_actions.data_structs.ability.PlayLandAbility.__init__<br>game.game_actions.data_structs.ability.PriorityActionAbility.__init__ | game/game_actions/data_structs/ability.py:875:__init__ | 14 598 | 15 934 | 0,3055 / 0,0741 |
| game.game_actions.data_structs.ability.AbilityData.__init__ | &lt;string&gt;:2:__init__ | 14 598 | 15 934 | 0,0113 / 0,0121 |
| game.ai.decisions.search_descriptors.ChoiceDescriptor.__init__ | &lt;string&gt;:2:__init__ | 12 868 | 13 198 | 0,0104 / 0,0100 |
| game.ai.information.structured_values.ValueStore.__init__ | game/ai/information/structured_values.py:182:__init__ | 12 702 | 9 153 | 0,0070 / 0,0050 |
| game.target.target_resolver.ImmutableTargetBinding.__init__ | game/target/target_resolver.py:96:__init__ | 8 758 | 12 296 | 0,0052 / 0,0070 |
| game.game_state.registers.layer_candidate_register._DefinitionWatch.__init__ | &lt;string&gt;:2:__init__ | 11 677 | 8 172 | 0,0040 / 0,0027 |
| game.mana.mana_value.ImmutableManaRequirement.__new__ | game/mana/mana_value.py:227:__new__ | 8 784 | 11 666 | 0,0103 / 0,0136 |
| game.game_actions.data_structs.ability.TriggerAbility.__init__<br>game.game_actions.data_structs.ability.TriggeredManaAbility.__init__ | game/game_actions/data_structs/ability.py:1248:__init__ | 10 476 | 11 660 | 0,0163 / 0,0181 |
| game.ai.information.runtime_observation.CardReadContract.__init__ | game/ai/information/runtime_observation.py:123:__init__ | 11 501 | 9 472 | 0,0031 / 0,0018 |
| game.game_actions.triggers.trigger_roster.TriggerBinding.__new__ | &lt;string&gt;:1:&lt;lambda&gt; | 11 094 | 11 210 | 0,0038 / 0,0035 |
| game.ai.information.structured_values.StructuredJSON.__init__ | game/ai/information/structured_values.py:274:__init__ | 10 975 | 11 019 | 0,0202 / 0,0197 |
| game.ai.decisions.attack_candidates.AttackRisk.__new__<br>game.enums.ActivatableAbilityType.__new__<br>game.enums.CardSubtype.__new__<br>game.enums.CardType.__new__<br>game.enums.CounterType.__new__<br>game.enums.Layer.__new__<br>game.enums.ManaType.__new__<br>game.enums.ModifierType.__new__<br>game.enums.RequestType.__new__<br>game.enums.ResolutionSpeed.__new__<br>game.enums.ResourceType.__new__<br>game.enums.RuntimeObjectType.__new__<br>game.enums.SAVariableType.__new__<br>game.enums.StepType.__new__<br>game.enums.TargetType.__new__<br>game.enums.TurnPhase.__new__<br>game.enums.ZoneType.__new__<br>game.enums._MetaResult.__new__<br>game.game_state.card_changes.CardChange.__new__ | C:/Users/stepa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/enum.py:1128:__new__ | 10 874 | 7 910 | 0,0050 / 0,0036 |
| game.mana.mana_value.ManaRequirementFragment.__init__ | &lt;string&gt;:2:__init__ | 10 620 | 5 530 | 0,0091 / 0,0048 |
| game.mana.mana_value.ManaRequirementFragment.__post_init__ | game/mana/mana_value.py:169:__post_init__ | 10 620 | 5 530 | 0,0059 / 0,0032 |
| game.ai.information.runtime_field_cache.Dependencies.__init__ | game/ai/information/runtime_field_cache.py:35:__init__ | 10 094 | 7 750 | 0,0063 / 0,0055 |
| game.game_actions.data_structs.ability.SubAbilityComposer.__init__ | game/game_actions/data_structs/ability.py:215:__init__ | 8 244 | 8 548 | 0,0094 / 0,0091 |
| game.mana.mana_value.ManaRequirement.__init__ | game/mana/mana_value.py:197:__init__ | 8 244 | 8 548 | 0,0121 / 0,0122 |
| game.game_actions.data_structs.ability.RuntimeSubAbility.__init__ | &lt;string&gt;:2:__init__ | 8 244 | 8 548 | 0,0107 / 0,0105 |
| game.game_actions.data_structs.game_action.AbilityExecutionPlan.__init__ | &lt;string&gt;:2:__init__ | 5 507 | 8 533 | 0,0056 / 0,0086 |
| game.game_actions.generation.exec_plan_gen_pipeline.PreparedExecutionPlan.__init__ | &lt;string&gt;:2:__init__ | 5 328 | 8 448 | 0,0078 / 0,0099 |
| game.game_actions.generation.generation_strategy.ExecutionPlanStrategy.__init__ | &lt;string&gt;:2:__init__ | 8 084 | 8 354 | 0,0077 / 0,0073 |
| game.mana.mana_value.ManaValue.__init__ | game/mana/mana_value.py:535:__init__ | 7 762 | 4 859 | 0,0380 / 0,0243 |
| game.game_actions.data_structs.action_node.ActionNodeOption.__init__ | &lt;string&gt;:2:__init__ | 4 662 | 7 392 | 0,0029 / 0,0045 |
| game.game_actions.generation.decision_abstraction.option_space.DecisionOptionSpace.__init__ | &lt;string&gt;:2:__init__ | 7 058 | 3 766 | 0,0056 / 0,0033 |
| game.ai.decisions.search_actions.PlayerAction.__init__ | &lt;string&gt;:2:__init__ | 5 570 | 6 714 | 0,0037 / 0,0045 |
| game.ai.evaluation.observation_policy.ActionRating.__init__ | &lt;string&gt;:2:__init__ | 5 445 | 6 714 | 0,0071 / 0,0085 |
| helper.query_system.object_register.SortedOrderedMap.__init__ | helper/query_system/object_register.py:514:__init__ | 6 622 | 4 100 | 0,0009 / 0,0005 |
| game.game_actions.generation.target_binding_generator.ProvidedTargetBindingGenerator.__init__ | game/game_actions/generation/target_binding_generator.py:44:__init__ | 3 996 | 6 336 | 0,0011 / 0,0017 |
| helper.query_system.query.OrQuery.__init__ | &lt;string&gt;:2:__init__ | 6 280 | 4 000 | 0,0026 / 0,0017 |
| helper.query_system.query.HasQuery.__init__ | &lt;string&gt;:2:__init__ | 6 265 | 4 764 | 0,0057 / 0,0045 |
| game.game_actions.generation.decision_abstraction.options.DeclareAttackersOption.__post_init__ | game/game_actions/generation/decision_abstraction/options.py:20:__post_init__ | 2 336 | 5 338 | 0,0024 / 0,0051 |
| game.game_actions.generation.decision_abstraction.options.DeclareAttackersOption.__init__ | &lt;string&gt;:2:__init__ | 2 336 | 5 338 | 0,0019 / 0,0038 |
| game.mana.mana_solver.ManaSolverResult.__init__ | &lt;string&gt;:2:__init__ | 3 330 | 5 280 | 0,0044 / 0,0070 |
| game.ai.information.search_recall.PlayerRecall.__init__ | &lt;string&gt;:2:__init__ | 5 139 | 3 891 | 0,0048 / 0,0037 |
| game.mana.discovery_context.ManaDiscoveryContext.__init__ | game/mana/discovery_context.py:34:__init__ | 4 754 | 3 074 | 0,0009 / 0,0006 |
| helper.query_system.object_register.RegisterUpdateContext.__init__ | &lt;string&gt;:2:__init__ | 4 748 | 2 381 | 0,0020 / 0,0010 |
| game.ai.information.structured_values.RecallDigest.__init__ | game/ai/information/structured_values.py:432:__init__ | 4 727 | 3 581 | 0,0138 / 0,0107 |
| game.ai.information.observation.PlayerObservationBuilder.__init__ | game/ai/information/observation.py:32:__init__ | 4 648 | 3 744 | 0,0615 / 0,0468 |
| game.game_loop.game_loop.PriorityWindowContext.__init__ | &lt;string&gt;:2:__init__ | 4 597 | 3 342 | 0,0015 / 0,0011 |
| game.ai.search.search_tree_memory.EdgeStatistics.__init__ | &lt;string&gt;:2:__init__ | 2 589 | 4 508 | 0,0004 / 0,0005 |
| game.game_loop.game_loop.CombatPhaseController.__init__<br>game.game_loop.game_loop.SimplePhaseController.__init__ | game/game_loop/game_loop.py:485:__init__ | 4 410 | 3 339 | 0,0022 / 0,0027 |
| game.ai.information.observation_projection.VisibilityRead.__init__ | &lt;string&gt;:2:__init__ | 4 137 | 4 388 | 0,0055 / 0,0058 |
| game.mana.mana_value.ImmutableManaValue.__new__ | game/mana/mana_value.py:567:__new__ | 4 300 | 2 264 | 0,0062 / 0,0035 |
| game.game_actions.generation.ability_action_gen_pipeline.ActionGenerationContext.__init__ | &lt;string&gt;:2:__init__ | 4 122 | 4 274 | 0,0022 / 0,0020 |
| game.game_actions.generation.subability_generator.SubAbilityGenerationResult.__init__ | &lt;string&gt;:2:__init__ | 4 122 | 4 274 | 0,0038 / 0,0036 |
| game.mana.symmetry.ManaSearchStatistics.__init__ | &lt;string&gt;:2:__init__ | 4 248 | 2 212 | 0,0022 / 0,0012 |
| game.game_actions.data_structs.game_action.AbilityAction.__post_init__<br>game.game_actions.data_structs.game_action.ManaAbilityAction.__post_init__ | game/game_actions/data_structs/game_action.py:353:__post_init__ | 2 664 | 4 224 | 0,0077 / 0,0123 |
| game.game_actions.generation.generation_strategy.ActionGenerationStrategy.__init__ | &lt;string&gt;:2:__init__ | 4 042 | 4 177 | 0,0079 / 0,0076 |
| game.mana.mana_solver.SourceActivatingManaSolver.__init__ | game/mana/mana_solver.py:123:__init__ | 4 042 | 4 177 | 0,0008 / 0,0008 |
| game.game_actions.generation.decision_abstraction.policies.AbilityGenerationPolicy.__init__ | &lt;string&gt;:2:__init__ | 4 088 | 2 018 | 0,0063 / 0,0033 |
| game.ai.evaluation.search_abstraction.CounterEvidence.__init__ | &lt;string&gt;:2:__init__ | 372 | 3 872 | 0,0002 / 0,0023 |
| game.game_state.card_mana_fields.ManaFields.__init__ | game/game_state/card_mana_fields.py:17:__init__ | 3 502 | 2 635 | 0,0007 / 0,0005 |
| game.ai.information.search_recall.RecallEntry.__init__ | &lt;string&gt;:2:__init__ | 3 410 | 2 111 | 0,0027 / 0,0019 |
| game.cards.starter_cards.ScryOneOperation.__init__<br>game.game_actions.advanced_effects.DrawOperation.__init__<br>game.game_actions.advanced_effects.InstallShieldOperation.__init__<br>game.game_actions.control_effects.DestroyAllCreaturesOperation.__init__<br>game.game_actions.data_structs.operation.ConcedeOperation.__init__<br>game.game_actions.data_structs.operation.GameEventOperation.__init__<br>game.game_actions.data_structs.operation.Operation.__init__<br>game.game_actions.data_structs.operation.PassPriorityOperation.__init__<br>game.game_actions.mana_activation.ManaActivationEventOperation.__init__<br>game.operations.card_operations.DrawCardOperation.__init__<br>game.rules.lands.PlayLandOperation.__init__ | game/game_actions/data_structs/operation.py:19:__init__ | 3 170 | 1 930 | 0,0008 / 0,0005 |
| game.game_actions.data_structs.game_action.ManaAbilityAction.__init__ | &lt;string&gt;:2:__init__ | 1 998 | 3 168 | 0,0069 / 0,0107 |
| game.mana.mana_generator.ManaPlanStep.__init__ | &lt;string&gt;:2:__init__ | 1 998 | 3 168 | 0,0017 / 0,0027 |
| game.ai.decisions.search_actions.PlayerActionBuilder.__init__ | game/ai/decisions/search_actions.py:126:__init__ | 2 634 | 2 940 | 0,0075 / 0,0081 |
| game.ai.decisions.search_context.SearchContextBuilder.__init__ | game/ai/decisions/search_context.py:30:__init__ | 2 634 | 2 940 | 0,0008 / 0,0009 |
| game.ai.decisions.generation_candidates.CandidateFrontier.__init__ | game/ai/decisions/generation_candidates.py:92:__init__ | 2 810 | 1 554 | 0,0017 / 0,0010 |
| game.ai.decisions.candidate_ranking.VisibleCandidateRanker.__init__ | game/ai/decisions/candidate_ranking.py:82:__init__ | 2 810 | 1 554 | 0,0008 / 0,0004 |
| game.ai.decisions.native_candidates.NativeProposalContext.__init__ | game/ai/decisions/native_candidates.py:13:__init__ | 2 810 | 1 554 | 0,0008 / 0,0004 |
| game.game_actions.generation.decision_abstraction.options.DeclareBlockersOption.__post_init__ | game/game_actions/generation/decision_abstraction/options.py:34:__post_init__ | 2 759 | 1 082 | 0,0031 / 0,0014 |
| game.game_actions.generation.decision_abstraction.options.DeclareBlockersOption.__init__ | &lt;string&gt;:2:__init__ | 2 759 | 1 082 | 0,0027 / 0,0011 |
| helper.query_system.query.RangeQuery.__init__ | &lt;string&gt;:2:__init__ | 2 738 | 1 714 | 0,0030 / 0,0019 |
| game.game_actions.data_structs.game_action.ScheduledResolution.__post_init__ | game/game_actions/data_structs/game_action.py:250:__post_init__ | 2 685 | 1 275 | 0,0035 / 0,0018 |
| game.game_actions.data_structs.game_action.ScheduledResolution.__init__ | &lt;string&gt;:2:__init__ | 2 685 | 1 275 | 0,0028 / 0,0013 |
| game.ai.agents.decision_maker.DecisionResult.__init__ | game/ai/agents/decision_maker.py:41:__init__ | 2 604 | 1 399 | 0,0031 / 0,0020 |
| game.game_actions.resolution.cost_validation.CostResources.__init__ | game/game_actions/resolution/cost_validation.py:16:__init__ | 2 506 | 1 190 | 0,0017 / 0,0008 |
| helper.query_system.query.DifferenceQuery.__init__ | &lt;string&gt;:2:__init__ | 2 336 | 1 428 | 0,0026 / 0,0017 |
| game.mana.mana_generator.ManaSource.__init__ | &lt;string&gt;:2:__init__ | 1 648 | 2 223 | 0,0025 / 0,0030 |
| game.ai.information.runtime_observation.PreparedView.__init__ | &lt;string&gt;:2:__init__ | 1 995 | 2 194 | 0,0044 / 0,0045 |
| game.mana.mana_value.ColoredSymbol.__init__ | &lt;string&gt;:2:__init__ | 2 148 | 1 130 | 0,0017 / 0,0011 |
| game.game_actions.generation.decision_abstraction.parameters.AbilityParameters.__post_init__ | game/game_actions/generation/decision_abstraction/parameters.py:20:__post_init__ | 2 124 | 1 106 | 0,0012 / 0,0007 |
| game.game_actions.generation.decision_abstraction.requests.AbilityDecisionRequest.__init__ | &lt;string&gt;:2:__init__ | 2 124 | 1 106 | 0,0039 / 0,0020 |
| game.game_actions.generation.decision_abstraction.parameters.AbilityParameters.__init__ | &lt;string&gt;:2:__init__ | 2 124 | 1 106 | 0,0028 / 0,0015 |
| game.game_actions.generation.decision_abstraction.policies.ManaGenerationPolicy.__init__ | &lt;string&gt;:2:__init__ | 2 124 | 1 106 | 0,0012 / 0,0007 |
| game.game_actions.generation.decision_abstraction.requests.ManaGenerationRequest.__init__ | &lt;string&gt;:2:__init__ | 2 124 | 1 106 | 0,0031 / 0,0017 |
| game.mana.mana_generator.ManaGenerator.__init__ | game/mana/mana_generator.py:83:__init__ | 2 124 | 1 106 | 0,0059 / 0,0035 |
| game.ai.simulation.search_simulator.DecisionAddress.__init__ | &lt;string&gt;:2:__init__ | 2 120 | 2 060 | 0,0041 / 0,0041 |
| game.game_actions.generation.decision_abstraction.policies.PriorityGenerationPolicy.__init__ | &lt;string&gt;:2:__init__ | 2 044 | 1 009 | 0,0109 / 0,0063 |
| game.game_actions.data_structs.game_action.PassPriorityAction.__init__ | &lt;string&gt;:2:__init__ | 2 044 | 1 009 | 0,0016 / 0,0008 |
| game.game_actions.generation.decision_abstraction.requests.PriorityDecisionRequest.__init__ | &lt;string&gt;:2:__init__ | 2 044 | 1 009 | 0,0020 / 0,0011 |
| game.game_actions.generation.decision_abstraction.policies.PriorityGenerationPolicy.__post_init__ | game/game_actions/generation/decision_abstraction/policies.py:78:__post_init__ | 2 044 | 1 009 | 0,0014 / 0,0007 |
| game.game_loop.continuations.PriorityDecisionBoundary.__init__ | &lt;string&gt;:2:__init__ | 2 044 | 1 009 | 0,0031 / 0,0018 |
| game.ai.information.search_recall.PlayerInformation.__init__ | &lt;string&gt;:2:__init__ | 1 528 | 1 617 | 0,0039 / 0,0039 |
| game.ai.simulation.search_simulator.SimulationNode.__init__ | &lt;string&gt;:2:__init__ | 1 495 | 1 543 | 0,0080 / 0,0084 |
| game.ai.decisions.search_decision.DecisionProjector.__init__ | game/ai/decisions/search_decision.py:60:__init__ | 1 317 | 1 470 | 0,0006 / 0,0007 |
| game.ai.information.structured_values.DigestReference.__init__ | &lt;string&gt;:2:__init__ | 1 317 | 1 470 | 0,0012 / 0,0013 |
| game.ai.evaluation.observation_policy._Scorer.__init__ | game/ai/evaluation/observation_policy.py:106:__init__ | 1 317 | 1 470 | 0,0129 / 0,0147 |
| game.ai.decisions.search_decision.ProjectedDecision.__init__ | &lt;string&gt;:2:__init__ | 1 317 | 1 470 | 0,0031 / 0,0034 |
| game.ai.information.runtime_observation.PreparedObservation.__init__ | &lt;string&gt;:2:__init__ | 1 317 | 1 470 | 0,0023 / 0,0024 |
| game.ai.information.runtime_observation.IdentityRecallEntry.__init__ | &lt;string&gt;:2:__init__ | 1 317 | 1 470 | 0,0016 / 0,0017 |
| game.mana.discovery_context._Entry.__init__ | &lt;string&gt;:2:__init__ | 1 395 | 1 081 | 0,0004 / 0,0003 |
| game.game_actions.generation.decision_abstraction.auxiliary.OrderOption.__post_init__<br>game.game_actions.generation.decision_abstraction.auxiliary.ReplacementOrderOption.__post_init__<br>game.game_actions.generation.decision_abstraction.auxiliary.TriggerOrderOption.__post_init__ | game/game_actions/generation/decision_abstraction/auxiliary.py:62:__post_init__ | 1 344 | 1 080 | 0,0007 / 0,0005 |
| game.game_actions.generation.decision_abstraction.auxiliary.ReplacementOrderOption.__init__ | &lt;string&gt;:2:__init__ | 1 344 | 1 080 | 0,0011 / 0,0009 |
| game.game_state.modifier.TimeStamp.__init__ | &lt;string&gt;:2:__init__ | 1 235 | 739 | 0,0022 / 0,0005 |
| game.ai.simulation.search_simulator.RecordedChoice.__init__ | &lt;string&gt;:2:__init__ | 1 020 | 1 176 | 0,0014 / 0,0016 |
| game.mana.mana_value.ImmutableManaPool.__new__ | game/mana/mana_value.py:147:__new__ | 845 | 1 141 | 0,0013 / 0,0018 |
| game.game_actions.resolution.validator.ValidationResult.__init__ | &lt;string&gt;:2:__init__ | 1 074 | 510 | 0,0011 / 0,0006 |
| game.game_actions.data_structs.game_action.AbilityAction.__init__ | &lt;string&gt;:2:__init__ | 666 | 1 056 | 0,0024 / 0,0039 |
| game.mana.mana_generator.ManaPlan.__init__ | &lt;string&gt;:2:__init__ | 666 | 1 056 | 0,0006 / 0,0010 |
| game.ai.information.structured_values.LexicalValue.__init__ | &lt;string&gt;:2:__init__ | 795 | 977 | 0,0004 / 0,0007 |
| game.game_loop.game_loop.PriorityWindow.__init__ | game/game_loop/game_loop.py:91:__init__ | 830 | 421 | 0,0025 / 0,0011 |
| game.ai.information.knowledge.PlayerEvent.__init__ | &lt;string&gt;:2:__init__ | 806 | 712 | 0,0018 / 0,0016 |
| game.operations.card_operations.DamageCreatureOperation.__init__<br>game.operations.card_operations.DamagePlayerOperation.__init__ | game/operations/card_operations.py:141:__init__ | 798 | 633 | 0,0026 / 0,0022 |
| game.game_state.native_modifier_plan.NativeModifierPlan.__init__ | game/game_state/native_modifier_plan.py:65:__init__ | 766 | 95 | 0,0007 / 0,0001 |
| helper.runtime_object.RuntimeObject.__init__ | helper/runtime_object.py:62:__init__ | 714 | 522 | 0,0003 / 0,0002 |
| helper.change_tracking.TrackedDict.__init__ | helper/change_tracking.py:8:__init__ | 712 | 704 | 0,0003 / 0,0011 |
| game.game_actions.triggers.trigger_registry.TriggerRoster.__new__ | game/game_actions/triggers/trigger_registry.py:15:__new__ | 682 | 467 | 0,0011 / 0,0008 |
| game.game_actions.generation.decision_abstraction.auxiliary.CombatDamageOption.__post_init__ | game/game_actions/generation/decision_abstraction/auxiliary.py:135:__post_init__ | 625 | — | 0,0008 / — |
| game.game_actions.generation.decision_abstraction.auxiliary.CombatDamageOption.__init__ | &lt;string&gt;:2:__init__ | 625 | — | 0,0006 / — |
| game.game_actions.resolution.sba_candidates.SBACandidateFilter.__init__ | game/game_actions/resolution/sba_candidates.py:16:__init__ | 603 | 429 | 0,0003 / 0,0002 |
| game.ai.simulation.search_simulator._Projection.__init__ | &lt;string&gt;:2:__init__ | 541 | 525 | 0,0015 / 0,0015 |
| game.operations.card_operations.TapCardOperation.__init__ | game/operations/card_operations.py:339:__init__ | 537 | 255 | 0,0008 / 0,0003 |
| game.game_actions.mana_effects.AddManaOperation.__init__ | game/game_actions/mana_effects.py:14:__init__ | 537 | 255 | 0,0009 / 0,0004 |
| game.game_actions.resolution.resolution_engine.ExecutionResult.__init__ | &lt;string&gt;:2:__init__ | 523 | 312 | 0,0010 / 0,0006 |
| game.game_loop.continuations.ReplacementOrderingFrame.__init__ | &lt;string&gt;:2:__init__ | 489 | 462 | 0,0009 / 0,0008 |
| game.game_state.card.CardDefinition.__post_init__ | game/game_state/card.py:129:__post_init__ | 416 | 416 | 0,0006 / 0,0005 |
| game.game_state.card.CardDefinition.__init__ | &lt;string&gt;:2:__init__ | 416 | 416 | 0,0017 / 0,0015 |
| game.game_actions.generation.structural_templates._Graph.__init__ | &lt;string&gt;:2:__init__ | 416 | 288 | 0,0008 / 0,0005 |
| game.ai.information.knowledge.PlayerKnowledge.__init__ | game/ai/information/knowledge.py:92:__init__ | 412 | 310 | 0,0001 / 0,0001 |
| game.ai.simulation.search_simulator._ReplayController.__init__ | game/ai/simulation/search_simulator.py:323:__init__ | 412 | 310 | 0,0004 / 0,0003 |
| game.operations.card_operations.MoveCardOperation.__init__ | game/operations/card_operations.py:17:__init__ | 403 | 362 | 0,0004 / 0,0004 |
| game.game_actions.resolution.sba_resolver.SBAViolation.__init__ | &lt;string&gt;:2:__init__ | 403 | 362 | 0,0003 / 0,0003 |
| game.ai.information.runtime_observation.RuntimeObservation.__init__ | game/ai/information/runtime_observation.py:198:__init__ | 371 | 296 | 0,0002 / 0,0001 |
| game.game_actions.generation.decision_abstraction.policies.DeclareAttackersPolicy.__init__ | &lt;string&gt;:2:__init__ | 366 | 310 | 0,0005 / 0,0004 |
| game.game_state.card.Card.__init__ | game/game_state/card.py:349:__init__ | 356 | 352 | 0,0077 / 0,0066 |
| game.game_state.card.CardRuntimeState.__init__ | game/game_state/card.py:191:__init__ | 356 | 352 | 0,0020 / 0,0012 |
| game.game_state.card.ZoneInfo.__init__ | game/game_state/card.py:67:__init__ | 356 | 352 | 0,0003 / 0,0003 |
| helper.change_tracking.TrackedSet.__init__ | helper/change_tracking.py:32:__init__ | 356 | 352 | 0,0002 / 0,0001 |
| game.game_state.modifier.ContinuouosEffectModifierSource.__init__ | &lt;string&gt;:2:__init__ | 356 | 352 | 0,0001 / 0,0001 |
| game.game_state.card.AttachInfo.__init__ | game/game_state/card.py:82:__init__ | 356 | 352 | 0,0001 / 0,0001 |
| game.game_state.modifier.CounterModifierSource.__init__ | &lt;string&gt;:2:__init__ | 356 | 352 | 0,0001 / 0,0001 |
| game.game_state.modifier.AttachedModifierSource.__init__ | &lt;string&gt;:2:__init__ | 356 | 352 | 0,0001 / 0,0001 |
| game.ai.decisions.native_candidates.NativeCombatStrategy.__init__ | game/ai/decisions/native_candidates.py:140:__init__ | 349 | 296 | 0,0001 / 0,0001 |
| game.game_loop.continuations.CombatDeclarationBoundary.__init__ | &lt;string&gt;:2:__init__ | 349 | 296 | 0,0006 / 0,0005 |
| game.game_actions.generation.decision_abstraction.policies.DeclareBlockersPolicy.__init__ | &lt;string&gt;:2:__init__ | 332 | 282 | 0,0005 / 0,0005 |
| game.game_actions.generation.decision_abstraction.auxiliary.ReplacementOrderRequest.__init__ | &lt;string&gt;:2:__init__ | 292 | 249 | 0,0007 / 0,0006 |
| game.game_actions.generation.decision_abstraction.auxiliary.ReplacementOrderPolicy.__init__ | &lt;string&gt;:2:__init__ | 292 | 249 | 0,0006 / 0,0005 |
| game.ai.decisions.generation_candidates.CandidateExpansion.__init__ | &lt;string&gt;:2:__init__ | 286 | 214 | 0,0002 / 0,0002 |
| game.mana.source_cache._Entry.__init__ | &lt;string&gt;:2:__init__ | 206 | 247 | 0,0007 / 0,0008 |
| game.ai.evaluation.search_abstraction.KnowledgeSummary.__init__ | &lt;string&gt;:2:__init__ | 24 | 242 | 0,0000 / 0,0002 |
| game.game_actions.data_structs.action_node.EffectActionNode.__init__ | &lt;string&gt;:2:__init__ | 234 | 169 | 0,0002 / 0,0001 |
| game.ai.simulation.runtime_clone.RuntimeClone.__init__ | game/ai/simulation/runtime_clone.py:143:__init__ | 222 | 163 | 0,0096 / 0,0077 |
| game.ai.decisions.generation_candidates.BeamCombatStrategy.__init__ | game/ai/decisions/generation_candidates.py:132:__init__ | 183 | 218 | 0,0001 / 0,0001 |
| game.game_state.registers.layer_candidate_register.LayerCandidateRegister.__init__ | game/game_state/registers/layer_candidate_register.py:23:__init__ | 214 | 159 | 0,0001 / 0,0001 |
| game.ai.simulation.simulation_clone.SimulationClone.__init__ | game/ai/simulation/simulation_clone.py:49:__init__ | 210 | 155 | 0,0102 / 0,0083 |
| game.game_actions.resolution.operation_executor.OperationExecutor.__init__ | game/game_actions/resolution/operation_executor.py:57:__init__ | 210 | 159 | 0,0007 / 0,0005 |
| game.game_actions.resolution.event_bus.EventBus.__init__ | game/game_actions/resolution/event_bus.py:360:__init__ | 210 | 159 | 0,0001 / 0,0000 |
| game.game_actions.resolution.event_bus.TriggerProcessor.__init__ | game/game_actions/resolution/event_bus.py:445:__init__ | 210 | 159 | 0,0001 / 0,0000 |
| game.game_loop.game_loop.GameLoop.__init__ | game/game_loop/game_loop.py:807:__init__ | 210 | 159 | 0,0005 / 0,0004 |
| game.game_actions.data_structs.ability.EffectBinding.__init__ | &lt;string&gt;:2:__init__ | 210 | 145 | 0,0002 / 0,0001 |
| game.game_actions.data_structs.ability.EffectSequence.__init__ | &lt;string&gt;:2:__init__ | 210 | 145 | 0,0002 / 0,0001 |
| game.game_actions.resolution.resolution_engine.ResolutionEngine.__init__<br>game.simulation.match_runner.LoggedResolutionEngine.__init__ | game/game_actions/resolution/resolution_engine.py:50:__init__ | 210 | 159 | 0,0027 / 0,0020 |
| game.game_actions.resolution.sba_resolver.SBAResolver.__init__ | game/game_actions/resolution/sba_resolver.py:25:__init__ | 210 | 159 | 0,0002 / 0,0001 |
| game.game_actions.resolution.action_processor.ActionProcessor.__init__<br>game.simulation.match_runner.LoggedProcessor.__init__ | game/game_actions/resolution/action_processor.py:23:__init__ | 210 | 159 | 0,0001 / 0,0010 |
| game.mana.source_cache.StaticManaSourceCache.__init__ | game/mana/source_cache.py:43:__init__ | 210 | 155 | 0,0001 / 0,0001 |
| game.game_state.card_snapshot_cache.CardSnapshotCache.__init__ | game/game_state/card_snapshot_cache.py:37:__init__ | 210 | 155 | 0,0001 / 0,0001 |
| game.game_actions.triggers.trigger_registry.NativeTriggerRegistry.__init__ | game/game_actions/triggers/trigger_registry.py:54:__init__ | 210 | 155 | 0,0001 / 0,0000 |
| game.ai.simulation.checkpoint_simulator._CheckpointReplay.__init__<br>game.ai.simulation.search_simulator._Replay.__init__ | game/ai/simulation/search_simulator.py:355:__init__ | 206 | 155 | 0,0010 / 0,0007 |
| game.ai.information.runtime_observation.ObservationReaders.__init__ | game/ai/information/runtime_observation.py:82:__init__ | 206 | 155 | 0,0011 / 0,0008 |
| game.ai.information.runtime_field_cache.RuntimeFieldCache.__init__ | game/ai/information/runtime_field_cache.py:143:__init__ | 206 | 155 | 0,0007 / 0,0005 |
| game.ai.sampling.current_position_worlds._Game.__init__ | &lt;string&gt;:2:__init__ | 191 | 159 | 0,0001 / 0,0000 |
| game.game_actions.generation.decision_abstraction.requests.DeclareAttackersRequest.__init__ | &lt;string&gt;:2:__init__ | 183 | 155 | 0,0003 / 0,0004 |
| game.ai.decisions.attack_candidates.PowerAttackersStrategy.__init__ | game/ai/decisions/attack_candidates.py:211:__init__ | 183 | 155 | 0,0001 / 0,0000 |
| game.game_state.modifier.ContinuousEffect.__post_init__ | game/game_state/modifier.py:343:__post_init__ | 179 | 85 | 0,0009 / 0,0004 |
| game.game_actions.resolution.cost_transaction.RuntimeCheckpoint.__init__ | game/game_actions/resolution/cost_transaction.py:37:__init__ | 179 | 85 | 0,0037 / 0,0017 |
| game.game_actions.resolution.cost_transaction.CostTransaction.__init__ | game/game_actions/resolution/cost_transaction.py:273:__init__ | 179 | 85 | 0,0025 / 0,0013 |
| game.game_actions.data_structs.game_action.StackItem.__post_init__ | game/game_actions/data_structs/game_action.py:300:__post_init__ | 179 | 85 | 0,0003 / 0,0001 |
| game.game_actions.data_structs.game_action.StackItem.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0002 / 0,0001 |
| game.game_state.modifier.ContinuousEffect.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0005 / 0,0002 |
| game.game_state.modifier.TimeStampDuration.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0002 / 0,0001 |
| game.game_state.modifier.DynamicTargetingStrategy.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0001 / 0,0001 |
| game.game_state.modifier.ContinuousEffectDefinition.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0005 / 0,0002 |
| game.game_state.modifier.ContinuousEffectState.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0005 / 0,0002 |
| game.target.continuous_targets.CardIncarnationsSpec.__init__ | &lt;string&gt;:2:__init__ | 179 | 85 | 0,0001 / 0,0001 |
| game.game_actions.mana_effects.SpendManaOperation.__init__ | game/game_actions/mana_effects.py:66:__init__ | 179 | 85 | 0,0008 / 0,0004 |
| game.cards.starter_cards.TemporaryModifierOperation.__init__ | game/cards/starter_cards.py:356:__init__ | 179 | 85 | 0,0011 / 0,0006 |
| game.game_state.registers.effect_register.EffectEntry.__init__ | game/game_state/registers/effect_register.py:21:__init__ | 179 | 85 | 0,0003 / 0,0002 |
| game.game_loop.continuations.CombatDamageBatchBoundary.__init__ | &lt;string&gt;:2:__init__ | 168 | 150 | 0,0003 / 0,0002 |
| game.game_actions.generation.decision_abstraction.requests.DeclareBlockersRequest.__init__ | &lt;string&gt;:2:__init__ | 166 | 141 | 0,0003 / 0,0003 |
| game.ai.search.search_trajectory.TrajectoryTraversal.__init__ | game/ai/search/search_trajectory.py:30:__init__ | 128 | 128 | 0,0000 / 0,0000 |
| game.ai.search.search_trajectory.ContinuationTraversal.__init__ | game/ai/search/search_trajectory.py:102:__init__ | 126 | 119 | 0,0001 / 0,0001 |
| game.game_actions.generation.decision_abstraction.auxiliary.CombatDamageRequest.__init__ | &lt;string&gt;:2:__init__ | 125 | — | 0,0003 / — |
| game.game_actions.generation.decision_abstraction.auxiliary.CombatDamagePolicy.__init__ | &lt;string&gt;:2:__init__ | 125 | — | 0,0003 / — |
| game.ai.search.search_tree_memory.RetainedNode.__init__ | &lt;string&gt;:2:__init__ | 17 | 52 | 0,0000 / 0,0000 |
| game.ai.search.search_tree_memory.EdgeTable.__init__ | game/ai/search/search_tree_memory.py:23:__init__ | 17 | 52 | 0,0002 / 0,0004 |
| game.ai.search.information_set_search.ActionEstimate.__init__ | &lt;string&gt;:2:__init__ | 3 | 46 | 0,0000 / 0,0000 |
| game.cards.starter_cards.ReturnSourceEffect.__init__<br>game.cards.starter_cards.SacrificeOneGoblinEffect.__init__<br>game.cards.starter_cards.ScryOneEffect.__init__<br>game.cards.starter_cards.TapOpponentsEffect.__init__<br>game.game_actions.advanced_effects.DrawEffect.__init__<br>game.game_actions.advanced_effects.InstallShieldEffect.__init__<br>game.game_actions.advanced_effects.SacrificeEffect.__init__<br>game.game_actions.advanced_effects.TemporaryBuffEffect.__init__<br>game.game_actions.card_effects.TapSourceEffect.__init__<br>game.game_actions.control_effects.CounterSpellEffect.__init__<br>game.game_actions.control_effects.DestroyAllCreaturesEffect.__init__<br>game.game_actions.data_structs.effect.Effect.__init__ | game/game_actions/data_structs/effect.py:37:__init__ | 36 | 36 | 0,0000 / 0,0000 |
| game.game_actions.data_structs.ability.SubAbilityDefinition.__init__ | &lt;string&gt;:2:__init__ | 24 | 24 | 0,0000 / 0,0000 |
| game.game_actions.card_effects.MoveSourceEffect.__init__ | game/game_actions/card_effects.py:55:__init__ | 24 | 24 | 0,0000 / 0,0000 |
| game.cards.starter_cards.ConfrontAssaultAbility.__init__<br>game.game_actions.data_structs.ability.CastSpellAbilityDefinition.__init__<br>game.game_actions.permanent_effects.AuraCastAbilityDefinition.__init__ | &lt;string&gt;:2:__init__ | 12 | 12 | 0,0000 / 0,0000 |
| helper.query_system.object_register.SimpleObjectIndexProvider.__init__ | helper/query_system/object_register.py:553:__init__ | 8 | 8 | 0,0001 / 0,0001 |
| helper.query_system.object_register.InMemoryObjectRegister.__init__ | helper/query_system/object_register.py:592:__init__ | 8 | 8 | 0,0001 / 0,0000 |
| helper.query_system.object_register.ObjectStorage.__init__ | helper/query_system/object_register.py:150:__init__ | 8 | 8 | 0,0000 / 0,0000 |
| game.game_state.registers.indexed_register.IndexedRegister.__init__ | game/game_state/registers/indexed_register.py:21:__init__ | 8 | 8 | 0,0000 / 0,0000 |
| game.ai.simulation.search_simulator.SearchSimulator.__init__ | game/ai/simulation/search_simulator.py:707:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.ai.simulation.search_simulator.WorldInitializationMetrics.__init__ | &lt;string&gt;:2:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.target.target_selector.SingleTargetSelector.__init__ | game/target/target_selector.py:154:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.target.target_resolver.TargetSlot.__init__ | &lt;string&gt;:2:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.target.target_selector.MinMaxTegetSelector.__init__ | &lt;string&gt;:2:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.target.target_resolver.TargetResolver.__init__ | &lt;string&gt;:2:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.game_state.registers.player_register.PlayerRegister.__init__ | game/game_state/registers/player_register.py:22:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.game_state.registers.card_register.CardRegister.__init__ | game/game_state/registers/card_register.py:114:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.cards.starter_support.RuleEffect.__init__ | game/cards/starter_support.py:29:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.ai.simulation.runtime_clone.DictionaryCopyPlans.__init__ | game/ai/simulation/runtime_clone.py:27:__init__ | 4 | 4 | 0,0010 / 0,0013 |
| game.ai.sampling.current_position_worlds._PreparedWorld.__init__ | game/ai/sampling/current_position_worlds.py:292:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.ai.simulation.checkpoint_simulator._Checkpoint.__init__ | &lt;string&gt;:2:__init__ | 4 | — | 0,0000 / — |
| game.ai.simulation.checkpoint_simulator.CheckpointSimulator.__init__ | game/ai/simulation/checkpoint_simulator.py:174:__init__ | 4 | 4 | 0,0000 / 0,0000 |
| game.ai.search.information_set_search.InformationSearchMetrics.__init__ | &lt;string&gt;:2:__init__ | 2 | 2 | 0,0000 / 0,0000 |
| game.ai.search.information_set_search.InformationSearchResult.__init__ | &lt;string&gt;:2:__init__ | 2 | 2 | 0,0000 / 0,0000 |
| game.ai.search.information_set_search._Run.__init__ | game/ai/search/information_set_search.py:510:__init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.search.information_set_search.InformationSearchBudget.__post_init__ | game/ai/search/information_set_search.py:50:__post_init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.decisions.search_candidates.ProgressiveWidening.__init__ | &lt;string&gt;:2:__init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.search.information_set_search.InformationSearchBudget.__init__ | &lt;string&gt;:2:__init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.decisions.search_candidates.CandidateOrdering.__init__ | game/ai/decisions/search_candidates.py:23:__init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.evaluation.observation_policy.ObservationOpponentPolicy.__init__ | game/ai/evaluation/observation_policy.py:55:__init__ | 1 | 1 | 0,0000 / 0,0000 |
| game.ai.information.runtime_observation.NativeReadSession.__init__ | &lt;string&gt;:2:__init__ | 1 | 1 | 0,0000 / 0,0000 |

## Některé C-level operace

Jde o počty volání příslušných operací, nikoli úplný počet fyzických alokací nebo bytů. `dict.copy` a `object.__new__` jsou další důvod, proč počet Card.__init__ neudává počet všech kopií karet.

| Operace | Volání 87 | Volání 102 |
| --- | ---: | ---: |
| &lt;built-in method __new__ of type object at ADDRESS&gt; | 756 321 | 575 964 |
| &lt;method 'digest' of '_blake2.blake2b' objects&gt; | 1 847 319 | 2 125 248 |
| &lt;method 'update' of '_blake2.blake2b' objects&gt; | 16 127 284 | 17 981 992 |
| &lt;method 'copy' of 'dict' objects&gt; | 932 537 | 787 855 |
| &lt;method 'copy' of 'set' objects&gt; | 7 528 | 2 818 |

