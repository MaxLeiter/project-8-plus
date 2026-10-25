// Project 8 Plus custom goals: runtime library (ops_goals.py adds it, with the generated per-goal part, as
// scripts\mods\p8q_goals.qb in pak/qb.pak). Original code; qbc text syntax (tools/qbc/qbc.py syntax).
//
// Public entry points (for Lua via modapi qb.call, story chapters, menus):
//   p8q_goal_start goal=<id>     add (if needed) + initialize + deactivate the others + activate
//   p8q_goal_unlock goal=<id>    add (if needed) + initialize: its marker / giver appears
//   p8q_goal_lock goal=<id>      deactivate + uninitialize: its marker / giver goes away
// Results (also as events p8q_goal_won / p8q_goal_failed / p8q_goal_quit, data = { goal = <id> }):
//   p8q_last_goal = <id>, p8q_last_result = 1 won / 2 failed / 3 quit, p8q_result_serial += 1 per end,
//   p8q_active_goal = <id> while one runs, else p8q_none. (A global set to `none` does not exist in the game:
//   the loader drops it, verified in game 2026-10-05; hence p8q_none.)
// Per-goal configuration: $p8q_goal_cfg.<id> = { kind title text ... } and $p8q_goal_infos.<id> = goal_info
// (both generated). Callbacks named by the engine (<id>_<checkpoint>_<tool>_<event>, <id>_post_activate, ...) are
// generated per goal and call the p8q_on_* scripts below.

p8q_version = 1

p8q_last_goal = p8q_none

p8q_last_result = 0

p8q_result_serial = 0

p8q_active_goal = p8q_none

p8q_n_success_cb = 0

p8q_n_fail_cb = 0

p8q_n_deactivate = 0

p8q_n_chk_fail = 0

p8q_activation_serial = 0

p8q_result_reported = 0

p8q_gap_count = 0

p8q_trick_count = 0

// ------------------------------------------------------------------------------------------- public entry points
script p8q_goal_start
    if not GotParam goal
        return
    endif
    spawnscriptnow p8q_goal_start_spawned params = { goal = <goal> }
endscript

script p8q_goal_start_spawned
    p8q_goal_ensure_initialized goal = <goal>
    if not GMan_GoalIsInitialized goal = <goal>
        return
    endif
    <was_active> = 0
    if GMan_HasActiveGoals
        <was_active> = 1
    endif
    GMan_DeactivateAllGoals
    <n> = 0
    begin
        if GMan_AllGoalsFinishedDeactivating
            break
        endif
        wait 1 gameframe
        <n> = ( <n> + 1 )
        if ( <n> > 600 )
            break
        endif
    repeat
    if ( <was_active> = 1 )
        // let the ended goal's end-of-run reset happen before the new goal places the skater (seen in game)
        wait 40 gameframes
    endif
    GMan_ActivateGoal goal = <goal>
endscript

script p8q_goal_unlock
    if not GotParam goal
        return
    endif
    spawnscriptnow p8q_goal_ensure_initialized params = { goal = <goal> }
endscript

script p8q_goal_lock
    if not GotParam goal
        return
    endif
    spawnscriptnow p8q_goal_lock_spawned params = { goal = <goal> }
endscript

script p8q_goal_lock_spawned
    if not GMan_GoalExists goal = <goal>
        return
    endif
    if GMan_GoalIsActive goal = <goal>
        GMan_DeactivateGoal goal = <goal>
        <n> = 0
        begin
            wait 2 gameframes
            if not GMan_GoalIsActive goal = <goal>
                break
            endif
            <n> = ( <n> + 1 )
            if ( <n> > 300 )
                break
            endif
        repeat
    endif
    if GMan_GoalIsInitialized goal = <goal>
        GMan_UnInitializeGoal goal = <goal>
    endif
endscript

script p8q_goal_ensure_initialized
    if not GMan_GoalExists goal = <goal>
        if not StructureContains structure = $p8q_goal_infos <goal>
            return
        endif
        GMan_AddGoal goal_info = ( $p8q_goal_infos.<goal> )
    endif
    if not GMan_GoalIsInitialized goal = <goal>
        // a won goal is uninitialized and only initializes again with force_init (goals_menu_select_goal,
        // goal_hsfreeskate_start_run do the same; seen in game 2026-10-06: without it the goal stays down)
        GMan_SetData goal = <goal> params = { force_init = 1 }
        GMan_InitializeGoal goal = <goal>
        <n> = 0
        begin
            if GMan_GoalIsInitialized goal = <goal>
                break
            endif
            wait 2 gameframes
            <n> = ( <n> + 1 )
            if ( <n> > 300 )
                break
            endif
        repeat
        GMan_SetData goal = <goal> params = { force_init = 0 }
    endif
