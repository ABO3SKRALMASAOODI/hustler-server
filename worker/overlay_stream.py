"""Stream long opaque cutaway assemblies through one decoder.

Twenty-four windows of one 4K file are twenty-four independent decoders,
not one shared source. Prepare the bottom opaque cover layer sequentially;
the normal graph still owns all other layers, sound, captions and branding.
"""
import math
import os
import shutil
import tempfile

import media


def _clock(value):
    # The ordinary overlay graph emits its authored bounds to milliseconds.
    return float(f'{float(value):.3f}')


def plan(items, fps):
    prefix = []
    for item in items:
        if (item.get('kind') != 'video' or item.get('fit') != 'cover'
                or item.get('opacity') not in (None, 1, 1.0)
                or any(item.get(k) for k in ('entrance', 'exit', 'rotation', 'screen'))):
            break
        prefix.append(item)
    if len(prefix) < 8:
        return []
    # Work on the output frame grid. Last layer wins at rounded overlaps,
    # just as it does in the ordinary ordered overlay chain.
    spans = [(math.ceil(_clock(x['start']) * fps - 1e-7),
              math.ceil(_clock(float(x['start']) + float(x['duration_s'])) * fps - 1e-7))
             for x in prefix]
    edges = sorted({0, *(n for pair in spans for n in pair)})
    pieces = []
    for a, b in zip(edges, edges[1:]):
        owner = next((i for i in range(len(spans)-1, -1, -1)
                      if spans[i][0] <= a < spans[i][1]), None)
        if pieces and pieces[-1][2] == owner:
            pieces[-1] = (pieces[-1][0], b, owner)
        else:
            pieces.append((a, b, owner))
    return prefix, pieces


def prepare(items, edl, width, height, fps, workdir, fetch, input_args,
            look_chain, cancelled_cb=None):
    planned = plan(items, fps)
    if not planned:
        return None
    prefix, pieces = planned
    folder = tempfile.mkdtemp(prefix='cutaway_stream_', dir=workdir)
    files = []
    # Keep the original enable bounds. A frame-grid timestamp rounded to
    # decimal text can be a fraction of a nanosecond later than that frame
    # and incorrectly expose the base picture for one frame after a gap.
    windows = [(_clock(x['start']), _clock(float(x['start']) + float(x['duration_s'])))
               for x in prefix]
    try:
        for n, (a, b, owner) in enumerate(pieces):
            duration = (b-a)/fps
            chain = []
            if owner is None:
                args = ['-f', 'lavfi', '-i',
                        f'color=black:s={width}x{height}:r={fps}:d={duration:.9f}']
            else:
                item = prefix[owner]
                args, rebased = input_args(item, fetch(item['asset_key']), fps)
                off = float(rebased.get('source_start_s') or 0)
                dur = float(item['duration_s'])
                if item.get('rate') is not None:
                    rate = float(item['rate'])
                    chain = [f'setpts=(PTS-{off:.6f}/TB)/{rate:.12f}',
                             f'fps={fps}:round=up:start_time=0',
                             f'trim=duration={dur:.6f}',
                             'tpad=stop_mode=clone:stop_duration=0.15']
                else:
                    chain = [f'trim=start={off:.3f}:end={off+dur+.15:.3f}',
                             'tpad=stop_mode=clone:stop_duration=0.15', 'setpts=PTS-STARTPTS']
                if item.get('source_crop'):
                    l, t, r, d = item['source_crop']
                    chain += [f"crop=w='trunc(iw*{r-l:.6f}/2)*2':h='trunc(ih*{d-t:.6f}/2)*2':"
                              f"x='trunc(iw*{l:.6f}/2)*2':y='trunc(ih*{t:.6f}/2)*2'"]
                chain += [f'scale={width}:{height}:force_original_aspect_ratio=increase',
                          f'crop={width}:{height}', 'format=yuv420p']
                chain += look_chain(edl, float(item['start']), dur)
                # Grade/texture clocks above remain relative to the authored
                # shot, including a shot partially hidden by a later layer.
                elapsed = a/fps - _clock(item['start'])
                chain += [f'setpts=PTS-{elapsed:.9f}/TB']
            # Overlay framesync holds the preceding source frame. Nearest
            # rounding can advance a VFR source across an internal cut.
            chain += [f'fps={fps}:round=up:start_time=0', f'trim=end_frame={b-a}',
                      'setpts=N/FRAME_RATE/TB', 'setsar=1', 'format=yuv420p']
            path = os.path.join(folder, f'{n}.nut')
            media.run(['ffmpeg', '-y', '-v', 'error', '-threads:v', '2', *args,
                       '-an', '-vf', ','.join(chain), '-filter_threads', '1',
                       '-frames:v', str(b-a), '-c:v', 'libx264', '-threads:v', '2',
                       '-preset', 'ultrafast', '-qp', '0', path],
                      timeout=180, cancelled_cb=cancelled_cb)
            files.append(path)
        listing = os.path.join(folder, 'concat.txt')
        with open(listing, 'w') as out:
            for path, (a, b, _owner) in zip(files, pieces):
                out.write("file '" + path.replace("'", "'\\''")
                          + f"'\nduration {(b-a)/fps:.9f}\n")
        program = os.path.join(folder, 'program.nut')
        media.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
                   '-i', listing, '-map', '0:v:0', '-c', 'copy', program],
                  timeout=180, cancelled_cb=cancelled_cb)
        for path in files:
            os.remove(path)
        print(f'[render] streamed {len(prefix)} opaque cutaways into one reader', flush=True)
        return program, {'start': 0., 'duration_s': pieces[-1][1]/fps,
                         'kind': 'video', '_cover_windows': windows}, len(prefix), folder
    except BaseException:
        shutil.rmtree(folder)
        raise
