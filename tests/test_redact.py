from ci_doctor.config.schema import RedactionConfig
from ci_doctor.core.redact import redact_text


def test_redacts_common_secrets():
    text = (
        "token glpat-ABCDEFGHIJKLMNOPQRSTUVWX\n"
        "clone https://user:hunter2@git.internal/repo.git\n"
        "Authorization: Bearer abcdef0123456789\n"
        "AWS_SECRET=supersecretvalue\n"
    )
    out = redact_text(text)
    assert "glpat-ABCDEFGHIJKLMNOPQRSTUVWX" not in out
    assert "hunter2" not in out
    assert "abcdef0123456789" not in out
    assert "supersecretvalue" not in out
    assert "[REDACTED:" in out
    assert "git.internal" in out  # host preserved, only credentials removed


def test_env_secret_literal_scrubbed():
    env = {"CI_JOB_SECRET": "s3cr3t-value-xyz", "PATH": "/usr/bin"}
    out = redact_text("deploying with s3cr3t-value-xyz now", environ=env)
    assert "s3cr3t-value-xyz" not in out
    assert "[REDACTED:env]" in out


def test_disabled_is_passthrough():
    cfg = RedactionConfig(enabled=False)
    assert redact_text("glpat-ABCDEFGHIJKLMNOPQRSTUVWX", cfg) == "glpat-ABCDEFGHIJKLMNOPQRSTUVWX"