endscript

// ------------------------------------------------------------------------------------------- results
script p8q_report goal = p8q_none result = 0
    if ( <result> = 1 )
        if StructureContains structure = $p8q_goal_cfg <goal>
            if StructureContains structure = ( $p8q_goal_cfg.<goal> ) unlocks
                <next> = ( $p8q_goal_cfg.<goal>.unlocks )
                GetArraySize <next>
                <i> = 0
                begin
                    p8q_goal_unlock goal = ( <next> [ <i> ] )
                    <i> = ( <i> + 1 )
                repeat <array_size>
            endif
        endif
    endif
    change p8q_last_goal = <goal>
    change p8q_last_result = <result>
    change p8q_result_serial = ( $p8q_result_serial + 1 )
    change p8q_result_reported = 1
    switch <result>
        case 1
            BroadcastEvent Type = p8q_goal_won data = { goal = <goal> }
        case 2
            BroadcastEvent Type = p8q_goal_failed data = { goal = <goal> }
        default
            BroadcastEvent Type = p8q_goal_quit data = { goal = <goal> }
    endswitch
endscript

script p8q_goal_won
    change p8q_n_success_cb = ( $p8q_n_success_cb + 1 )
    if GotParam goal_id
        <goal> = <goal_id>
    endif
    if ( $p8q_result_reported = 0 )
        p8q_report goal = <goal> result = 1
    endif
endscript

script p8q_goal_failed
    change p8q_n_fail_cb = ( $p8q_n_fail_cb + 1 )
    if GotParam goal_id
        <goal> = <goal_id>
    endif
    if ( $p8q_result_reported = 0 )
        p8q_report goal = <goal> result = 2
    endif
endscript

// ------------------------------------------------------------------------------------------- engine callbacks
script p8q_on_activate
    change p8q_active_goal = <goal>
    change p8q_result_reported = 0
    change p8q_activation_serial = ( $p8q_activation_serial + 1 )
endscript

script p8q_on_deactivate
    change p8q_n_deactivate = ( $p8q_n_deactivate + 1 )
    if ( $p8q_result_reported = 0 )
        if GMan_HasWonGoalThisTry goal = <goal>
            p8q_report goal = <goal> result = 1
        else
            if GMan_HasWonGoal goal = <goal>
                p8q_report goal = <goal> result = 3
            else
                p8q_report goal = <goal> result = 3
            endif
        endif
    endif
    change p8q_active_goal = p8q_none
    p8q_cleanup goal = <goal>
    if StructureContains structure = ( $p8q_goal_cfg.<goal> ) replay
        spawnscriptnow p8q_replay_later params = { goal = <goal> }
    endif
endscript

// The game uninitializes a won goal after its post_deactivate (goal_should_uninit: won and not a permanent type);
// a free-skate goal with `replay` is initialized again once that happened, so its marker / giver comes back.
script p8q_replay_later
    <n> = 0
    begin
        wait 10 gameframes
        if not GMan_GoalIsInitialized goal = <goal>
            break
        endif
        <n> = ( <n> + 1 )
        if ( <n> > 30 )
            return
        endif
    repeat
    if not GMan_HasActiveGoals
        p8q_goal_ensure_initialized goal = <goal>
    endif
endscript

script p8q_chk_enter
    <cfg> = ( $p8q_goal_cfg.<goal> )
    if StructureContains structure = <cfg> text
        goal_ui_display_message Type = instruction text = ( <cfg>.text )
    endif
    <kind> = ( <cfg>.kind )
    switch <kind>
        case score
            p8q_show_score goal = <goal>
        case combo
            p8q_show_score goal = <goal>
        case collect
            spawnscriptnow p8q_mon_collect id = p8q_goal_scripts params = { goal = <goal> cfg = <cfg> }
        case race
            spawnscriptnow p8q_mon_race id = p8q_goal_scripts params = { goal = <goal> cfg = <cfg> }
        case gap
            spawnscriptnow p8q_mon_gap id = p8q_goal_scripts params = { goal = <goal> cfg = <cfg> }
        case trick
            change p8q_trick_count = 0
            if StructureContains structure = <cfg> points
                p8q_spawn_object index = 0 Model = ( <cfg>.Model ) Pos = ( ( <cfg>.points ) [ 0 ] ) spin = 0 blip = 1
            endif
    endswitch
endscript

script p8q_chk_exit
    p8q_cleanup goal = <goal>
