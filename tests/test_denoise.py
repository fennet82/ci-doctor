from ci_doctor.config.schema import DenoiseConfig
from ci_doctor.core.denoise import denoise, strip_ansi


def _cfg(**kw):
    base = dict(collapse_carriage_returns=True, dedupe_repeats=True, noise_patterns=[r"^\s*$", r"^Progress:"])
    base.update(kw)
    return DenoiseConfig(**base)


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m text") == "red text"


def test_collapse_carriage_returns():
    out = denoise(["downloading 1%\rdownloading 50%\rdownloading done"], _cfg())
    assert out == ["downloading done"]


def test_dedupe_consecutive_with_count():
    out = denoise(["same", "same", "same", "other"], _cfg())
    assert out == ["same  (×3)", "other"]


def test_noise_dropped_but_anchor_kept():
    lines = ["Progress: 10%", "Progress: 20%", "ERROR: real boom", "Progress: 30%"]
    out = denoise(lines, _cfg())
    assert "ERROR: real boom" in out
    assert not any(x.startswith("Progress:") for x in out)


def test_anchor_matching_noise_pattern_is_not_dropped():
    # A line matching a noise pattern but also looking like an error must survive.
    out = denoise(["Progress: ERROR something failed"], _cfg(noise_patterns=[r"^Progress:"]))
    assert out == ["Progress: ERROR something failed"]


def test_cuts_noisy_log_over_70pct_and_retains_anchor():
    # Synthesize a ~50k-line noisy log: repeated waiting spam + \r progress + a real error.
    lines = ["Waiting for pod to be scheduled..."] * 40000
    lines += [f"pulling layer deadbeef {p}%\rpulling layer deadbeef done" for p in range(9000)]
    lines += ["$ pytest -q", "E   assert 1 == 2", "ERROR: Job failed: exit code 1"]
    before = len(lines)

    out = denoise(lines, _cfg(noise_patterns=[r"^Waiting for pod"]))

    reduction = 1 - len(out) / before
    assert reduction > 0.70, f"only cut {reduction:.0%}"
    assert "ERROR: Job failed: exit code 1" in out  # anchor retained
    assert "E   assert 1 == 2" in out
