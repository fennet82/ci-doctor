"""The CI system and the git host are chosen independently.

Jenkins builds GitLab repos and Woodpecker builds Forgejo ones, so `ci` picks who
ran the pipeline and `scm` picks who receives the note. These tests pin the wiring:
a vendor that is both roles connects once, and a role with no adapter degrades to
"no note" rather than an error.
"""

from types import SimpleNamespace

import pytest

from ci_doctor import cli
from ci_doctor.config.loader import load_config
from ci_doctor.config.schema import Config
from ci_doctor.core.models import SYNTHETIC_SECTIONS
from ci_doctor.core.ports import CIProvider, SCMProvider
from ci_doctor.pipeline import JobResult
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.github.segmenter import GitHubSegmenter
from ci_doctor.providers.gitlab.provider import GitLabProvider
from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter
from ci_doctor.providers.registry import (
    make_ci_provider,
    make_scm_provider,
    make_segmenter,
    segmenter_for,
    segmenter_for_log,
)
from tests import support


def _gitlab_provider():
    """A GitLabProvider on an injected client, so nothing touches the network."""
    return GitLabProvider(load_config(environ={}), client=SimpleNamespace(), environ={"CI_PROJECT_ID": "1"})


def test_bundled_vendor_implements_both_ports():
    """GitLab and GitHub answer as CI system and as git host."""
    for adapter in (GitLabProvider, GitHubProvider):
        assert issubclass(adapter, CIProvider) and issubclass(adapter, SCMProvider)


def test_same_vendor_serves_both_roles_over_one_client():
    """Pipelines and merge requests are one API: reuse the connection, don't rebuild it.

    A second instance would mean a second token read and a second version probe
    for the same endpoint.
    """
    ci_provider = _gitlab_provider()
    assert make_scm_provider(load_config(environ={}), ci_provider) is ci_provider


def test_mixed_setup_builds_a_separate_git_host():
    """`ci: jenkins` + `scm: github` reads nothing from GitHub but posts there."""
    scm = make_scm_provider(Config(ci="jenkins", scm="github"), ci_provider=None)
    assert isinstance(scm, GitHubProvider)


def test_ci_without_an_adapter_is_an_error():
    """An unreadable CI system fails loudly — there is no run to analyze."""
    with pytest.raises(ValueError, match="unsupported CI system: jenkins"):
        make_ci_provider(Config(ci="jenkins"))


@pytest.mark.parametrize("cfg", [Config(ci="gitlab", scm="none"), Config(ci="jenkins")])
def test_no_git_host_is_not_an_error(cfg):
    """No note target degrades to terminal + artifacts, never to a failure.

    `scm: none` is the explicit opt-out; `ci: jenkins` with no `scm` is the user
    who simply hasn't said where their code lives.
    """
    assert make_scm_provider(cfg, ci_provider=None) is None


def test_note_is_skipped_when_there_is_no_git_host(capsys):
    """The delivery step says why it skipped instead of raising."""
    report = SimpleNamespace(confidence="high")
    run = SimpleNamespace(id="1", mr=SimpleNamespace(iid="7"))
    cfg = Config(ci="jenkins")
    cfg.output.mr_note = True

    cli._maybe_post_mr(None, run, [JobResult(None, None, report)], cfg)

    assert "no git-host adapter for 'jenkins'" in capsys.readouterr().err


def test_segmenter_follows_the_ci_not_the_git_host():
    """Log framing is whatever the runner printed, so `scm` must not change it."""
    assert isinstance(make_segmenter(Config(ci="github", scm="gitlab")), GitHubSegmenter)


@pytest.mark.parametrize("provider", support.providers())
def test_replay_reads_the_log_not_the_config(provider):
    """Offline replay picks the segmenter from the log's own framing.

    A replayed file carries no run metadata, so `ci` is whatever the config
    defaults to — a value the user never chose. Reading a GitLab trace with
    GitHub's segmenter is not a near miss: no marker matches, every line lands in
    the synthetic preamble, and attribution answers "no signal". The fallback
    here is deliberately a CI that does not exist, so only the log can decide.
    """
    raw = support.read_log(provider, "sample")
    chosen = segmenter_for_log(raw, fallback="nonexistent-ci")
    assert type(chosen) is type(segmenter_for(provider))

    real = [s.name for s in chosen.segment(raw) if s.name not in SYNTHETIC_SECTIONS]
    assert real, "the log parsed into nothing but preamble/trailer — wrong segmenter"


def test_an_unrecognisable_log_falls_back_to_the_configured_ci():
    """Nothing in the log identifies a runner, so `ci` is the tie-breaker."""
    assert isinstance(segmenter_for_log("just some output\n", fallback="github"), GitHubSegmenter)
    assert isinstance(segmenter_for_log("just some output\n", fallback="gitlab"), GitLabSegmenter)