endscript

script p8q_chk_fail
    change p8q_n_chk_fail = ( $p8q_n_chk_fail + 1 )
    p8q_goal_failed goal = <goal>
endscript

script p8q_cleanup
    killspawnedscript id = p8q_goal_scripts
    p8q_kill_objects
    panel_hide_score_in_goal
    goal_ui_kill_message Type = instruction
endscript

script p8q_pass
    GMan_PassCheckpoint goal = <goal>
endscript

script p8q_show_score
    GMan_ScoreAccumulatorReset goal = <goal> tool = acc
    panel_show_score_in_goal
endscript

// ------------------------------------------------------------------------------------------- world objects
p8q_object_ids = [
    p8q_obj_0 p8q_obj_1 p8q_obj_2 p8q_obj_3 p8q_obj_4 p8q_obj_5 p8q_obj_6 p8q_obj_7 p8q_obj_8 p8q_obj_9
    p8q_obj_10 p8q_obj_11 p8q_obj_12 p8q_obj_13 p8q_obj_14 p8q_obj_15 p8q_obj_16 p8q_obj_17 p8q_obj_18 p8q_obj_19
    p8q_obj_20 p8q_obj_21 p8q_obj_22 p8q_obj_23 p8q_obj_24 p8q_obj_25 p8q_obj_26 p8q_obj_27 p8q_obj_28 p8q_obj_29
    p8q_obj_30 p8q_obj_31
]

script p8q_spawn_object index = 0 Model = "gameobjects\\secret_tape\\secret_tape.mdl" spin = 1 blip = 0
    <obj> = ( $p8q_object_ids [ <index> ] )
    if isCreated <obj>
        <obj>:Die
        wait 1 gameframe
    endif
    <components> = [
        { component = motion }
        { component = Model }
        { component = suspend }
        { component = hover }
    ]
    if ( <blip> = 1 )
        AddArrayElement Array = <components> Element = { component = CompassBlip CompassBlipType = goal }
        <components> = <Array>
    endif
    SetSearchAllAssetContexts
    CreateCompositeObject {
        Components = <components>
        params = {
            Name = <obj>
            Model = <Model>
            Pos = <Pos>
            SuspendDistance = 0
            lod_dist1 = 400
            lod_dist2 = 401
        }
    }
    SetSearchAllAssetContexts off
    if ( <spin> = 1 )
        <obj>:Obj_RotY speed = 200
        <obj>:Obj_Hover Amp = 0.25 Freq = 2
    endif
endscript

script p8q_kill_object index = 0
    <obj> = ( $p8q_object_ids [ <index> ] )
    if isCreated <obj>
        <obj>:Die
    endif
endscript

script p8q_kill_objects
    GetArraySize $p8q_object_ids
    <i> = 0
    begin
        <obj> = ( $p8q_object_ids [ <i> ] )
        if isCreated <obj>
            <obj>:Die
        endif
        <i> = ( <i> + 1 )
    repeat <array_size>
endscript

// ------------------------------------------------------------------------------------------- monitors
script p8q_near Pos = (0.0, 0.0, 0.0) Radius = 1.5
    GetDistance ObjectA = Skater posb = <Pos>
    if ( <dist_atob> < <Radius> )
        return true
    endif
    return FALSE
endscript

script p8q_mon_collect
    <pts> = ( <cfg>.points )
    GetArraySize <pts>
    <n> = <array_size>
    <i> = 0
    begin
        p8q_spawn_object index = <i> Model = ( <cfg>.Model ) Pos = ( <pts> [ <i> ] )
        <i> = ( <i> + 1 )
    repeat <n>
    <num> = 0
    begin
        <i> = 0
        begin
            <obj> = ( $p8q_object_ids [ <i> ] )
            if isCreated <obj>
                if p8q_near Pos = ( <pts> [ <i> ] ) Radius = ( <cfg>.Radius )
                    <obj>:Die
                    <num> = ( <num> + 1 )
                    SoundEvent event = GoalUtils_SFX_GotFlag
                    goal_collect_got_object_msg num_collected = <num> Num_Objects = <n> collect_text = ( <cfg>.collect_text )
                    if ( <num> = <n> )
                        p8q_pass goal = <goal>
                        return
                    endif
                endif
            endif
            <i> = ( <i> + 1 )
        repeat <n>
        wait 2 gameframes
    repeat
endscript

