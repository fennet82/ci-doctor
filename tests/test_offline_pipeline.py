"""M5 no-network guarantee: the whole deterministic pipeline runs offline.

conftest blocks all real sockets, so if any stage tried the network this fails.
"""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.analyze import build_bundle
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from ci_doctor.llm.report import produce_report
from ci_doctor.render.json_out import JsonRenderer
from ci_doctor.render.markdown import MarkdownRenderer
from tests import support

CASE = "script_failure_noisy"


@pytest.mark.parametrize("provider", support.providers_with(CASE))
def test_full_offline_pipeline_no_network(provider):
    """Every deterministic stage runs end to end without touching the network."""
    log = support.read_log(provider, CASE)
    job = Job(id="1", name="build", status="failed", failure_reason=FailureReason.SCRIPT_FAILURE, log=log)
    cfg = load_config(environ={})

    job.sections = support.segment(provider, log)
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    bundle = build_bundle(job, attr, job.sections, cfg)
    report = produce_report(job, attr, bundle, cfg)  # llm unconfigured -> deterministic, no network

    md = MarkdownRenderer().render(report)
    js = JsonRenderer().render(report)
    assert report.failure_phase == "script"
    assert "### Root cause" in md
    assert '"failure_phase": "script"' in js
