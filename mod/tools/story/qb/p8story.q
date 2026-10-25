// p8story.qb -- helper scripts of the Project 8 Plus story runtime (tools/story/lua/story/*.lua).
// Installed into the main script pak by tools/modkit/ops_story.py (shared by every story mod in a build).
// The Lua runtime calls these through modapi's qb.call; results come back through the globals below, which the
// runtime polls (dialogue choices, cutscene skip). Talk prompts are the peds library's (peds.talk, Lua).

p8story_version = 1

p8story_dialog_result = -1

p8story_skip = 0

// pad_choose_script of every dialogue button (create_dialog_box -> create_dialog_button -> button_choose_script,
// which has already destroyed the box)
script p8story_choose
    change p8story_dialog_result = <idx>
endscript

// ControlScript of PlayIGCCam: fly the keyframes { LockTo Pos Quat FOV time }, then hold the last one
script p8story_cam_control
    if GotParam keyframes
        GetArraySize <keyframes>
        if ( <array_size> > 1 )
            CCam_SplineMorph keyframes = <keyframes> motion = <motion>
        else
            CCam_DoMorph ( <keyframes> [ 0 ] )
        endif
        CCam_WaitMorph
    endif
    begin
        wait 1 gameframe
    repeat
endscript

// cutscene skip / "press to continue": watch the X button in a spawned script
script p8story_arm_skip
    killspawnedscript id = p8story_skip_watch
    change p8story_skip = 0
    spawnscriptnow p8story_skip_watch id = p8story_skip_watch
endscript

script p8story_skip_watch
    wait 10 gameframes
    begin
        if ControllerPressed X
            change p8story_skip = 1
            break
        endif
        wait 1 gameframe
    repeat
endscript

script p8story_disarm_skip
    killspawnedscript id = p8story_skip_watch
endscript

// reward: a deck appears as bought in the skate shop (ui_shop_decks does the same when you buy one)
script p8story_unlock_deck
    UpdateStructElement struct = shop_decks_bought Element = <deck> value = 1
    change shop_decks_bought = <newstruct>
endscript