script p8q_mon_race
    <pts> = ( <cfg>.points )
    GetArraySize <pts>
    <n> = <array_size>
    <next> = 0
    p8q_spawn_object index = 0 Model = ( <cfg>.Model ) Pos = ( <pts> [ 0 ] ) spin = 0 blip = 1
    if ( <n> > 1 )
        p8q_spawn_object index = 1 Model = ( <cfg>.Model ) Pos = ( <pts> [ 1 ] ) spin = 0
    endif
    begin
        if p8q_near Pos = ( <pts> [ <next> ] ) Radius = ( <cfg>.Radius )
            p8q_kill_object index = <next>
            <next> = ( <next> + 1 )
            SoundEvent event = GoalUtils_SFX_GotFlag
            if ( <next> = <n> )
                p8q_pass goal = <goal>
                return
            endif
            goal_collect_got_object_msg num_collected = <next> Num_Objects = <n> collect_text = ( <cfg>.collect_text )
            p8q_spawn_object index = <next> Model = ( <cfg>.Model ) Pos = ( <pts> [ <next> ] ) spin = 0 blip = 1
            <after> = ( <next> + 1 )
            if ( <after> < <n> )
                p8q_spawn_object index = <after> Model = ( <cfg>.Model ) Pos = ( <pts> [ <after> ] ) spin = 0
            endif
        endif
        wait 1 gameframe
    repeat
endscript

script p8q_mon_gap
    <pts> = ( <cfg>.points )
    change p8q_gap_count = 0
    p8q_spawn_object index = 0 Model = ( <cfg>.Model ) Pos = ( <pts> [ 0 ] ) spin = 0 blip = 1
    p8q_spawn_object index = 1 Model = ( <cfg>.Model ) Pos = ( <pts> [ 1 ] ) spin = 0
    <in_a> = 0
    <in_b> = 0
    begin
        if p8q_near Pos = ( <pts> [ 0 ] ) Radius = ( <cfg>.radius_from )
            if ( <in_a> = 0 )
                if ( ( <cfg>.air ) = 0 )
                    <in_a> = 1
                    Skater:StartGap GapID = p8q_gap flags = ( <cfg>.gap_flags )
                else
                    if Skater:InAir
                        <in_a> = 1
                        Skater:StartGap GapID = p8q_gap flags = ( <cfg>.gap_flags )
                    endif
                endif
            endif
        else
            <in_a> = 0
        endif
        if p8q_near Pos = ( <pts> [ 1 ] ) Radius = ( <cfg>.radius_to )
            if ( <in_b> = 0 )
                <in_b> = 1
                Skater:EndGap GapID = p8q_gap text = ( <cfg>.gap_name ) score = ( <cfg>.gap_score ) GapScript = p8q_gap_got GapScriptParams = { goal = <goal> }
            endif
        else
            <in_b> = 0
        endif
        if ( $p8q_gap_count > ( ( <cfg>.count ) - 1 ) )
            p8q_pass goal = <goal>
            return
        endif
        wait 1 gameframe
    repeat
endscript

script p8q_gap_got
    change p8q_gap_count = ( $p8q_gap_count + 1 )
endscript

script p8q_trick_got
    <cfg> = ( $p8q_goal_cfg.<goal> )
    if StructureContains structure = <cfg> points
        if not p8q_near Pos = ( ( <cfg>.points ) [ 0 ] ) Radius = ( <cfg>.Radius )
            return
        endif
    endif
    change p8q_trick_count = ( $p8q_trick_count + 1 )
    if ( $p8q_trick_count < ( <cfg>.count ) )
        goal_collect_got_object_msg num_collected = $p8q_trick_count Num_Objects = ( <cfg>.count ) collect_text = ( <cfg>.collect_text )
    else
        p8q_pass goal = <goal>
    endif
endscript

// ------------------------------------------------------------------------------------------- pause menu start
// ui_create_pausemenu calls this (patched in by ops_goals when a goal has start.trigger "menu"): one item per goal
// of $p8q_menu_goals, above GAME OPTIONS, while no goal runs.
script p8q_pause_menu_items
    if GMan_HasActiveGoals
        return
    endif
    GetArraySize $p8q_menu_goals
    if ( <array_size> = 0 )
        return
    endif
    <i> = 0
    begin
        <goal> = ( $p8q_menu_goals [ <i> ] )
        add_vert_menu_item {
            text = ( $p8q_goal_cfg.<goal>.menu_text )
            pad_choose_script = p8q_menu_start
            pad_choose_params = { goal = <goal> }
        }
        <i> = ( <i> + 1 )
    repeat <array_size>
endscript

script p8q_menu_start
    spawnscriptnow p8q_goal_start_spawned params = { goal = <goal> }
    do_actual_unpause
    ui_change_state state = uistate_gameplay
endscript
