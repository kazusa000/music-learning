from test_studio_browser import run_studio_browser


def test_tempo_meter_and_playing_browser(tmp_path):
    run_studio_browser(tmp_path, "project-timing.mjs", "timing-ui")
