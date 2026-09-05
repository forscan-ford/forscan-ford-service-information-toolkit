from pathlib import Path


def test_tso_jobs_defaults_to_auto_without_overwriting_override():
    script = (Path(__file__).parent.parent / "tso_convert.bat").read_text()
    assert 'set "JOBS=0"' in script
    assert 'if defined TSO_JOBS set "JOBS=%TSO_JOBS%"' in script
    assert 'set "TSO_JOBS=0"' not in script
