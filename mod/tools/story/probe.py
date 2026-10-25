"""In-game probe of the story runtime's game side (op `story.debug`; a test hook, never ship it).

It checks, without modapi, that the calls the Lua runtime makes do what the runtime expects:
- dialogue boxes with a speaker title and choice buttons;
- cinematic camera shots from compiled keyframes (this also checks the quaternion convention);
- letterbox, subtitles, fades and the bottom hint;
- the rewards.

The QB file `p8story_dbg` holds:
- a command loop. The zone's setup script starts it (one `spawnscriptlater` line appended).
- data compiled from the story: the first dialogue's lines, every cutscene shot's keyframes, the first character.

The harness drives the loop by setting `p8story_dbg_cmd` through the PS3_DEBUG console
(tools/modkit/goals/qbglobals.py `set` / `get`, or dbg.sh qbset):

  cmd  1  dialogue line (speaker title, Continue)        2  choice box (the first choice node)
       3  close the box, unpause                            4  cutscene frame + shot  <arg> (1-based) + subtitle
       5  end the cutscene frame                            6  (unused: talk prompts are peds.talk, Lua)
       7  rewards (1 stat point + the first deck reward)   8  hint box                       9  fade out / 10 fade in
      11  GMan_DebugBeatCheckpoint (win the running goal)   12  GMan_DeactivateAllGoals
      13  put the skater next to the first character (Houses example spot)
Results: p8story_dbg_ack counts executed commands; p8story_dialog_result as in the runtime.
"""
import storyfmt as SF


def qf(x):
    s = repr(round(float(x), 6))
    return s if ('.' in s or 'e' in s) else s + '.0'


def qvec(v):
    return '(' + ', '.join(qf(c) for c in v) + ')'


def qw(s):
    out = []
    for ch in str(s):
        if ch in '"\\':
            out.append('\\' + ch)
        elif ch == '\n':
            out.append('\\n')
        elif ord(ch) < 32 or ord(ch) > 126:
            out.append('\\u%04x' % ord(ch))
        else:
            out.append(ch)
    return 'w"' + ''.join(out) + '"'


def keyframes_text(keys):
    items = []
    for k in keys:
        s = f'{{ LockTo = world Pos = {qvec(k["Pos"])} Quat = {qvec(k["Quat"])} FOV = {qf(k["FOV"])}'
        if 'time' in k:
            s += f' time = {qf(k["time"])}'
        items.append(s + ' }')
    return '[ ' + ', '.join(items) + ' ]'


