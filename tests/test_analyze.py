import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.analyze import build_bundle
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from tests import support

CASE = "script_failure_noisy"


@pytest.mark.parametrize("provider", support.providers_with(CASE))
def test_build_bundle_excludes_fetch_noise_but_flags_it(provider):
    log = support.read_log(provider, CASE)
    job = Job(id="1", name="build", status="failed",
              failure_reason=FailureReason.SCRIPT_FAILURE, log=log)
    cfg = load_config(environ={})
    job.sections = support.segment(provider, log)
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)

    bundle = build_bundle(job, attr, job.sections, cfg)
    assert bundle.blamed_phase == "script"
    assert bundle.token_estimate > 0

    joined = "\n".join(bundle.blamed_lines)
    assert "assert 1 == 2" in joined                 # the real cause is present
    assert "Failed to extract cache" not in joined    # the loud fetch noise is not
    assert any("fetch" in s for s in bundle.secondary)  # but flagged as non-causal context
