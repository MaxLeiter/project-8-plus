// Project 8 Plus -- Create-a-Skater Plus (P8Plus ini: cas_plus, cas_items).
//
// Our own QB file: compiled by tools/gen_casplus.py into src/p8plus_casplus_qb.h and registered at run time
// with the game's own QB loader (p8plus_casplus.c). Nothing of the game is copied here.
//
// Name conventions (the generator and the plugin read them):
//   p8c_w_<name>   wrapper for the game script <name>: when the game registers <name>, the plugin keeps the
//                  game's body as p8c_orig_<name> and points <name> at this script
//   p8c_d_<name>   replacement value for the game global <name> (same type)
//   p8c_a_<name>   elements appended to the game global array <name> (same element type)
//   p8c_*          our own scripts and globals
// Globals the plugin sets from the ini when it loads this file: p8c_on, p8c_items_all.

p8c_on = 1
p8c_items_all = 1

// ---------------------------------------------------------------------------------------------------------
// Colour: full ranges and finer steps (stock: saturation 0..90, lightness 0..75, steps 5 / 3 / 2)
// ---------------------------------------------------------------------------------------------------------
p8c_d_colormenu_max_saturation = 100.0
p8c_d_colormenu_max_value = 100.0
p8c_d_colormenu_hue_increment = 3
p8c_d_colormenu_saturation_increment = 2
p8c_d_colormenu_value_increment = 2

p8c_helper_color = {
    helper_text_elements = [
        { text = w"\\b7/\\b4 SELECT" }
        { text = w"\\b6/\\b5 ADJUST" }
        { text = w"\\b1 COPY" }
        { text = w"\\b2 PASTE" }
        { text = w"\\m1 BACK" }
        { text = w"\\be/\\bf ROTATE" }
    ]
}

p8c_helper_swatch = {
    helper_text_elements = [
        { text = w"\\b7/\\b4 SELECT" }
        { text = w"\\b6/\\b5 SWATCH" }
        { text = w"\\b1 COPY" }
        { text = w"\\b2 PASTE" }
        { text = w"\\m1 BACK" }
        { text = w"\\be/\\bf ROTATE" }
    ]
}

// Swatches: h s v as the colour menu stores them; rgba = the chip shown in the row
p8c_swatches = [
    { h = 0 s = 0 v = 100 rgba = [128 128 128 128] }
    { h = 0 s = 0 v = 72 rgba = [92 92 92 128] }
    { h = 0 s = 0 v = 48 rgba = [61 61 61 128] }
    { h = 0 s = 0 v = 24 rgba = [31 31 31 128] }
    { h = 0 s = 0 v = 6 rgba = [8 8 8 128] }
    { h = 0 s = 85 v = 80 rgba = [102 15 15 128] }
    { h = 350 s = 80 v = 52 rgba = [67 13 22 128] }
    { h = 8 s = 58 v = 95 rgba = [122 60 51 128] }
    { h = 26 s = 88 v = 95 rgba = [122 61 15 128] }
    { h = 44 s = 80 v = 80 rgba = [102 81 20 128] }
    { h = 54 s = 85 v = 98 rgba = [125 115 19 128] }
    { h = 88 s = 72 v = 85 rgba = [72 109 30 128] }
    { h = 135 s = 70 v = 38 rgba = [15 49 23 128] }
    { h = 68 s = 55 v = 48 rgba = [57 61 28 128] }
    { h = 176 s = 72 v = 55 rgba = [20 70 67 128] }
    { h = 200 s = 52 v = 95 rgba = [58 101 122 128] }
    { h = 222 s = 80 v = 78 rgba = [20 44 100 128] }
    { h = 226 s = 78 v = 30 rgba = [8 15 38 128] }
    { h = 212 s = 42 v = 58 rgba = [43 58 74 128] }
    { h = 268 s = 62 v = 72 rgba = [62 35 92 128] }
    { h = 312 s = 72 v = 85 rgba = [109 30 93 128] }
    { h = 338 s = 38 v = 96 rgba = [123 76 93 128] }
    { h = 32 s = 38 v = 82 rgba = [105 86 65 128] }
    { h = 24 s = 62 v = 38 rgba = [49 31 18 128] }
]

// relative to the head's own skin texture (h 0 s 0 v 100 = the texture as it is)
p8c_skin_swatches = [
    { h = 0 s = 0 v = 100 rgba = [128 128 128 128] }
    { h = 8 s = 12 v = 100 rgba = [128 114 113 128] }
    { h = 36 s = 18 v = 100 rgba = [128 119 105 128] }
    { h = 55 s = 16 v = 90 rgba = [115 112 97 128] }
    { h = 28 s = 24 v = 80 rgba = [102 89 78 128] }
    { h = 24 s = 32 v = 62 rgba = [79 64 54 128] }
    { h = 20 s = 36 v = 46 rgba = [59 46 38 128] }
    { h = 18 s = 40 v = 32 rgba = [41 31 25 128] }
]

p8c_swatch_index = -1

// colour clipboard (COPY COLOR / PASTE COLOR)
p8c_clip_set = 0
p8c_clip_h = 0
p8c_clip_s = 50
p8c_clip_v = 50

// ---- colour rows on the item pages (appended to the game's option tables) --------------------------------
// part_parent = the text of the page the row belongs to (the game matches it to open the page)
p8c_a_edit_skater_head_options = [
    {
        text = w"Eye Color"
        submenu = p8c_eye_color_menu
        modifier
        part_parent = w"Eyes"
        cam_anim_sub = head
    }
    {
        text = w"Skin Tone"
        submenu = skin_color_menu
        modifier
        part_parent = w"Face"
        cam_anim_sub = head
    }
    {
        text = w"Glasses Color"
        submenu = glasses_color_menu
        is_enabled_script = check_if_part_colorable
        is_enabled_params = { parts = [CAS_PUNK_Glasses, CAS_URBN_Glasses, CAS_SURF_Glasses, CAS_KID_Glasses, CAS_FEM_Glasses] }
        modifier
        part_parent = w"Glasses"
    }
]

