from falklandv3.core.engine import Engine


def test_tick_moves_ship_when_underway():
    engine = Engine()
    engine.set_course(90.0)
    engine.set_speed(12.0)
    start_x, start_y = engine.ship.x_nm, engine.ship.y_nm

    engine.tick(300.0)  # 5 minutes

    assert engine.ship.x_nm > start_x  # moved east
    assert engine.ship.y_nm == start_y
    assert engine.ship.speed_kts == 12.0
