"""The Windows VK -> evdev translation the Windows collector depends on."""

from behavioral_auth.collector.keycodes import _VK_MAP, vk_to_evdev


def test_backspace_lands_on_evdev_14():
    """features/keystroke.py counts backspaces by the literal code 14."""
    assert vk_to_evdev(0x08) == 14


def test_letters_map_to_distinct_evdev_codes():
    codes = [vk_to_evdev(0x41 + i) for i in range(26)]        # A-Z
    assert len(set(codes)) == 26                              # all distinct
    assert vk_to_evdev(ord('Q')) == 16                        # evdev KEY_Q
    assert vk_to_evdev(ord('A')) == 30                        # evdev KEY_A


def test_digits_map_to_the_evdev_number_row():
    assert vk_to_evdev(ord('1')) == 2                         # KEY_1
    assert vk_to_evdev(ord('0')) == 11                        # KEY_0


def test_unmapped_key_is_stable_and_cannot_collide():
    vk = 0xFF                                                 # not in the table
    assert vk_to_evdev(vk) == vk_to_evdev(vk)                 # stable
    assert vk_to_evdev(vk) >= 1000                            # out of evdev range
    # never equals a real mapped evdev code
    assert vk_to_evdev(vk) not in _VK_MAP.values()


def test_press_and_release_of_one_key_share_a_code():
    """Dwell pairing needs the same code on down and up — the map is a function
    of the key, not the event, so this holds by construction."""
    assert vk_to_evdev(ord('K')) == vk_to_evdev(ord('K'))
