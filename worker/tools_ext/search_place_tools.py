"""One-call placement of persistent search results."""
from mcp_search import place, place_scenes

DEPARTMENT = 'picture'
TOOL_SPECS = {
    'place_search_result': (place,
        'Download and place an exact saved search_stock/research_broll result in Valmera in one call. '
        'Inspect its returned pictures. Supply output start and duration in seconds; source_start_s '
        'selects a window within the downloaded clip. mode=overlay (default) preserves narration '
        'under a full-frame cutaway; insert adds time to the program. This writes the timeline, '
        'not just a downloadable asset. Continue to a complete preview and export_final.',
        {'id': {'type': 'string'}, 'at_output_s': {'type': 'number', 'minimum': 0},
         'duration_s': {'type': 'number', 'minimum': .2},
         'source_start_s': {'type': 'number', 'minimum': 0},
         'mode': {'type': 'string', 'enum': ['overlay', 'insert']}}),
    'place_scenes': (place_scenes,
        'Build a narrated film\'s whole picture in ONE call: the path from a narration (an '
        'uploaded voice-over) to a first cut. After get_transcript and your searches (search_stock, '
        'research_broll, find_footage + fetch_url), pass every chosen picture with the narration '
        'seconds it covers: scenes=[{id: saved search result, or asset_key: an upload, start_s, '
        'end_s, source_start_s?, motion?}], covering the whole narration. With no main video the '
        'pictures become the sequence (each ends at its end_s; a scene that cannot land is covered '
        'by the next) and narration_asset_key is laid under it; over a main video each scene is a '
        'full-frame cutaway. Reports what landed, what did not and why, and narration left without '
        'picture. Then render_preview(complete=true) and export_final.',
        {'scenes': {'type': 'array', 'minItems': 1, 'maxItems': 400,
                    'items': {'type': 'object', 'properties': {
                        'id': {'type': 'string'}, 'asset_key': {'type': 'string'},
                        'start_s': {'type': 'number', 'minimum': 0},
                        'end_s': {'type': 'number', 'minimum': 0},
                        'source_start_s': {'type': 'number', 'minimum': 0},
                        'motion': {'type': 'string',
                                   'enum': ['zoom_in', 'zoom_out', 'pan_left', 'pan_right']}},
                        'required': ['start_s', 'end_s']}},
         'narration_asset_key': {'type': 'string'}}),
}
REQUIRED_ARGS = {'place_search_result': ['id', 'at_output_s', 'duration_s'],
                 'place_scenes': ['scenes']}
WRITE_TOOLS = {'place_search_result', 'place_scenes'}