p8c_a_edit_skater_torso_options = [
    {
        text = w"Left Accessory Color"
        submenu = accessory1_color_menu
        is_enabled_script = check_if_part_colorable
        is_enabled_params = { parts = [CAS_PUNK_Accessory01, CAS_URBN_Accessory01, CAS_SURF_Accessory01, CAS_KID_Accessory01, CAS_FEM_Accessory01] }
        modifier
        part_parent = w"Left Accessory"
        cam_anim_sub = Accessory
    }
    {
        text = w"Right Accessory Color"
        submenu = accessory2_color_menu
        is_enabled_script = check_if_part_colorable
        is_enabled_params = { parts = [CAS_PUNK_Accessory02, CAS_URBN_Accessory02, CAS_SURF_Accessory02, CAS_KID_Accessory02, CAS_FEM_Accessory02] }
        modifier
        part_parent = w"Right Accessory"
        cam_anim_sub = Accessory
    }
    {
        text = w"Misc Item Color"
        submenu = accessory3_color_menu
        is_enabled_script = check_if_part_colorable
        is_enabled_params = { parts = [CAS_PUNK_Accessory03, CAS_URBN_Accessory03, CAS_SURF_Accessory03, CAS_KID_Accessory03, CAS_FEM_Accessory03] }
        modifier
        part_parent = w"Misc items"
    }
]

p8c_a_edit_skater_leg_options = [
    {
        text = w"Shoes Color"
        submenu = shoe_color_menu
        is_enabled_script = check_if_part_colorable
        is_enabled_params = { parts = [CAS_Shoes] }
        modifier
        part_parent = w"Shoes"
        cam_anim_sub = Feet
    }
]

// ---------------------------------------------------------------------------------------------------------
// Items: every piece in every list (cas_items = all). The game's own compatibility rules still apply.
// Kept hidden: the "None" placeholders and the goal-ped mascot head (its mesh lives in a ped pak).
// ---------------------------------------------------------------------------------------------------------
script p8c_w_cas_item_is_visible
    p8c_orig_cas_item_is_visible <...>
    if ( <is_visible> = 0 )
        if ( $p8c_items_all = 1 )
            <desc> = ( ( <part> [ <part_index> ] ).desc_id )
            if not ( <desc> = None )
                if not ( <desc> = Head_Mascot )
                    <is_visible> = 1
                endif
            endif
        endif
    endif
    return is_visible = <is_visible> secret_color = <secret_color>
endscript

// ---------------------------------------------------------------------------------------------------------
// Skin tone: the head's colour row (skin_color_menu) drives every skin part of the style together
// ---------------------------------------------------------------------------------------------------------
script p8c_is_head_part
    switch <part>
        case CAS_PUNK_Head
        case CAS_URBN_Head
        case CAS_SURF_Head
        case CAS_KID_Head
        case CAS_FEM_Head
            return head = 1
    endswitch
    return head = 0
endscript

p8c_skin_part_names = ["Head", "Hands", "Scalp", "Bare_Torso", "Lower_Legs"]

// the same colour on every skin part of the current style (cas_body = arms / neck)
script p8c_skin_set
    GetCurrentSkaterProfileIndex
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    SetPlayerAppearanceColor player = <currentSkaterProfileIndex> part = cas_body h = <h> s = <s> v = <v> use_default_hsv = <use_default_hsv>
    GetArraySize $p8c_skin_part_names
    <i> = 0
    begin
        create_character_part_id part_name = ( $p8c_skin_part_names [ <i> ] ) character_type = <character_type>
        SetPlayerAppearanceColor player = <currentSkaterProfileIndex> part = <part_id> h = <h> s = <s> v = <v> use_default_hsv = <use_default_hsv>
        <i> = ( <i> + 1 )
    repeat <array_size>
endscript

script p8c_w_colormenu_set_hsv
    if ChecksumEquals a = <part> b = CAS_Eyes
        p8c_eye_set <...>
        return
    endif
    p8c_is_head_part part = <part>
    if ( <head> = 0 )
        p8c_orig_colormenu_set_hsv <...>
        return
    endif
    if not GotParam use_default_hsv
        <use_default_hsv> = 0
    endif
    p8c_skin_set h = <h> s = <s> v = <v> use_default_hsv = <use_default_hsv>
    GetCurrentSkaterProfileIndex
    RefreshSkaterColors Skater = 0 profile = <currentSkaterProfileIndex>
endscript

script p8c_w_colormenu_reset_to_default
    if ChecksumEquals a = <part> b = CAS_Eyes
        p8c_eye_set h = 0 s = 0 v = 100 use_default_hsv = 1
        SoundEvent event = Generic_Menu_Pad_Choose_SFX
        colormenu_refresh_arrows part = <part>
        return
    endif
    p8c_is_head_part part = <part>
    if ( <head> = 0 )
        p8c_orig_colormenu_reset_to_default <...>
        return
    endif
    colormenu_set_hsv part = <part> h = 0 s = 50 v = 50 use_default_hsv = 1
    SoundEvent event = Generic_Menu_Pad_Choose_SFX
    colormenu_refresh_arrows part = <part>
endscript

// ---------------------------------------------------------------------------------------------------------
// Colour menu: coarse steps, swatches, copy / paste
// ---------------------------------------------------------------------------------------------------------
script p8c_w_colormenu_focus
    p8c_orig_colormenu_focus <...>
    if GotParam from_caf
        return
    endif
    if GotParam pad_left_handler
        GetTags
        p8c_copy_paste_handlers id = <id> part = <part>
        create_helper_text helper_pos = <helper_pos> p8c_helper_color scale = 0.9
    endif
endscript

// Square copies the colour of this piece, Circle pastes the copied colour (on every row of a colour menu)
script p8c_copy_paste_handlers
    SetScreenElementProps {
        id = <id>
        event_handlers = [
            { pad_square p8c_color_copy params = { part = <part> } }
            { pad_circle p8c_color_paste params = { part = <part> } }
        ]
        replace_handlers
    }
endscript

// No default colour of its own: show (and start from) h 0 s 0 v 100, the piece as its texture draws it
// (the game shows 0 / 50 / 50, which is a half-dark tint once the first slider moves)
script p8c_w_colormenu_get_default_color
    if ChecksumEquals a = <part> b = CAS_Eyes
        return h = 0 s = 0 v = 100
    endif
    p8c_orig_colormenu_get_default_color <...>
    GetActualCASOptionStruct part = <part> desc_id = <desc_id>
    if not GotParam default_h
        if not GotParam default_s
            if not GotParam default_v
                return h = 0 s = 0 v = 100
            endif
        endif
    endif
    return h = <h> s = <s> v = <v>
endscript

