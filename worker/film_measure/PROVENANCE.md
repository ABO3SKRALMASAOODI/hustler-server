The signal implementation in delta.py is copied from the owner's fixed
week-1106/rater/edit_delta.py (15 November 2026), without its command-line
interface. Frame classification comes from taste_ceil.py; subjective
ceilings and the superseded density allowances are deliberately excluded.

The production image uses NumPy/OpenCV equivalents of the original SciPy
filters, connected components and FFT. test_first_cut_floor.py compares
their results with SciPy. FFmpeg executables resolve from the worker's PATH.

The caller runs measurements in a bounded subprocess, removes its local
preview and report afterwards, and retains evidence in first_cut_phases.
A measurement failure is unavailable evidence, never a claimed pass.
