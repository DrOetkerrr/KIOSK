from falklandv3.core.weather import WeatherSimulator


def test_weather_simulator_tick_produces_state():
    sim = WeatherSimulator()
    before = sim.snapshot()
    prev = (before.wind_dir_deg, before.wind_speed_kts, before.sea_state)
    sim.tick(30.0)
    state_after = sim.snapshot()
    assert 0.0 <= state_after.wind_dir_deg < 360.0
    assert state_after.wind_speed_kts >= 0.0
    assert state_after.sea_state >= 0.0
    after_tuple = (state_after.wind_dir_deg, state_after.wind_speed_kts, state_after.sea_state)
    assert after_tuple != prev