script p8c_w_colormenu_add_options_to_menu
    p8c_orig_colormenu_add_options_to_menu <...>
    if GotParam from_caf
        return
    endif
    if not ( $p8c_on = 1 )
        return
    endif
    sliderbar_add_item {
        text = w"SWATCH"
        focus_script = p8c_swatch_focus
        focus_params = { part = <part> helper_pos = <helper_pos> }
        unfocus_script = p8c_swatch_unfocus
        unfocus_params = { }
        pad_choose_script = nullscript
        child_texture = white2
        icon_id = p8c_swatch_chip
        icon_scale = (6.0, 1.5)
        icon_rgba = [ 64 64 64 0 ]
        icon_pos = (12.0, 0.0)
        text_pos = (-80.0, 2.0)
        text_just = [ Right center ]
        tab = tab3
        anchor_id = p8c_swatch_anchor
        up_arrow_id = p8c_swatch_up_arrow
        down_arrow_id = p8c_swatch_down_arrow
        arrow_pos_up = (0.0, 5.0)
        arrow_pos_down = (0.0, -5.0)
        arrow_rgba = [ 100 90 80 255 ]
        bar_pos = (-34.0, -0.5)
    }
    change p8c_swatch_index = -1
    if ScreenElementExists id = mod_menu
        DoScreenElementMorph id = { mod_menu child = 0 } scale = (32.0, 15.6) time = 0
    endif
endscript

script p8c_swatch_list
    if ChecksumEquals a = <part> b = CAS_Eyes
        return list = ( $p8c_eye_swatches )
    endif
    p8c_is_head_part part = <part>
    if ( <head> = 1 )
        return list = ( $p8c_skin_swatches )
    endif
    return list = ( $p8c_swatches )
endscript

script p8c_swatch_focus
    GetTags
    SetScreenElementProps {
        id = { <id> child = 0 }
        rgba = ( $mainmenu_color_scheme.text_focus_color )
        shadow_rgba = [ 0 , 0 , 0 , 0 ]
    }
    DoScreenElementMorph id = { <id> child = 2 } alpha = 0.5 time = 0
    SetScreenElementProps {
        id = <id>
        event_handlers = [
            { pad_left p8c_swatch_step params = { part = <part> dir = -1 } }
            { pad_right p8c_swatch_step params = { part = <part> dir = 1 } }
        ]
        replace_handlers
    }
    p8c_copy_paste_handlers id = <id> part = <part>
    create_helper_text helper_pos = <helper_pos> p8c_helper_swatch scale = 0.9
endscript

script p8c_swatch_unfocus
    GetTags
    SetScreenElementProps {
        id = { <id> child = 0 }
        rgba = [ 110 110 110 255 ]
        shadow_rgba = $UI_text_shadow_color
    }
    DoScreenElementMorph id = { <id> child = 2 } alpha = 0 time = 0
endscript

script p8c_swatch_step
    p8c_swatch_list part = <part>
    GetArraySize <list>
    <i> = ( $p8c_swatch_index + <dir> )
    if ( <i> < 0 )
        <i> = ( <array_size> - 1 )
    endif
    if ( <i> > ( <array_size> - 1 ) )
        <i> = 0
    endif
    change p8c_swatch_index = <i>
    <c> = ( <list> [ <i> ] )
    SoundEvent event = Pause_Menu_Level_Change_SFX
    colormenu_set_hsv part = <part> h = ( <c>.h ) s = ( <c>.s ) v = ( <c>.v )
    colormenu_refresh_arrows part = <part>
    if ScreenElementExists id = p8c_swatch_chip
        SetScreenElementProps id = p8c_swatch_chip rgba = ( <c>.rgba )
    endif
endscript

script p8c_color_copy
    colormenu_get_hsv part = <part>
    change p8c_clip_h = <h>
    change p8c_clip_s = <s>
    change p8c_clip_v = <v>
    change p8c_clip_set = 1
    SoundEvent event = Generic_Menu_Pad_Choose_SFX
endscript

script p8c_color_paste
    if ( $p8c_clip_set = 0 )
        return
    endif
    colormenu_set_hsv part = <part> h = ( $p8c_clip_h ) s = ( $p8c_clip_s ) v = ( $p8c_clip_v )
    colormenu_refresh_arrows part = <part>
    SoundEvent event = Generic_Menu_Pad_Choose_SFX
endscript

