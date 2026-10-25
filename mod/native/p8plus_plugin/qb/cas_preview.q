// Project 8 Plus -- CAS preview on focus (P8Plus ini: cas_preview_on_focus).
//
// Create-A-Skater item lists (shirts, pants, shoes, hats, hair, decks, grip tape, ...) show the
// highlighted item on the skater after a short pause (cas_preview_delay_ms), the way current games do.
// X (choose) keeps it, exactly as before; DONE / back puts back what the skater wore when the list was
// opened (or the last item chosen in it). Only choosing changes the profile that gets saved: a preview
// is undone before the list closes, before a choice is applied, and when the cursor reaches the item
// already worn or DONE.
//
// Our own code, compiled by tools/gen_caspreview.py (thp8-mod tools/qbc) into
// src/p8plus_caspreview_qb.h; the plugin puts it into the game's QB symbol table at run time
// (p8plus_caspreview.c). Nothing of the game is copied:
//   * scripts named p8p_* are new symbols of the plugin;
//   * the four scripts named after game scripts are WRAPPERS: when the game loads its CAS script
//     file (pak/CAS_common, file id 0xa11c8d25), the plugin re-registers the game's own body as
//     p8p_orig_<name> and points <name> at the wrapper, which calls p8p_orig_<name> <...>.
//       edit_skater_highlight_cas_item   the item list's focus hook (empty in the game) -> start the timer
//       edit_skater_set_selected_cas_item the list's per-item create hook -> remember the item's part/desc_id
//       cas_add_item                     choose (X) -> undo the preview first, keep the result after
//       edit_skater_keep_part_prompt     DONE / back -> undo the preview before the list closes
// The committed look is kept in the game's own preview slot (RememberTemporaryAppearance
// Name = cas_preview_temp_profile, as the game's deck shop and CAS do); a preview is
// RestoreTemporaryAppearance + the same part edits X makes, then a model (or board) refresh.
// p8p_cas_preview_frames (int: cas_preview_delay_ms at the game's frame rate) is defined by the plugin;
// p8p_cas_preview_refreshing (int) is 1 while a preview rebuilds the model (the plugin times those).
// State lives in tags of the list's anchor element cas_menu, so it ends with the list:
//   p8p_snap  committed look remembered     p8p_dirty  1 = a preview is on the skater
//   p8p_shown item container shown now      p8p_board  1 = the preview changed only the board

// ---- wrappers (installed over the game's scripts of the CAS file) ---------------------------------
script edit_skater_highlight_cas_item
    p8p_orig_edit_skater_highlight_cas_item <...>
    p8p_cas_preview_focus id = <id>
endscript

script edit_skater_set_selected_cas_item
    p8p_orig_edit_skater_set_selected_cas_item <...>
    p8p_cas_preview_item_created container_id = <container_id> pad_choose_script = <pad_choose_script> pad_choose_params = <pad_choose_params>
endscript

script cas_add_item
    p8p_cas_preview_before_choose
    p8p_orig_cas_add_item <...>
    p8p_cas_preview_after_choose
endscript

script edit_skater_keep_part_prompt
    p8p_cas_preview_end
    p8p_orig_edit_skater_keep_part_prompt <...>
endscript

// ---- helpers ----------------------------------------------------------------------------------------
// item created: items that choose a part (cas_add_item) carry their choose params as a tag
script p8p_cas_preview_item_created
    if GotParam pad_choose_script
        if ChecksumEquals a = <pad_choose_script> b = cas_add_item
            <container_id>:settags p8p_params = <pad_choose_params>
        endif
    endif
endscript

// focus: the first focus of a list remembers the committed look; every focus restarts the timer
script p8p_cas_preview_focus
    if not ScreenElementExists id = cas_menu
        return
    endif
    cas_menu:GetSingleTag p8p_snap
    if not GotParam p8p_snap
        p8p_cas_preview_remember
    endif
    cas_menu:Obj_KillSpawnedScript Name = p8p_cas_preview_wait
    cas_menu:Obj_SpawnScriptNow p8p_cas_preview_wait params = { id = <id> }
endscript

// runs on the list (dies with it); killed and restarted by every focus change: fast scrolling builds nothing.
// Counted in frames, not seconds: a rebuild stalls the game for a few frames and a timed wait would then end in
// the next (long) frame, so held-down scrolling would rebuild at every step.
script p8p_cas_preview_wait
    <frames> = ( $p8p_cas_preview_frames )
    wait <frames> gameframes
    p8p_cas_preview_show id = <id>
endscript

script p8p_cas_preview_show
    if not ScreenElementExists id = cas_menu
        return
    endif
    if not ScreenElementExists id = <id>
        return
    endif
    cas_menu:GetSingleTag p8p_shown
    if GotParam p8p_shown
        if ( <p8p_shown> = <id> )
            return
        endif
    endif
    <dirty> = 0
    cas_menu:GetSingleTag p8p_dirty
    if GotParam p8p_dirty
        <dirty> = <p8p_dirty>
    endif
    <was_board> = 0
    cas_menu:GetSingleTag p8p_board
    if GotParam p8p_board
        <was_board> = <p8p_board>
    endif
    <id>:GetSingleTag p8p_params
    <id>:GetSingleTag selected
    <committed> = 1
    if GotParam p8p_params
        <committed> = 0
        if GotParam selected
            if ( <selected> = 1 )
                <committed> = 1
            endif
        endif
        if ( <committed> = 0 )
            if ( <dirty> = 0 )
                p8p_cas_preview_is_worn <p8p_params>
                <committed> = <worn>
            endif
        endif
    endif
    if ( <committed> = 1 )
        if ( <dirty> = 1 )
            p8p_cas_preview_restore board = <was_board> refresh
        endif
        cas_menu:settags p8p_shown = <id> p8p_dirty = 0 p8p_board = 0
        return
    endif
    p8p_cas_preview_apply <p8p_params> dirty = <dirty> was_board = <was_board>
    cas_menu:settags p8p_shown = <id> p8p_dirty = 1 p8p_board = <board>