def probe_text(data):
    """QB text of p8story_dbg for a compiled story."""
    chars = data['characters']
    first_char = next(iter(chars), None)
    ch = chars.get(first_char) or {'name': 'Someone', 'color': [128, 128, 128, 255]}
    ped = ch.get('ped') or (f'p8s_{first_char}' if first_char else 'Skater')
    col = ch.get('color', [128, 128, 128, 255])
    title_rgba = '[ ' + ' '.join(str(int(round(c * 128 / 255))) for c in col[:3]) + ' 255 ]'
    # first dialogue: first line + first choice
    line, choice = None, None
    for d in data['dialogues'].values():
        for n in d['nodes'].values():
            if n['kind'] == 'line' and line is None:
                line = n
            if n['kind'] == 'choice' and choice is None:
                choice = n
    line = line or {'speaker': first_char, 'text': 'Hello!'}
    choice_buttons = ', '.join(
        f'{{ text = {qw(c["text"])} pad_choose_script = p8story_choose pad_choose_params = {{ idx = {i + 1} }} }}'
        for i, c in enumerate((choice or {'choices': [{'text': 'Yes'}, {'text': 'No'}]})['choices']))
    shots = [s for cs in data['cutscenes'].values() for s in cs['shots']]
    subs = [e for cs in data['cutscenes'].values() for e in cs['events'] if e['e'] in ('text', 'line')]
    sub = subs[0]['text'] if subs else 'Subtitle test.'
    if subs and subs[0]['e'] == 'line' and subs[0].get('speaker') in chars:
        sub = f'({chars[subs[0]["speaker"]]["name"]}) {sub}'
    deck = None
    for q in data['quests'].values():
        for a in q['on_complete']:
            if a.get('a') == 'reward' and a.get('decks'):
                deck = a['decks'][0]
    lines = ['// p8story_dbg.qb -- story.debug test hooks (tools/story/probe.py). Never ship.', '',
             'p8story_dbg_cmd = 0', '', 'p8story_dbg_arg = 0', '', 'p8story_dbg_ack = 0', '',
             'p8story_dbg_running = 0', '',
             f'p8story_dbg_title = {qw(ch["name"].upper())}', '',
             f'p8story_dbg_line = {qw(line["text"])}', '',
             f'p8story_dbg_choice_text = {qw((choice or {}).get("text") or line["text"])}', '',
             f'p8story_dbg_sub = {qw(sub)}', '']
    for i, s in enumerate(shots):
        lines += [f'p8story_dbg_shot{i + 1} = {keyframes_text(s["keys"])}', '']
    if not shots:
        lines += ['p8story_dbg_shot1 = [ { LockTo = world Pos = (0.0, 2.0, 0.0) Quat = (0.0, 0.0, 0.0) FOV = 72.0 } ]',
                  '']
    lines += [
        'script p8story_dbg_start',
        '    if ( $p8story_dbg_running = 0 )',
        '        change p8story_dbg_running = 1',
        '        spawnscriptnow p8story_dbg_loop',
        '    endif',
        'endscript', '',
        'script p8story_dbg_loop',
        '    begin',
        '        if ( $p8story_dbg_cmd > 0 )',
        '            <cmd> = $p8story_dbg_cmd',
        '            <arg> = $p8story_dbg_arg',
        '            change p8story_dbg_cmd = 0',
        '            p8story_dbg_run cmd = <cmd> arg = <arg>',
        '            change p8story_dbg_ack = ( $p8story_dbg_ack + 1 )',
        '        endif',
        '        wait 1 gameframe',
        '    repeat',
        'endscript', '',
        'script p8story_dbg_run',
        '    switch <cmd>',
        '        case 1',
        '            change p8story_dialog_result = -1',
        '            kill_start_key_binding',
        '            pauseskaters',
        '            create_dialog_box { title = $p8story_dbg_title text = $p8story_dbg_line buttons = [ { text = '
        'w"\\\\m0 Continue" pad_choose_script = p8story_choose pad_choose_params = { idx = 1 } } ] no_helper_text '
        f'title_rgba = {title_rgba} }}',
        '        case 2',
        '            change p8story_dialog_result = -1',
        '            kill_start_key_binding',
        '            pauseskaters',
        '            create_dialog_box { title = $p8story_dbg_title text = $p8story_dbg_choice_text buttons = [ '
        f'{choice_buttons} ] no_helper_text title_rgba = {title_rgba} }}',
        '        case 3',
        '            dialog_box_exit no_pad_start',
        '            unpauseskaters',
        '            restore_start_key_binding',
        '        case 4',
        '            kill_start_key_binding',
        '            freeze_skater_for_cinematic',
        '            hide_all_hud_items',
        '            fake_letterboxing',
        '            KillSkaterCamAnim Name = p8story_cam',
    ]
    for i in range(max(1, len(shots))):
        lines += [f'            if ( <arg> = {i + 1} )',
                  f'                PlayIGCCam {{ Name = p8story_cam ControlScript = p8story_cam_control params = {{ '
                  f'keyframes = $p8story_dbg_shot{i + 1} motion = smooth }} play_hold = 1 }}',
                  '            endif']
    lines += [
        '            cutscene_subtitle_create text = $p8story_dbg_sub duration = 4.0',
        '        case 5',
        '            KillSkaterCamAnim Name = p8story_cam',
        '            cutscene_subtitle_cleanup',
        '            fake_letterboxing remove',
        '            show_all_hud_items',
        '            unfreeze_skater_after_cinematic',
        '            restore_start_key_binding',
        '        case 7',
        '            AwardStatPoint 1',
    ]
    if deck:
        lines += [f'            p8story_unlock_deck deck = {deck}']
    lines += [
        '        case 8',
        '            create_speech_box text = $p8story_dbg_title anchor_id = p8story_hint no_pad_choose no_pad_start '
        'z_priority = 5 Pos = (320.0, 70.0)',
        '        case 9',
        '            fadetoblack On time = 0.5 alpha = 1.0',
        '        case 10',
        '            fadetoblack off time = 0.5 no_wait',
        '        case 11',
        '            GMan_DebugBeatCheckpoint',
        '        case 12',
        '            GMan_DeactivateAllGoals',
        '        case 13',
        '            goal_move_skater restart_pos = { Pos = (-62.3, 2.39, -66.0) Quat = (0.0, 0.382683, 0.0) }',
        '    endswitch',
        'endscript', '']
    return '\n'.join(lines)


def hook_setup_script(text, setup_name):
    """Append `p8story_dbg_start` to the zone's setup script (qbc text of the zone's scripts file)."""
    lines = text.split('\n')
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith('script ') and ln.split()[1].lower() == setup_name.lower():
            j = i + 1
            while lines[j].strip() != 'endscript':
                j += 1
            lines.insert(j, '    spawnscriptlater p8story_dbg_start')
            return '\n'.join(lines)
    raise KeyError(setup_name)


__all__ = ['probe_text', 'hook_setup_script', 'SF']
