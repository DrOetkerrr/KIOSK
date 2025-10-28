from falklandv3.core.weapons import WeaponInventory


def test_weapon_inventory_defaults_include_specs():
    inventory = WeaponInventory()
    slots = inventory.slots()
    assert "MM38 Exocet" in slots
    exocet = slots["MM38 Exocet"]
    assert exocet.max_ammo == 4
    assert exocet.min_range_nm == 8.0
    assert exocet.max_range_nm == 22.0


def test_weapon_inventory_arm_and_safe():
    inventory = WeaponInventory()
    name = inventory.arm("sea dart fwd")
    assert name == "Sea Dart Fwd"
    assert inventory.slots()["Sea Dart Fwd"].state == "Armed"

    inventory.safe("Sea Dart Fwd")
    assert inventory.slots()["Sea Dart Fwd"].state == "Safe"


def test_weapon_fire_consumes_ammo():
    inventory = WeaponInventory()
    slot_before = inventory.slots()["20mm Oerlikon"]
    starting_ammo = slot_before.ammo
    assert inventory.fire("20mm Oerlikon") is True
    slot_after = inventory.slots()["20mm Oerlikon"]
    assert slot_after.ammo == starting_ammo - slot_before.ammo_per_shot