// ---------------------------------------------------------------------------------------------------------
// Body and face shape: the game's own per-bone-group scale (SetPlayerAppearanceScale, ModelApplyBoneScale over
// master_scaling_list) exposed in two new pages, plus bone groups of our own for the face bones no retail group
// uses. Values are percent (100 = stock); ranges in p8c_a_scalingmenu_constraints.
// ---------------------------------------------------------------------------------------------------------
p8c_neck_bone_group = [Bone_Neck]
// the arm and leg meshes are weighted to the twist / split helpers too (unnamed: raw checksums)
p8c_arms_bone_group = [Bone_Bicep_L, Bone_Bicep_R, Bone_Forearm_L, Bone_Forearm_R, #3ddd9ccb, #6e693d48, #adeebcfb, #c7d2a1a8, #9466002b, #57e18198, #431a672c, #b9155a4f]
p8c_legs_bone_group = [Bone_Thigh_L, Bone_Thigh_R, Bone_Knee_L, Bone_Knee_R, Bone_Split_Knee_L, Bone_Split_Knee_R]
p8c_feet_bone_group = [Bone_Ankle_L, Bone_Toe_L, Bone_Ankle_R, Bone_Toe_R]
p8c_eyes_bone_group = [bone_Eye_l, bone_Eye_r]
p8c_lids_bone_group = [bone_Eyelid_Upper_l, bone_Eyelid_Upper_r, bone_Eyelid_Lower_l, bone_Eyelid_Lower_r]
p8c_brows_bone_group = [bone_brow_l, bone_Brow_r, Bone_Brow_Mid, bone_Brow_Mid_l, bone_Brow_Mid_r]
p8c_cheeks_bone_group = [bone_Cheek_l, bone_Cheek_r, bone_Cheek_Upper_l, bone_Cheek_Upper_r]
p8c_lips_bone_group = [bone_Lip_Upper_L, bone_Lip_Upper_Mid, bone_Lip_Upper_r, bone_Lip_Lower_L, bone_Lip_Lower_Mid, bone_Lip_Lower_r, #5378af83, #a97792e0, #7c73f6cf, #867ccbac]
p8c_nose_bone_group = [#e36af2cc]
p8c_mouth_bone_group = [bone_Mouth_l, bone_Mouth_r]
p8c_jawbone_bone_group = [bone_Jaw]

p8c_a_master_scaling_list = [
    { part = p8c_neck_bone_group bone_scaling = 1 }
    { part = p8c_arms_bone_group bone_scaling = 1 }
    { part = p8c_legs_bone_group bone_scaling = 1 }
    { part = p8c_feet_bone_group bone_scaling = 1 }
    { part = p8c_eyes_bone_group bone_scaling = 1 }
    { part = p8c_lids_bone_group bone_scaling = 1 }
    { part = p8c_brows_bone_group bone_scaling = 1 }
    { part = p8c_cheeks_bone_group bone_scaling = 1 }
    { part = p8c_lips_bone_group bone_scaling = 1 }
    { part = p8c_mouth_bone_group bone_scaling = 1 }
    { part = p8c_jawbone_bone_group bone_scaling = 1 }
    { part = p8c_nose_bone_group bone_scaling = 1 }
]

p8c_a_scalingmenu_constraints = [
    { part = p8c_neck_bone_group male_min = 70.0 male_max = 150.0 female_min = 70.0 female_max = 150.0 }
    { part = p8c_arms_bone_group male_min = 75.0 male_max = 135.0 female_min = 75.0 female_max = 135.0 }
    { part = p8c_legs_bone_group male_min = 75.0 male_max = 135.0 female_min = 75.0 female_max = 135.0 }
    { part = p8c_feet_bone_group male_min = 80.0 male_max = 130.0 female_min = 80.0 female_max = 130.0 }
    { part = p8c_eyes_bone_group male_min = 70.0 male_max = 140.0 female_min = 70.0 female_max = 140.0 }
    { part = p8c_lids_bone_group male_min = 60.0 male_max = 150.0 female_min = 60.0 female_max = 150.0 }
    { part = p8c_brows_bone_group male_min = 60.0 male_max = 150.0 female_min = 60.0 female_max = 150.0 }
    { part = p8c_cheeks_bone_group male_min = 60.0 male_max = 150.0 female_min = 60.0 female_max = 150.0 }
    { part = p8c_lips_bone_group male_min = 60.0 male_max = 150.0 female_min = 60.0 female_max = 150.0 }
    { part = p8c_mouth_bone_group male_min = 70.0 male_max = 140.0 female_min = 70.0 female_max = 140.0 }
    { part = p8c_jawbone_bone_group male_min = 70.0 male_max = 140.0 female_min = 70.0 female_max = 140.0 }
    { part = p8c_nose_bone_group male_min = 60.0 male_max = 150.0 female_min = 60.0 female_max = 150.0 }
]

// slider sets (labels for the pad; axis flags as the scaling sliders take them)
// Bone-local axes in the bind pose (model space: y up, z forward, x across), from the skeletons:
//   spine, neck, head and every face bone: local X = up, Y = forward, Z = across
//   arms: X = along the arm; legs: X = down the leg; collars: X = across; ankles: X = forward
p8c_s_limb = [ { text = w"LENGTH" X = 1 } { text = w"THICKNESS" y = 1 z = 1 } ]
p8c_s_feet = [ { text = w"SIZE" X = 1 y = 1 z = 1 } { text = w"LENGTH" X = 1 } ]
p8c_s_trunk = [ { text = w"SIZE" X = 1 y = 1 z = 1 } { text = w"WIDTH" z = 1 } { text = w"DEPTH" y = 1 } ]
p8c_s_shoulders = [ { text = w"SIZE" X = 1 y = 1 z = 1 } { text = w"DEPTH" y = 1 } ]
p8c_s_face = [ { text = w"SIZE" X = 1 y = 1 z = 1 } { text = w"WIDTH" z = 1 } { text = w"HEIGHT" X = 1 } { text = w"DEPTH" y = 1 } ]
p8c_s_whole = [ { text = w"SIZE" X = 1 y = 1 z = 1 } { text = w"HEIGHT" y = 1 } { text = w"WIDTH" X = 1 } ]
p8c_s_size = [ { text = w"SIZE" X = 1 y = 1 z = 1 } ]

p8c_body_options = [
    { group_title = w"BODY SHAPE" text = w"Overall Size" submenu = p8c_size_menu should_add_scaling_options p8c_shape bone_group = object_scaling scaling_params = { p8c_sliders = p8c_s_whole } male_part = cas_body female_part = cas_body }
    { text = w"Head Size" submenu = p8c_head_menu should_add_scaling_options p8c_shape bone_group = head_bone_group scaling_params = { p8c_sliders = p8c_s_size } male_part = CAS_PUNK_Head female_part = CAS_FEM_Head }
    { text = w"Neck" submenu = p8c_neck_menu should_add_scaling_options p8c_shape bone_group = p8c_neck_bone_group scaling_params = { p8c_sliders = p8c_s_limb } male_part = cas_body female_part = cas_body }
    { text = w"Shoulders" submenu = p8c_torso_menu should_add_scaling_options p8c_shape bone_group = torso_bone_group scaling_params = { p8c_sliders = p8c_s_shoulders } male_part = cas_body female_part = cas_body }
    { text = w"Stomach" submenu = p8c_stomach_menu should_add_scaling_options p8c_shape bone_group = stomach_bone_group scaling_params = { p8c_sliders = p8c_s_trunk } male_part = cas_body female_part = cas_body }
    { text = w"Arms" submenu = p8c_arms_menu should_add_scaling_options p8c_shape bone_group = p8c_arms_bone_group scaling_params = { p8c_sliders = p8c_s_limb } male_part = cas_body female_part = cas_body }
    { text = w"Hands" submenu = p8c_hands_menu should_add_scaling_options p8c_shape bone_group = hands_bone_group scaling_params = { p8c_sliders = p8c_s_size } male_part = cas_body female_part = cas_body }
    { text = w"Legs" submenu = p8c_legs_menu should_add_scaling_options p8c_shape bone_group = p8c_legs_bone_group scaling_params = { p8c_sliders = p8c_s_limb } male_part = cas_body female_part = cas_body }
    { text = w"Feet" submenu = p8c_feet_menu should_add_scaling_options p8c_shape bone_group = p8c_feet_bone_group scaling_params = { p8c_sliders = p8c_s_feet } male_part = cas_body female_part = cas_body }
]

p8c_face_options = [
    { group_title = w"FACE SHAPE" text = w"Eyes" submenu = p8c_eyes_menu should_add_scaling_options p8c_shape bone_group = p8c_eyes_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Eyelids" submenu = p8c_lids_menu should_add_scaling_options p8c_shape bone_group = p8c_lids_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Brows" submenu = p8c_brows_menu should_add_scaling_options p8c_shape bone_group = p8c_brows_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Cheeks" submenu = p8c_cheeks_menu should_add_scaling_options p8c_shape bone_group = p8c_cheeks_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Nose" submenu = p8c_nose_menu should_add_scaling_options p8c_shape bone_group = p8c_nose_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Lips" submenu = p8c_lips_menu should_add_scaling_options p8c_shape bone_group = p8c_lips_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Mouth Width" submenu = p8c_mouth_menu should_add_scaling_options p8c_shape bone_group = p8c_mouth_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
    { text = w"Jaw" submenu = p8c_jaw_menu should_add_scaling_options p8c_shape bone_group = p8c_jawbone_bone_group scaling_params = { p8c_sliders = p8c_s_face } male_part = cas_body female_part = cas_body cam_anim_sub = head }
]

// our slider rows (pad-friendly labels) instead of the X / Y / Z / XZ / XYZ set
script p8c_w_scalingmenu_add_options_to_menu
    if not GotParam p8c_sliders
        p8c_orig_scalingmenu_add_options_to_menu <...>
        return
    endif
    if GotParam cas_part
        scalingmenu_resolve_head_bone_scale part = <part> cas_part = <cas_part>
    endif
    SetScreenElementProps id = current_menu spacing_between = scalingmenu_spacing_between
    GetArraySize <p8c_sliders>
    <i> = 0
    begin
        <sl> = ( <p8c_sliders> [ <i> ] )
        <ax> = 0
        <ay> = 0
        <az> = 0
        if StructureContains structure = <sl> X
            <ax> = 1
        endif
        if StructureContains structure = <sl> y
            <ay> = 1
        endif
        if StructureContains structure = <sl> z
            <az> = 1
        endif
        formattext ChecksumName = sid "p8c_slider_%i" i = <i>
        p8c_shape_addslider text = ( <sl>.text ) affectX = <ax> affectY = <ay> affectZ = <az> id = <sid> part = <part>
        <i> = ( <i> + 1 )
    repeat <array_size>
    sliderbar_add_text_item {
        text = w"RESET TO DEFAULT"
        pad_choose_script = scalingmenu_reset_to_default
        pad_choose_params = { part = <part> cas_part = <cas_part> }
    }
    create_helper_text generic_helper_text_color_menu
endscript

// head-model default scales are resolved for the four retail head groups only; ours start at 100
script p8c_w_scalingmenu_resolve_head_bone_scale
    switch <part>
        case headtop_bone_group
        case head_bone_group
        case Nose_bone_group
        case Jaw_bone_group
            p8c_orig_scalingmenu_resolve_head_bone_scale <...>
    endswitch
endscript

// BODY SHAPE / FACE SHAPE in the CUSTOMIZE list, just above DONE
script p8c_w_add_cas_menu_item
    if GotParam id
        if ChecksumEquals a = <id> b = cas_done
            if ScreenElementExists id = edit_skater_vmenu
                if ( $p8c_on = 1 )
                    p8c_orig_add_cas_menu_item text = w"BODY" cas_heading
                    p8c_orig_add_cas_menu_item {
                        text = w"BODY SHAPE"
                        pad_choose_script = p8c_open_shape
                        pad_choose_params = { options_array = p8c_body_options cam_anim = fullbody cas_index = 42 }
                        scale_highlight_text = 0
                        tag_grid_x = 42
                    }
                    p8c_orig_add_cas_menu_item {
                        text = w"FACE SHAPE"
                        pad_choose_script = p8c_open_shape
                        pad_choose_params = { options_array = p8c_face_options cam_anim = head cas_index = 43 }
                        scale_highlight_text = 0
                        tag_grid_x = 43
                    }
                    p8c_orig_add_cas_menu_item {
                        text = w"PRO BODY"
                        pad_choose_script = p8c_pro_list
                        scale_highlight_text = 0
                        tag_grid_x = 44
                    }
                    p8c_orig_add_cas_menu_item text = w"LOOKS" cas_heading
                    <saved> = w""
                    if ( $p8c_look_saved = 1 )
                        <saved> = w"Saved"
                        change p8c_look_saved = 0
                    endif
                    p8c_orig_add_cas_menu_item {
                        text = w"SAVE LOOK"
                        pad_choose_script = p8c_look_save
                        scale_highlight_text = 0
                        extra_text = <saved>
                        cas_summary
                        tag_grid_x = 45
                        extras_offset = (30.0, 0.0)
                    }
                    p8c_orig_add_cas_menu_item {
                        text = w"LOAD LOOK"
                        pad_choose_script = p8c_look_list
                        scale_highlight_text = 0
                        tag_grid_x = 46
                    }
                endif
            endif
        endif
    endif
    // the game's add_cas_menu_item ends with "return item_container_id = <container_id>": its callers hang
    // per-item elements off that container (the trick list's trick names, leaderboard rows, the missions
    // list's waypoint icons). A wrapper has to hand it on, or those elements are never created (the trick
    // list then shows only the button combos, no trick names). The extra items above return one too:
    // dropped first, so neither the call below nor our caller gets a stale container.
    RemoveParameter item_container_id
    p8c_orig_add_cas_menu_item <...>
    if GotParam item_container_id
        return item_container_id = <item_container_id>
    endif
endscript

script p8c_open_shape
    create_scale_options_menu options_array = <options_array> cam_anim = <cam_anim> cas_index = <cas_index>
    cas_killskatercamanim
    cas_setup_rotating_camera cam_anim = <cam_anim>
    if ( <cam_anim> = head )
        MakeSkaterGoto CasAI params = { no_init pause_skater }
    endif
endscript

// a shape page's sliders take the place of the page's list (hidden while they are open; the game's exit from
// the slider menu shows it again), so they never cover the skater
script p8c_w_create_cas_modifier_menu
    if GotParam p8c_shape
        if ScreenElementExists id = scaling_menu
            DoScreenElementMorph id = scaling_menu alpha = 0 time = 0
        endif
        p8c_orig_create_cas_modifier_menu <...> Pos = (70.0, 130.0) helper_pos = (246.0, 296.0)
        return
    endif
    if ChecksumEquals a = <submenu> b = p8c_eye_color_menu
        change p8c_menu_eye = 1
        p8c_orig_create_cas_modifier_menu <...>
        change p8c_menu_eye = 0
        return
    endif
    p8c_orig_create_cas_modifier_menu <...>
endscript

// one slider row of a shape page, laid out like the colour menu's rows (the game's scaling rows are sized for
// the board page); the game's scaling handlers do the work
script p8c_shape_addslider
    MangleChecksums a = <id> b = up_arrow
    <up_arrow_id> = <mangled_id>
    MangleChecksums a = <id> b = down_arrow
    <down_arrow_id> = <mangled_id>
    MangleChecksums a = <id> b = slider_bar
    <slider_bar_id> = <mangled_id>
    MangleChecksums a = <id> b = anchor
    <anchor_id> = <mangled_id>
    sliderbar_add_item {
        text = <text>
        focus_script = p8c_shape_focus
        focus_params = {
            pad_left_handler = scalingmenu_decrement
            pad_right_handler = scalingmenu_increment
            handler_params = { part = <part> affectX = <affectX> affectY = <affectY> affectZ = <affectZ> up_arrow_id = <up_arrow_id> down_arrow_id = <down_arrow_id> }
            up_arrow_id = <up_arrow_id>
            down_arrow_id = <down_arrow_id>
            part = <part>
            scaling_bar_id = <slider_bar_id>
            affectX = <affectX>
            affectY = <affectY>
            affectZ = <affectZ>
        }
        unfocus_script = p8c_shape_unfocus
        unfocus_params = {
            up_arrow_id = <up_arrow_id>
            down_arrow_id = <down_arrow_id>
            scaling_bar_id = <slider_bar_id>
        }
        pad_choose_script = nullscript
        child_texture = bw_slider
        icon_id = <slider_bar_id>
        icon_scale = (2.4, 2.0)
        icon_rgba = [ 40 40 40 118 ]
        icon_pos = (12.0, 0.0)
        text_pos = (-80.0, 2.0)
        text_just = [ Right center ]
        tab = tab3
        anchor_id = <anchor_id>
        up_arrow_id = <up_arrow_id>
        down_arrow_id = <down_arrow_id>
        arrow_pos_up = (0.0, 5.0)
        arrow_pos_down = (0.0, -5.0)
        arrow_rgba = [ 100 90 80 255 ]
        bar_pos = (-34.0, -0.5)
    }
endscript

script p8c_shape_focus
    scalingmenu_focus <...>
    GetSingleTag id
    SetScreenElementProps {
        id = { <id> child = 0 }
        rgba = ( $mainmenu_color_scheme.text_focus_color )
        shadow_rgba = [ 0 , 0 , 0 , 0 ]
    }
    DoScreenElementMorph id = { <id> child = 2 } alpha = 0.5 time = 0
endscript

script p8c_shape_unfocus
    scalingmenu_unfocus <...>
    GetSingleTag id
    SetScreenElementProps {
        id = { <id> child = 0 }
        rgba = [ 110 110 110 255 ]
        shadow_rgba = $UI_text_shadow_color
    }
endscript

// ---------------------------------------------------------------------------------------------------------
// Eye colour: kept as the colour of the CAS_Eyes part (h s v use_default_hsv, saved with the skater; the eye
// items have no materials, so the game's own tint never touches them). The plugin recolours the iris of the shared eye texture to it (p8plus_caseyes.c), so the whites stay white.
// ---------------------------------------------------------------------------------------------------------
p8c_menu_eye = 0
p8c_eye_editable = [{ part = CAS_Eyes colormenu = p8c_eye_color_menu }]

p8c_eye_swatches = [
    { h = 0 s = 0 v = 100 rgba = [128 128 128 128] }
    { h = 30 s = 55 v = 70 rgba = [90 68 40 128] }
    { h = 38 s = 70 v = 90 rgba = [115 87 35 128] }
    { h = 80 s = 45 v = 80 rgba = [87 102 56 128] }
    { h = 125 s = 60 v = 75 rgba = [38 96 46 128] }
    { h = 185 s = 50 v = 90 rgba = [58 112 115 128] }
    { h = 210 s = 70 v = 100 rgba = [38 83 128 128] }
    { h = 215 s = 25 v = 85 rgba = [82 91 109 128] }
    { h = 265 s = 55 v = 85 rgba = [78 49 109 128] }
    { h = 0 s = 70 v = 75 rgba = [96 29 29 128] }
    { h = 0 s = 0 v = 30 rgba = [38 38 38 128] }
]

// (the Eye Color row is in p8c_a_edit_skater_head_options above)

script p8c_w_build_master_editable_list
    p8c_orig_build_master_editable_list <...>
    if ( $p8c_menu_eye = 1 )
        return master_editable_list = ( <master_editable_list> + $p8c_eye_editable )
    endif
    return master_editable_list = <master_editable_list>
endscript

script p8c_w_colormenu_get_hsv
    if ChecksumEquals a = <part> b = CAS_Eyes
        GetCurrentSkaterProfileIndex
        if GetPlayerAppearancePart player = <currentSkaterProfileIndex> part = CAS_Eyes
            if GotParam h
                if GotParam use_default_hsv
                    if ( <use_default_hsv> = 0 )
                        return h = <h> s = <s> v = <v> use_default_hsv = 0
                    endif
                endif
            endif
        endif
        return h = 0 s = 0 v = 100 use_default_hsv = 1
    endif
    p8c_orig_colormenu_get_hsv <...>
endscript

// a colour of our own needs the base (brown) eye texture: the stock blue / green / black eyes are other textures
script p8c_eye_set
    if not GotParam use_default_hsv
        <use_default_hsv> = 0
    endif
    GetCurrentSkaterProfileIndex
    if ( <use_default_hsv> = 0 )
        p8c_eye_desc
        if GotParam eye_desc
            if not ( <eye_desc> = #"Brown Eyes" )
                EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = CAS_Eyes desc_id = #"Brown Eyes" }
                RefreshSkaterModel Skater = 0 profile = <currentSkaterProfileIndex>
            endif
        endif
    endif
    SetPlayerAppearanceColor player = <currentSkaterProfileIndex> part = CAS_Eyes h = <h> s = <s> v = <v> use_default_hsv = <use_default_hsv>
    p8c_eye_apply
endscript

// (a script of its own: GetPlayerAppearancePart returns the part's h s v too, which must not overwrite the caller's)
script p8c_eye_desc
    GetCurrentSkaterProfileIndex
    if GetPlayerAppearancePart player = <currentSkaterProfileIndex> part = CAS_Eyes
        return eye_desc = <desc_id>
    endif
endscript

// the current skater's eye colour to the plugin (PrintStruct = the retail no-op the plugin listens to)
script p8c_eye_apply
    GetCurrentSkaterProfileIndex
    <eh> = 0
    <es> = 0
    <ev> = 100
    <ed> = 1
    if GetPlayerAppearancePart player = <currentSkaterProfileIndex> part = CAS_Eyes
        if GotParam h
            <eh> = <h>
            <es> = <s>
            <ev> = <v>
            if GotParam use_default_hsv
                <ed> = <use_default_hsv>
            endif
        endif
    endif
    PrintStruct p8c_cmd = p8c_eye h = <eh> s = <es> v = <ev> use_default_hsv = <ed>
endscript

// a stock eye colour chosen from the list replaces ours
script p8c_w_cas_add_item
    p8c_orig_cas_add_item <...>
    if GotParam part
        if ChecksumEquals a = <part> b = CAS_Eyes
            GetCurrentSkaterProfileIndex
            SetPlayerAppearanceColor player = <currentSkaterProfileIndex> part = CAS_Eyes h = 0 s = 0 v = 100 use_default_hsv = 1
            p8c_eye_apply
        endif
    endif
endscript

// every skater model build shows the current skater's eye colour
script p8c_w_create_model_from_appearance
    p8c_orig_create_model_from_appearance <...>
    if ( $p8c_on = 1 )
        p8c_eye_apply
    endif
endscript

// ---------------------------------------------------------------------------------------------------------
// Looks: SAVE LOOK / LOAD LOOK (files in <mods>\_cas_presets, written and read by the plugin: p8plus_caslooks.c)
// ---------------------------------------------------------------------------------------------------------
p8c_look = { }
p8c_look_names = [w"-"]
p8c_look_count = 0
p8c_look_result = 0
p8c_look_saved = 0

script p8c_look_save
    GetCurrentSkaterProfileIndex
    GetPlayerAppearance player = <currentSkaterProfileIndex>
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> display_name
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    change p8c_look_result = 0
    PrintStruct p8c_cmd = p8c_save_look look = <profile> name = <display_name> style = <character_type>
    if ( $p8c_look_result = 1 )
        change p8c_look_saved = 1
        SoundEvent event = Generic_Menu_Pad_Choose_SFX
    endif
    change current_cas_index = 45
    create_edit_skater_menu
endscript

script p8c_look_list
    change current_cas_index = 46
    GetCurrentSkaterProfileIndex
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    PrintStruct p8c_cmd = p8c_list_looks style = <character_type>
    FireEvent Type = unfocus target = current_menu
    // a short list without the scrolling window (it would start the rows part-way down the panel)
    if ( $p8c_look_count > 6 )
        make_cas_menu {
            menu_bg_scale = (0.97999996, 0.92999995)
            menu_bg_pos = (-30.0, 0.0)
            highlight_scale = (2.12, 1.65)
            bar_scale = (2.1399999, 1.6)
            mask_scale = (1.0, 1.1)
            mask_rot_angle = ( 6.5 )
            scroll_bar_offset = (0.0, 0.0)
            mask_pos = (100.0, 102.0)
            parent = root_window
            pad_back_script = create_edit_skater_menu
            title = w"LOAD LOOK"
            menu_id = p8c_looks_menu
            vmenu_id = p8c_looks_vmenu
            scrolling
        }
    else
        make_cas_menu {
            menu_bg_scale = (0.97999996, 0.92999995)
            menu_bg_pos = (-30.0, 0.0)
            highlight_scale = (2.12, 1.65)
            bar_scale = (2.1399999, 1.6)
            mask_scale = (1.0, 1.1)
            mask_rot_angle = ( 6.5 )
            scroll_bar_offset = (0.0, 0.0)
            mask_pos = (100.0, 102.0)
            parent = root_window
            pad_back_script = create_edit_skater_menu
            title = w"LOAD LOOK"
            menu_id = p8c_looks_menu
            vmenu_id = p8c_looks_vmenu
        }
    endif
    SetScreenElementProps {
        id = p8c_looks_vmenu
        event_handlers = [
            { pad_l1 cas_rotate_skater_left }
            { pad_r1 cas_rotate_skater_right }
        ]
        replace_handlers
    }
    if ScreenElementExists id = current_scrolling_menu
        DoScreenElementMorph id = current_scrolling_menu alpha = 0
    endif
    if ( $p8c_look_count = 0 )
        add_cas_menu_item text = w"NO SAVED LOOKS YET" not_focusable
    else
        <i> = 0
        <n> = ( $p8c_look_count )
        begin
            GetUpperCaseString ( $p8c_look_names [ <i> ] )
            formattext ChecksumName = lid "p8c_look_item_%i" i = <i>
            add_cas_menu_item {
                text = <UpperCaseString>
                id = <lid>
                pad_choose_script = p8c_look_load
                pad_choose_params = { index = <i> }
            }
            <i> = ( <i> + 1 )
        repeat <n>
    endif
    add_cas_menu_item text = w"DONE" id = p8c_looks_done pad_choose_script = create_edit_skater_menu
    wait 2 gameframes
    if ScreenElementExists id = current_scrolling_menu
        SetScreenElementProps id = current_scrolling_menu reset_window_top
        DoScreenElementMorph id = current_scrolling_menu alpha = 1
    endif
    cas_menu_finish build_pause_parts = 0
endscript

// the look goes over the current appearance: a part the file leaves out (its item is gone) keeps the current one
script p8c_look_load
    change p8c_look_result = 0
    PrintStruct p8c_cmd = p8c_load_look index = <index>
    if ( $p8c_look_result = 1 )
        GetCurrentSkaterProfileIndex
        GetPlayerAppearance player = <currentSkaterProfileIndex>
        SetPlayerAppearance player = <currentSkaterProfileIndex> appearance_structure = ( <profile> + $p8c_look )
        RefreshSkaterModel Skater = 0 profile = <currentSkaterProfileIndex>
        p8c_eye_apply
        SoundEvent event = Generic_Menu_Pad_Choose_SFX
    endif
endscript

// ---------------------------------------------------------------------------------------------------------
// Pro bodies: the pro and secret skaters' whole models are items of cas_body (hidden in every list). PRO BODY
// puts one on the created skater (the style's parts are cleared, as a pro's own appearance has none); STYLE
// BODY goes back to the style's first default look. Shape sliders, deck and board work on top of it.
// ---------------------------------------------------------------------------------------------------------
p8c_pro_bodies = [
    { desc_id = hawk text = w"Tony Hawk" sex = male }
    { desc_id = burnquist text = w"Bob Burnquist" sex = male }
    { desc_id = dollin text = w"Dustin Dollin" sex = male }
    { desc_id = Huston text = w"Nyjah Huston" sex = male }
    { desc_id = Margera text = w"Bam Margera" sex = male }
    { desc_id = mullen text = w"Rodney Mullen" sex = male }
    { desc_id = rodriguez text = w"Paul Rodriguez" sex = male }
    { desc_id = Sheckler text = w"Ryan Sheckler" sex = male }
    { desc_id = Song text = w"Daewon Song" sex = male }
    { desc_id = vallely text = w"Mike Vallely" sex = male }
    { desc_id = williams text = w"Stevie Williams" sex = male }
    { desc_id = barker text = w"Travis Barker" sex = male }
    { desc_id = Hosoi text = w"Christian Hosoi" sex = male }
    { desc_id = staab text = w"Kevin Staab" sex = male }
    { desc_id = zombie text = w"Zombie" sex = male }
    { desc_id = VOODOO text = w"Voodoo" sex = male }
    { desc_id = NS_mascot text = w"Mascot" sex = male }
    { desc_id = Adams text = w"Lyn-z Adams Hawkins" sex = female }
]

p8c_style_part_names = ["Head", "Scalp", "Hair", "Hat_Hair", "Legs", "Lower_Legs", "Hands", "Bare_Torso", "Torso", "Socks", "Glasses", "Hat", "Accessory01", "Accessory02", "Accessory03"]

script p8c_pro_list
    change current_cas_index = 44
    GetCurrentSkaterProfileIndex
    GetSkaterSex player = <currentSkaterProfileIndex>
    FireEvent Type = unfocus target = current_menu
    make_cas_menu {
        menu_bg_scale = (0.97999996, 0.92999995)
        menu_bg_pos = (-30.0, 0.0)
        highlight_scale = (2.12, 1.65)
        bar_scale = (2.1399999, 1.6)
        mask_scale = (1.0, 1.1)
        mask_rot_angle = ( 6.5 )
        scroll_bar_offset = (0.0, 0.0)
        mask_pos = (100.0, 102.0)
        parent = root_window
        pad_back_script = create_edit_skater_menu
        title = w"PRO BODY"
        menu_id = p8c_pro_menu
        vmenu_id = p8c_pro_vmenu
        scrolling
    }
    SetScreenElementProps {
        id = p8c_pro_vmenu
        event_handlers = [
            { pad_l1 cas_rotate_skater_left }
            { pad_r1 cas_rotate_skater_right }
        ]
        replace_handlers
    }
    DoScreenElementMorph id = current_scrolling_menu alpha = 0
    add_cas_menu_item text = w"STYLE BODY" id = p8c_pro_style pad_choose_script = p8c_pro_body_clear
    GetArraySize $p8c_pro_bodies
    <i> = 0
    begin
        <p> = ( $p8c_pro_bodies [ <i> ] )
        <ok> = 1
        if ( <is_male> )
            if ( ( <p>.sex ) = female )
                <ok> = 0
            endif
        else
            if ( ( <p>.sex ) = male )
                <ok> = 0
            endif
        endif
        if ( <ok> = 1 )
            GetUpperCaseString ( <p>.text )
            formattext ChecksumName = pid "p8c_pro_item_%i" i = <i>
            add_cas_menu_item {
                text = <UpperCaseString>
                id = <pid>
                pad_choose_script = p8c_pro_body_set
                pad_choose_params = { desc_id = ( <p>.desc_id ) }
            }
        endif
        <i> = ( <i> + 1 )
    repeat <array_size>
    add_cas_menu_item text = w"DONE" id = p8c_pro_done pad_choose_script = create_edit_skater_menu
    wait 2 gameframes
    if ScreenElementExists id = current_scrolling_menu
        SetScreenElementProps id = current_scrolling_menu reset_window_top
        DoScreenElementMorph id = current_scrolling_menu alpha = 1
    endif
    cas_menu_finish build_pause_parts = 0
endscript

script p8c_pro_body_set
    GetCurrentSkaterProfileIndex
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = cas_body desc_id = <desc_id> }
    GetArraySize $p8c_style_part_names
    <i> = 0
    begin
        create_character_part_id part_name = ( $p8c_style_part_names [ <i> ] ) character_type = <character_type>
        EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = <part_id> desc_id = None }
        <i> = ( <i> + 1 )
    repeat <array_size>
    EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = CAS_Shoes desc_id = None }
    RefreshSkaterModel Skater = 0 profile = <currentSkaterProfileIndex>
    SoundEvent event = Generic_Menu_Pad_Choose_SFX
endscript

// back to the style's own body: its first default look (what DEFAULT LOOK 1 sets)
script p8c_pro_body_clear
    GetCurrentSkaterProfileIndex
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    switch <character_type>
        case CAS_FEM
            <look> = appearance_custom_skater_FEM_01
        case CAS_KID
            <look> = appearance_custom_skater_KID_01
        case CAS_SURF
            <look> = appearance_custom_skater_SURF_01
        case CAS_PUNK
            <look> = appearance_custom_skater_PUNK_01
        default
            <look> = appearance_custom_skater_URBN_01
    endswitch
    SetPlayerAppearance player = <currentSkaterProfileIndex> appearance_structure = <look>
    RefreshSkaterModel Skater = 0 profile = <currentSkaterProfileIndex>
    SoundEvent event = Generic_Menu_Pad_Choose_SFX
endscript

// ---------------------------------------------------------------------------------------------------------
// A saved skater wearing an item whose mod is gone: the slot shows "Unknown" and draws nothing. When the
// CUSTOMIZE list is built, such parts go back to the slot's None item (with the game's own follow-up rules).
// ---------------------------------------------------------------------------------------------------------
script p8c_w_create_edit_skater_menu
    if ( $p8c_on = 1 )
        p8c_fix_missing_items
    endif
    p8c_orig_create_edit_skater_menu <...>
endscript

script p8c_fix_missing_items
    GetCurrentSkaterProfileIndex
    GetSingleSkaterProfileInfo player = <currentSkaterProfileIndex> character_type
    <changed> = 0
    GetArraySize $p8c_style_part_names
    <i> = 0
    begin
        create_character_part_id part_name = ( $p8c_style_part_names [ <i> ] ) character_type = <character_type>
        p8c_fix_part part = <part_id>
        if ( <fixed> = 1 )
            <changed> = 1
        endif
        <i> = ( <i> + 1 )
    repeat <array_size>
    p8c_fix_part part = CAS_Shoes
    if ( <fixed> = 1 )
        <changed> = 1
    endif
    if ( <changed> = 1 )
        RefreshSkaterModel Skater = 0 profile = <currentSkaterProfileIndex>
    endif
endscript

script p8c_fix_part
    GetCurrentSkaterProfileIndex
    if not GetPlayerAppearancePart player = <currentSkaterProfileIndex> part = <part>
        return fixed = 0
    endif
    if not GotParam desc_id
        return fixed = 0
    endif
    <want> = <desc_id>
    if ( <want> = None )
        return fixed = 0
    endif
    GetArraySize <part>
    <k> = 0
    begin
        if ( ( <part> [ <k> ].desc_id ) = <want> )
            return fixed = 0
        endif
        <k> = ( <k> + 1 )
    repeat <array_size>
    EditPlayerAppearance profile = <currentSkaterProfileIndex> target = SetPart targetParams = { part = <part> desc_id = None }
    cas_handle_disqualifications part = <part> desc_id = None
    return fixed = 1
endscript