endscript

// the item is what the skater wears now (the game marks only items it finds; a missing part = None)
script p8p_cas_preview_is_worn
    <want> = <desc_id>
    RemoveParameter desc_id
    GetCurrentSkaterProfileIndex
    GetPlayerAppearancePart player = <currentSkaterProfileIndex> part = <part>
    if GotParam desc_id
        if ( <desc_id> = <want> )
            return worn = 1
        endif
        return worn = 0
    endif
    if ( <want> = None )
        return worn = 1
    endif
    return worn = 0
endscript

// the committed look + the edits X would make for this item (minus menu updates, sounds and poses)
script p8p_cas_preview_apply
    GetCurrentSkaterProfileIndex
    if ( <dirty> = 1 )
        RestoreTemporaryAppearance player = <currentSkaterProfileIndex> Name = cas_preview_temp_profile NoFaceTexture
    endif
    cas_handle_disqualifications part = <part> desc_id = <desc_id>
    switch <part>
        case CAS_PUNK_Head
        case CAS_URBN_Head
        case CAS_SURF_Head
        case CAS_KID_Head
        case CAS_FEM_Head
            EditPlayerAppearance profile = <currentSkaterProfileIndex> target = ClearPart targetParams = { part = headtop_bone_group }
            EditPlayerAppearance profile = <currentSkaterProfileIndex> target = ClearPart targetParams = { part = head_bone_group }
            EditPlayerAppearance profile = <currentSkaterProfileIndex> target = ClearPart targetParams = { part = Nose_bone_group }
            EditPlayerAppearance profile = <currentSkaterProfileIndex> target = ClearPart targetParams = { part = Jaw_bone_group }
    endswitch
    EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = <part> desc_id = <desc_id> }
    ExtendCrc <part> "_sec" out = sec_checksum
    EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = <sec_checksum> desc_id = None }
    <board> = 0
    if ChecksumEquals a = <part> b = deck_graphic
        <board> = 1
    endif
    if ChecksumEquals a = <part> b = griptape
        <board> = 1
    endif
    <refresh_board> = <board>
    if ( <dirty> = 1 )
        if ( <was_board> = 0 )
            <refresh_board> = 0
        endif
    endif
    p8p_cas_preview_refresh board = <refresh_board>
    return board = <board>
endscript

// board-only changes (deck graphic, grip tape) rebuild only the board, like the game's deck shop
script p8p_cas_preview_refresh
    GetCurrentSkaterProfileIndex
    if InFrontEnd
        <player> = 0
    else
        <player> = <currentSkaterProfileIndex>
    endif
    change p8p_cas_preview_refreshing = 1
    <done> = 0
    if GotParam board
        if ( <board> = 1 )
            RefreshSkaterBoard Skater = <player> profile = <currentSkaterProfileIndex>
            <done> = 1
        endif
    endif
    if ( <done> = 0 )
        RefreshSkaterModel Skater = <player> profile = <currentSkaterProfileIndex>
    endif
    change p8p_cas_preview_refreshing = 0
endscript

script p8p_cas_preview_remember
    GetCurrentSkaterProfileIndex
    RememberTemporaryAppearance player = <currentSkaterProfileIndex> Name = cas_preview_temp_profile NoFaceTexture
    cas_menu:settags p8p_snap = 1 p8p_dirty = 0 p8p_board = 0 p8p_shown = none
endscript

script p8p_cas_preview_restore
    GetCurrentSkaterProfileIndex
    RestoreTemporaryAppearance player = <currentSkaterProfileIndex> Name = cas_preview_temp_profile NoFaceTexture
    if GotParam refresh
        p8p_cas_preview_refresh board = <board>
    endif
endscript

// DONE / back: the list closes with the committed look
script p8p_cas_preview_end
    if not ScreenElementExists id = cas_menu
        return
    endif
    cas_menu:Obj_KillSpawnedScript Name = p8p_cas_preview_wait
    cas_menu:GetSingleTag p8p_dirty
    cas_menu:GetSingleTag p8p_board
    if GotParam p8p_dirty
        if ( <p8p_dirty> = 1 )
            p8p_cas_preview_restore board = <p8p_board> refresh
        endif
    endif
    cas_menu:settags p8p_dirty = 0 p8p_board = 0 p8p_shown = none
endscript

// X: the game applies the item to the committed look (not to a preview), then that becomes committed
script p8p_cas_preview_before_choose
    if not ScreenElementExists id = cas_menu
        return
    endif
    cas_menu:Obj_KillSpawnedScript Name = p8p_cas_preview_wait
    cas_menu:GetSingleTag p8p_dirty
    if GotParam p8p_dirty
        if ( <p8p_dirty> = 1 )
            p8p_cas_preview_restore
        endif
    endif
endscript

script p8p_cas_preview_after_choose
    if not ScreenElementExists id = cas_menu
        return
    endif
    p8p_cas_preview_remember
endscript
