"""Wave 3's pinned additions (devices/INTERFACES_W3.md), read by the
Wave 0 / Wave 1 seam tests so their pins stay exact for the earlier waves
while Wave 3 grows through the same seams, and by tests/test_w3_seams.py."""

W3_FX_TAGS = {"track", "object", "plane", "light"}
W3_SLOTS = {"MotionItem.anchor"}
W3_VERBS = {"attach", "track_point", "select_object", "pin_to_plane",
            "place_in_world", "remove_world", "add_element", "add_collage",
            "set_stack", "add_hud", "add_breakdown", "add_device_stage",
            "set_brand_kit", "plan_footage", "look_at_world"}
W3_DEPARTMENTS = {"world"}
# One skill per track (INTERFACES_W3.md §9): each joins read_skill's name
# enum automatically, like a department joins load_tools'.
W3_SKILLS = {"tracking", "occlusion", "type-in-world", "elements",
             "layouts", "world", "acquisition"}
W3_SIDECAR_KINDS = {"landmarks.face", "landmarks.hand", "landmarks.pose",
                    "track.points", "camera.motion", "objects.sam2",
                    "planes.h", "plate.clean"}
