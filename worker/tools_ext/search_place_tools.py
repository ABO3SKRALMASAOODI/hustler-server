"""One-call placement of persistent search results."""
from mcp_search import place

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
}
REQUIRED_ARGS = {'place_search_result': ['id', 'at_output_s', 'duration_s']}
WRITE_TOOLS = {'place_search_result'}
